"""実体どうしの関係を解決する。フレームワークには依存しない。

入口（画面・API・ジョブ・イベント処理…）ごとに、定義されたコード片から始めて
import を 3 段までたどり、各アダプタの `usages` が拾ったもの（テーブル操作・API 呼び出し・
画面遷移・外部連携・イベント発行・キュー投入）を、検出済みの実体に結び付ける。

関係には状態を付ける（`core.status`）。

- confirmed  入口のコード片そのもの（深さ 0）で使っていて、行き先が 1 つに決まる
- inferred   import をたどった先（深さ 1 以上）で使っている、または規約・パス名から結び付けた
- unresolved 行き先の候補が複数ある / 行き先が検出結果に無い。**本文用の関係には入れず**、
             `pending` に理由と候補を残す。資料の「未確定」と coverage の分母に現れる
"""

import re

from .model import normalize_path
from .status import ACCEPTED, diagnostic

MAX_DEPTH = 3
MAX_FILES = 40

RE_WEBHOOK_SIGNATURE = re.compile(
    r"constructEvent|verifyWebhook|webhooks?\.verify|x-hub-signature|stripe-signature|"
    r"svix|x-line-signature|x-slack-signature|validateRequest|verifySignature", re.I)


class Lookup:
    """usage から実体を引くための索引。"""

    def __init__(self, b):
        self.b = b
        self.endpoints = {}
        for e in b.items("endpoints"):
            if e["protocol"] == "http":
                self.endpoints.setdefault(normalize_path(e["path"]), []).append(e)
        self.screens = {}
        for s in b.items("screens"):
            self.screens.setdefault(normalize_path(s["route"]), []).append(s)
        self.consumers = {}
        for j in b.items("jobs"):
            if j["kind"] == "queue-consumer" and j.get("queue"):
                self.consumers.setdefault(j["queue"], []).append(j)

    def tables(self, alias, app_id, repo_key):
        """(決まったテーブルの列, 決めきれなかったときの候補の列)。"""
        uids = self.b.table_aliases.get(alias, set())
        items = sorted((self.b.get(u) for u in uids if self.b.get(u)), key=lambda t: t["_uid"])
        if not items:
            return [], []
        same_app = [t for t in items if t["app"] == app_id]
        if same_app:
            return (same_app, []) if len(same_app) == 1 else ([], same_app)
        same_repo = [t for t in items if t["app"] and t["app"].startswith(f"app:{repo_key}")]
        pool = same_repo or items
        return (pool, []) if len(pool) == 1 else ([], pool)

    def endpoint(self, method, path, method_source):
        cands = self.endpoints.get(normalize_path(path), [])
        exact = [e for e in cands if e["method"] == method]
        if exact:
            return exact
        anys = [e for e in cands if e["method"] == "ANY"]
        if anys:
            return anys
        # メソッドを書いていない fetch は GET とみなしたが、その path に 1 つしか無ければそれ
        if method_source == "default" and len(cands) == 1:
            return cands
        return []

    def screen(self, route):
        return self.screens.get(normalize_path(route), [])

    def screen_pattern(self, route):
        """完全一致が無いとき、動的なルート（`/blog/:slug`・`/*slug`）で受けている画面。

        フレームワークの優先順位と同じく、決まった文字の多いものを優先する
        （固定 > 動的セグメント > キャッチオール）。最も具体的なものが 1 つに決まらなければ候補として返す。
        """
        segs = [s for s in route.split("?")[0].split("#")[0].split("/") if s]
        scored = []
        for items in self.screens.values():
            for s in items:
                score = _route_match(s["route"], segs)
                if score is not None:
                    scored.append((score, s))
        if not scored:
            return [], []
        best = min(score for score, _ in scored)
        top = [s for score, s in scored if score == best]
        return (top, []) if len(top) == 1 else ([], top)


def _route_match(pattern, segs):
    """route のパターンが segs に合えば (キャッチオールの数, 動的セグメントの数)。合わなければ None。"""
    pats = [p for p in pattern.split("/") if p]
    dynamic = 0
    for i, p in enumerate(pats):
        if p.startswith("*"):
            optional = p.endswith("?")
            rest = segs[i:]
            if not rest and not optional:
                return None
            return (1, dynamic)
        if i >= len(segs):
            return None
        if p.startswith((":", "[", "{", "<")):
            dynamic += 1
        elif p != segs[i]:
            return None
    return (0, dynamic) if len(pats) == len(segs) and dynamic else None


def resolve(b, apps, adapters_by_app):
    lookup = Lookup(b)
    app_by_id = {a.id: a for a in apps}
    _mark_webhooks(b, app_by_id)
    for item in list(b.all_entities()):
        loc = item.get("_location")
        app = app_by_id.get(item["app"])
        if app is None:
            continue
        if loc and loc.get("file"):
            _resolve_item(b, lookup, app, adapters_by_app[app.id], item, loc)
        trigger = item.get("_trigger")
        if trigger and trigger["kind"] == "call":
            ev = item["evidence"][0] if item["evidence"] else None
            found = lookup.endpoint(trigger["method"], trigger["path"], "trigger")
            for ep in found:
                b.relate(item, "calls", ep["_uid"], ev)
            if not found:
                b.add_pending(item, "calls", f"{trigger['method']} {trigger['path']}", "unresolved",
                              "設定に書かれた呼び出し先が、検出した API に無い", ev)
        if item.get("_sql"):
            from ..detectors.sql import sql_table_usages
            for u in sql_table_usages(item["_sql"]):
                found, candidates = lookup.tables(u["alias"], app.id, app.repo.key)
                for t in found:
                    b.relate(item, "tables", t["_uid"], item["evidence"][0], operation=u["op"])
                if candidates:
                    b.add_pending(item, "tables", u["alias"], "unresolved",
                                  "同じ名前のテーブルが複数あり、どれを指すか決められない", item["evidence"][0],
                                  candidates=[t["_uid"] for t in candidates])
        if item["_collection"] == "entrypoints" and item["type"] == "db-trigger":
            b.mark_unresolved(item, "トリガ関数の処理内容は解析の対象外")
    _link_webhook_senders(b)


# トップレベルの定義（JS / TS の function・const・class、Python の def・class）
RE_TOP_DEF = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function\*?\s+([\w$]+)|(?:const|let|var|class)\s+([\w$]+)|def\s+(\w+))", re.M)


# 行頭から始まる文（閉じ括弧で始まる行は前の定義の続き）
RE_TOP_STATEMENT = re.compile(r"^(?![\s}\])])\S", re.M)


def definitions(text):
    """{名前: (開始, 終了)}。定義の次のトップレベルの文の手前までを 1 つの定義とみなす。

    `const app = new Hono()` のあとに続く `app.get(...)` を定義に含めないため、
    次の定義ではなく次の文で切る。
    """
    out = {}
    for m in RE_TOP_DEF.finditer(text):
        name = m.group(1) or m.group(2) or m.group(3)
        nxt = RE_TOP_STATEMENT.search(text, text.find("\n", m.start()) + 1 if "\n" in text[m.start():] else len(text))
        end = nxt.start() if nxt else len(text)
        out.setdefault(name, (m.start(), end))
    return out


def _uses(body, name):
    return re.search(r"(?<![\w$.])" + re.escape(name) + r"\b", body) is not None


RE_FUNCTION_DEF = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\b|def\b|"
    r"(?:const|let|var)\s+[\w$]+\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[\w$]+\s*=>))")


def _should_follow(body, name, definition, depth=1):
    """その定義を辿るか。

    関数は参照されていれば辿る（`router.get('/', list)` のように関数を渡す書き方がある）。
    オブジェクトやインスタンスは、呼び出し・メンバー参照・JSX・`new` で使われたときだけ辿る。
    `getServerSession(authOptions)` のように設定を渡しているだけの箇所から、
    設定の中の OAuth やメール送信を、その画面の連携として拾わないため。
    ただしモジュールの最上位で設定を渡して入口そのものを作っている場合（`export default
    NextAuth(authOptions)` / `const handler = NextAuth(authOptions)`）は辿る。その設定の中身は
    入口の動作だから。関数の中で渡しているだけ（`getServerSession(authOptions)`）なら辿らない。
    """
    if definition is None or RE_FUNCTION_DEF.match(definition):
        return _uses(body, name)
    n = re.escape(name)
    if depth == 0 and re.search(
            r"^(?:export\s+default\s+|(?:export\s+)?(?:const|let|var)\s+[\w$]+\s*=\s*)"
            r"[\w$.]+\([^)]*(?<![\w$.])" + n + r"\b", body, re.M):
        return True
    return re.search(r"(?<![\w$.])" + n + r"\s*(?:\(|\.|`)|<" + n + r"\b|\bnew\s+" + n + r"\b", body) is not None


def _walk(app, adapters, loc):
    """入口のコード片から始めて、使っている定義をたどる。

    別ファイルは import した名前の定義だけを読む（ファイル全体を読むと、使っていない関数の
    テーブル操作や API 呼び出しまで混ざる）。定義が見つからなければファイル全体を読む。
    同じファイルのトップレベル定義を参照していれば、それも読む。
    返すのは (ファイル, 本文, 深さ, 本文の開始位置) の列。
    """
    rel = loc["file"]
    full = app.read(rel)
    start = loc.get("start") or 0
    end = loc.get("end")
    end = len(full) if end is None else end
    seen = {(rel, start)}
    queue = [(rel, start, end, 0)]
    out = []
    while queue and len(out) < MAX_FILES:
        f, s, e, depth = queue.pop(0)
        whole = app.read(f)
        seg = whole[s:e]
        out.append((f, seg, depth, s))
        if depth >= MAX_DEPTH:
            continue
        body = _without_import_lines(seg)
        imports, aliases = {}, {}
        for a in adapters:
            imports.update(a.imports(app, f, whole))
            aliases.update(a.aliases(app, f, whole))
        for name, target in sorted(imports.items()):
            if name.startswith("*") or not _uses(body, name):
                continue
            text = app.read(target)
            found = definitions(text).get(aliases.get(name, name))
            span = found or (0, len(text))
            if not _should_follow(body, name, text[span[0]:span[1]] if found else None, depth):
                continue
            if (target, span[0]) in seen:
                continue
            seen.add((target, span[0]))
            queue.append((target, span[0], span[1], depth + 1))
        for name, (ds, de) in sorted(definitions(whole).items(), key=lambda kv: kv[1]):
            if s <= ds < e or (f, ds) in seen:
                continue
            # `const handler = NextAuth(authOptions); export { handler as GET }` の handler は入口そのもの。
            # 深さを増やさずにたどる（入口のコード片の続きとして扱う）
            if re.search(r"\bexport\s*\{[^}]*(?<![\w$])" + re.escape(name) + r"\b", body):
                seen.add((f, ds))
                queue.append((f, ds, de, depth))
                continue
            if not _should_follow(body, name, whole[ds:de], depth):
                continue
            seen.add((f, ds))
            queue.append((f, ds, de, depth + 1))
    return out


def _without_import_lines(text):
    return re.sub(r"^\s*(?:import|export\s+\*|from\s+\S+\s+import)\b[^\n]*$", "", text, flags=re.M)


def _resolve_item(b, lookup, app, adapters, item, loc):
    schema_defs, schema_uses = {}, []
    sessions, branches = [], []
    for rel, text, depth, offset in _walk(app, adapters, loc):
        aliases = {}
        whole = app.read(rel)
        for a in adapters:
            aliases.update(a.aliases(app, rel, whole))
        base_line = whole.count("\n", 0, offset)
        # 入口のコード片そのもので見つけたものは confirmed、import をたどった先は inferred
        status = "confirmed" if depth == 0 else "inferred"
        for a in adapters:
            for u in a.usages(app, rel, text):
                line = base_line + text.count("\n", 0, u.get("offset", 0)) + 1
                ev = app.ev(rel, line, f"usage.{u['kind']}")
                if u["kind"] == "table" and u["alias"] in aliases \
                        and not any(lookup.tables(u["alias"], app.id, app.repo.key)):
                    u = dict(u, alias=aliases[u["alias"]])
                if u["kind"] == "schema-def":
                    schema_defs.setdefault(u["name"], dict(u, evidence=ev))
                    continue
                if u["kind"] == "schema-use":
                    schema_uses.append(dict(u, evidence=ev, depth=depth))
                    continue
                if u["kind"] in ("session", "unauth-branch"):
                    (sessions if u["kind"] == "session" else branches).append(dict(u, evidence=ev, depth=depth))
                    continue
                _apply(b, lookup, app, item, u, ev, status)
    if schema_uses and item["_collection"] == "endpoints":
        _attach_request_schema(b, item, schema_defs, schema_uses)
    if item["_collection"] == "endpoints":
        _attach_auth_check(item, sessions, branches)


def _attach_auth_check(item, sessions, branches):
    """未ログインのときの応答を、事実として持つ（predicate: unauthenticated_status）。

    確定させるのは、ログイン状態の値が無いときに返す分岐がコードにあるときだけ。
    入口のコード片の分岐は confirmed、import をたどった先の分岐は inferred。
    """
    def ref(ev):
        return {"file": ev["file"], "line": ev["line"]}
    found = [x for x in branches if x["result"] is not None]
    if found:
        first = min(found, key=lambda x: x["depth"])
        values = sorted({str(x["result"]) for x in found if x["depth"] == first["depth"]})
        if len(values) > 1:
            item["authCheck"] = {"session": True, "unauthenticatedStatus": None, "status": "conflict",
                                 "reason": f"未ログインのときの分岐が複数あり、応答が違う（{', '.join(values)}）",
                                 "evidence": [ref(x["evidence"]) for x in found]}
            return
        item["authCheck"] = {"session": True, "unauthenticatedStatus": first["result"],
                             "status": "confirmed" if first["depth"] == 0 else "inferred",
                             "evidence": [ref(first["evidence"])]}
    elif sessions:
        item["authCheck"] = {"session": True, "unauthenticatedStatus": None, "status": "unresolved",
                             "reason": "ログイン状態を取り出しているが、未ログインのときに返す分岐がコードに無い"
                                       "（取り出した値を条件などに渡しているだけ）",
                             "evidence": [ref(x["evidence"]) for x in sessions[:3]]}
    else:
        item["authCheck"] = {"session": False, "unauthenticatedStatus": None, "status": "unresolved",
                             "reason": "入口からたどった範囲に、ログイン状態を確かめる処理が無い"}


def _attach_request_schema(b, item, defs, uses):
    """実行時の入力検証（zod など）を、API のリクエストの根拠として持つ。

    precedence の `api.request` で最も強い `runtime-schema` にあたる。
    """
    fields, evidence = [], []
    for use in uses:
        d = defs.get(use["name"])
        if not d:
            b.add_pending(item, "request", use["name"], "unresolved",
                          "入力検証のスキーマの定義をたどれなかった", use["evidence"])
            continue
        for f in d["fields"]:
            entry = dict(f)
            if entry["in"] == "auto":
                entry["in"] = use["in"]
            if entry not in fields:
                fields.append(entry)
        evidence.append({"file": d["evidence"]["file"], "line": d["evidence"]["line"]})
        evidence.append({"file": use["evidence"]["file"], "line": use["evidence"]["line"]})
    if fields:
        item["requestSchema"] = {"source": "runtime-schema", "library": "zod", "status": "confirmed",
                                 "fields": fields, "evidence": evidence}


def _apply(b, lookup, app, item, u, ev, status):
    kind = u["kind"]
    if kind == "table":
        found, candidates = lookup.tables(u["alias"], app.id, app.repo.key)
        for t in found:
            if t["_uid"] != item["_uid"]:
                b.relate(item, "tables", t["_uid"], ev, operation=u["op"], status=status)
        if candidates:
            b.add_pending(item, "tables", u["alias"], "unresolved",
                          "同じ名前のテーブルが複数あり、どれを指すか決められない", ev,
                          candidates=[t["_uid"] for t in candidates])
    elif kind == "integration":
        webhook = bool(item.get("webhook"))
        integ = b.add_integration(u["key"], u["name"], ev,
                                  direction="inbound" if webhook else "outbound",
                                  protocol=u.get("protocol"), sdk=u.get("sdk"), host=u.get("host"))
        b.relate(item, "integrations", integ["_uid"], ev, status=status)
    elif kind == "call":
        found = lookup.endpoint(u["method"], u["path"], u.get("methodSource"))
        found = [e for e in found if e["_uid"] != item["_uid"]]
        if not found and not u["path"].startswith("/api/"):
            # `${API_URL}/snaps/upload-image` の API_URL が `.../api` で終わる書き方（Strapi への呼び出しなど）。
            # /api を補うと別のアプリの API に一致するなら、その API を呼ぶと推定する
            prefixed = [e for e in lookup.endpoint(u["method"], "/api" + u["path"], u.get("methodSource"))
                        if e["_uid"] != item["_uid"] and e["app"] != item["app"]]
            if prefixed:
                found, status = prefixed, "inferred"
        if len(found) > 1 and u.get("based") is False:
            # `fetch("/api/snaps")` のようにホストを書かない呼び出しは、同じアプリ（同じオリジン）の API に届く
            same = [e for e in found if e["app"] == item["app"]]
            if len(same) == 1:
                found, status = same, "inferred"
        if len(found) == 1:
            b.relate(item, "calls", found[0]["_uid"], ev, status=status)
        elif found:
            b.add_pending(item, "calls", f"{u['method']} {u['path']}", "unresolved",
                          "同じパスの API が複数のアプリにあり、どれを呼ぶか決められない", ev,
                          candidates=[e["_uid"] for e in found])
        else:
            b.add_pending(item, "calls", f"{u['method']} {u['path']}", "unresolved",
                          "呼び出し先が、検出した API に無い（外部の API か、解析器が読めない書き方）", ev)
    elif kind == "navigate" and item["_collection"] == "screens":
        # precedence の screen.transition: 遷移のコード > 部品に渡した href > ルートの名前からの推測
        if u.get("how") == "prop":
            status = "inferred"
        found = [s for s in lookup.screen(u["route"]) if s["_uid"] != item["_uid"]]
        for s in found:
            b.relate(item, "links", s["_uid"], ev, tag=u.get("how"), status=status)
        if not found and not lookup.screen(u["route"]):
            endpoints = [e for e in lookup.endpoint("GET", u["route"], "default") if e["_uid"] != item["_uid"]]
            for e in endpoints:
                b.relate(item, "calls", e["_uid"], ev, status=status)
            if endpoints:
                return
            # `/terms` を `/*slug` の画面が受けるような場合。パターンで結び付けたので inferred
            matched, candidates = lookup.screen_pattern(u["route"])
            for s in matched:
                if s["_uid"] != item["_uid"]:
                    b.relate(item, "links", s["_uid"], ev, tag=u.get("how"), status="inferred")
            if candidates:
                b.add_pending(item, "links", u["route"], "unresolved",
                              "遷移先を受けられる動的なルートの画面が複数あり、どれか決められない", ev,
                              candidates=[s["_uid"] for s in candidates])
            elif not matched:
                b.add_pending(item, "links", u["route"], "unresolved",
                              "遷移先が、検出した画面に無い", ev)
    elif kind == "publish":
        event = b.add_event_publisher(u["event"], ev)
        b.relate(item, "publishes", event["_uid"], ev, status=status)
    elif kind == "enqueue":
        consumers = lookup.consumers.get(u["queue"], []) if u.get("queue") else []
        for j in consumers:
            if j["_uid"] != item["_uid"]:
                b.relate(item, "enqueues", j["_uid"], ev, status=status)
        if not consumers:
            b.add_pending(item, "enqueues", u.get("queue") or "（名前不明）", "unresolved",
                          "キューの受け手が見つからない", ev)
    elif kind == "internal-host":
        b.add_unsupported({"id": app.id}, ev["file"], "integration",
                          "社内・私有ホストへの呼び出し。資料に載せないためホスト名を記録していない")


def _mark_webhooks(b, app_by_id):
    """外部から呼ばれる受け口に Webhook の印を付ける。関係の解決より前に行う。

    判定は 2 つ。パスに `webhook` を含む、または定義のコード片に署名検証の呼び出しがある。
    """
    for e in b.items("endpoints"):
        if e["protocol"] != "http":
            continue
        signed = False
        loc = e.get("_location")
        app = app_by_id.get(e["app"])
        if loc and app:
            text = app.read(loc["file"])
            end = loc.get("end")
            signed = bool(RE_WEBHOOK_SIGNATURE.search(
                text[loc.get("start") or 0:end if end is not None else len(text)]))
        if signed or re.search(r"webhook", e["path"], re.I):
            e["webhook"] = True


def _link_webhook_senders(b):
    """Webhook の送り手を、パスに出ている名前で結び付ける（`/webhooks/stripe` → Stripe）。"""
    for e in b.items("endpoints"):
        if not e.get("webhook"):
            continue
        segs = [s.lower() for s in e["path"].split("/")]
        for integ in b.items("integrations"):
            if integ["key"] in segs or integ["name"].lower() in segs:
                # パスの名前から結び付けたものは inferred（コードで呼び出しを確かめたわけではない）
                b.relate(e, "integrations", integ["_uid"], e["evidence"][0] if e["evidence"] else None,
                         status="inferred")
        for rel in e.get("_rel", {}).get("integrations", {}):
            integ = b.get(rel)
            if integ and "inbound" not in integ["directions"]:
                integ["directions"].append("inbound")


def apply_env_examples(b, apps):
    """`.env.example` に並ぶ変数名のうち、外部連携の名前で始まるものを候補として残す。

    名前が似ているだけでは確定させない（precedence の `naming`）。unresolved の候補として持ち、
    初期化や呼び出しの引数で使われていれば、そちらの根拠で confirmed に上がる。
    """
    from ..detectors.integrations import env_example_names
    # 引数から別の連携先に結び付いた変数は、名前が似ていても候補にしない
    owned = {e["name"] for i in b.items("integrations") for e in i["envVars"] if e["status"] in ACCEPTED}
    for app in apps:
        for name, rel in sorted(env_example_names(app).items()):
            if name in owned:
                continue
            for integ in b.items("integrations"):
                prefix = re.sub(r"[^A-Z0-9]", "_", re.split(r"[-.:]", integ["key"].split(":")[-1])[0].upper())
                if len(prefix) >= 3 and name.startswith(prefix + "_"):
                    b.add_env(integ, name, "naming", "unresolved",
                              {"repo": app.repo.key, "file": rel, "rule": "env.example"},
                              reason=".env.example に名前の似た変数があるだけで、使っている箇所を確かめていない")


def report_pending(b):
    """未確定の関係を診断に載せる。重大度は WARNING（関係が解決できない）。"""
    for item in b.all_entities():
        for p in item.get("_pending", []):
            if p["field"] in ("calls", "links", "tables"):
                b.add_diagnostic(diagnostic(
                    "WARNING", f"relation.{p['field']}.unresolved",
                    f"{item['key']}: {p['value']} — {p['reason']}"))
