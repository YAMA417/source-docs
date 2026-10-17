"""実体どうしの関係を解決する。フレームワークには依存しない。

入口（画面・API・ジョブ・イベント処理…）ごとに、定義されたコード片から始めて
import を 3 段までたどり、各アダプタの `usages` が拾ったもの（テーブル操作・API 呼び出し・
画面遷移・外部連携・イベント発行・キュー投入）を、検出済みの実体に結び付ける。

**結び付け先が見つからないものは推測で埋めない。** `unmatched` に残し、資料と coverage で見せる。
"""

import re

from .model import normalize_path

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
        uids = self.b.table_aliases.get(alias, set())
        items = [self.b.get(u) for u in uids if self.b.get(u)]
        if not items:
            return []
        same_app = [t for t in items if t["app"] == app_id]
        if same_app:
            return same_app if len(same_app) == 1 else []
        same_repo = [t for t in items if t["app"] and t["app"].startswith(f"app:{repo_key}")]
        pool = same_repo or items
        return pool if len(pool) == 1 else []

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
            for ep in lookup.endpoint(trigger["method"], trigger["path"], "trigger"):
                b.relate(item, "calls", ep["_uid"], item["evidence"][0] if item["evidence"] else None)
        if item.get("_sql"):
            from .adapters.sql import sql_table_usages
            for u in sql_table_usages(item["_sql"]):
                for t in lookup.tables(u["alias"], app.id, app.repo.key):
                    b.relate(item, "tables", t["_uid"], item["evidence"][0], operation=u["op"])
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
            if s <= ds < e or (f, ds) in seen or not _should_follow(body, name, whole[ds:de], depth):
                continue
            seen.add((f, ds))
            queue.append((f, ds, de, depth + 1))
    return out


def _env_prefix(key):
    """連携先のキーから、環境変数名の接頭辞を作る（`github-oauth` → `GITHUB`、`host:api.x.com` → `API`）。"""
    return re.split(r"[-.:]", key.split(":")[-1])[0].upper()


def _without_import_lines(text):
    return re.sub(r"^\s*(?:import|export\s+\*|from\s+\S+\s+import)\b[^\n]*$", "", text, flags=re.M)


def _resolve_item(b, lookup, app, adapters, item, loc):
    env_by_file = {}
    integrations_by_file = {}
    unmatched = []
    for rel, text, depth, offset in _walk(app, adapters, loc):
        aliases = {}
        whole = app.read(rel)
        for a in adapters:
            aliases.update(a.aliases(app, rel, whole))
        base_line = whole.count("\n", 0, offset)
        for a in adapters:
            for u in a.usages(app, rel, text):
                line = base_line + text.count("\n", 0, u.get("offset", 0)) + 1
                ev = app.ev(rel, line, f"usage.{u['kind']}")
                if u["kind"] == "table" and u["alias"] in aliases                         and not lookup.tables(u["alias"], app.id, app.repo.key):
                    u = dict(u, alias=aliases[u["alias"]])
                _apply(b, lookup, app, item, u, ev, unmatched, env_by_file, integrations_by_file, rel)
    # 環境変数は、同じファイルで使っている外部連携に結び付ける
    # 変数名の接頭辞が連携先の名前と合うもの（`STRIPE_*` → Stripe）はその連携先にだけ付ける。
    # どれとも合わないものは、同じファイルの連携先すべてに付ける（どれのものか決められないため）
    for rel, keys in integrations_by_file.items():
        for name in env_by_file.get(rel, []):
            owners = [k for k in keys if name.startswith(_env_prefix(k) + "_")]
            for key in owners or keys:
                integ = b.get(f"integrations||{key}")
                if name not in integ["envVars"]:
                    integ["envVars"].append(name)
    if unmatched:
        item.setdefault("unmatched", [])
        for u in unmatched:
            if u not in item["unmatched"]:
                item["unmatched"].append(u)


def _apply(b, lookup, app, item, u, ev, unmatched, env_by_file, integrations_by_file, rel):
    kind = u["kind"]
    if kind == "table":
        tables = lookup.tables(u["alias"], app.id, app.repo.key)
        for t in tables:
            if t["_uid"] != item["_uid"]:
                b.relate(item, "tables", t["_uid"], ev, operation=u["op"])
    elif kind == "integration":
        webhook = bool(item.get("webhook"))
        integ = b.add_integration(u["key"], u["name"], ev,
                                  direction="inbound" if webhook else "outbound",
                                  protocol=u.get("protocol"), sdk=u.get("sdk"), host=u.get("host"))
        b.relate(item, "integrations", integ["_uid"], ev)
        integrations_by_file.setdefault(rel, set()).add(u["key"])
    elif kind == "call":
        found = lookup.endpoint(u["method"], u["path"], u.get("methodSource"))
        found = [e for e in found if e["_uid"] != item["_uid"]]
        for e in found:
            b.relate(item, "calls", e["_uid"], ev)
        if not found:
            unmatched.append({"kind": "call", "value": f"{u['method']} {u['path']}",
                              "file": ev["file"], "line": ev["line"]})
    elif kind == "navigate" and item["_collection"] == "screens":
        found = lookup.screen(u["route"])
        for s in found:
            if s["_uid"] != item["_uid"]:
                b.relate(item, "links", s["_uid"], ev, tag=u.get("how"))
        if not found:
            endpoints = [e for e in lookup.endpoint("GET", u["route"], "default") if e["_uid"] != item["_uid"]]
            for e in endpoints:
                b.relate(item, "calls", e["_uid"], ev)
            if not endpoints:
                unmatched.append({"kind": "navigate", "value": u["route"], "file": ev["file"],
                                  "line": ev["line"]})
    elif kind == "publish":
        event = b.add_event_publisher(u["event"], ev)
        b.relate(item, "publishes", event["_uid"], ev)
    elif kind == "enqueue":
        consumers = lookup.consumers.get(u["queue"], []) if u.get("queue") else []
        for j in consumers:
            if j["_uid"] != item["_uid"]:
                b.relate(item, "enqueues", j["_uid"], ev)
        if not consumers:
            unmatched.append({"kind": "enqueue", "value": u.get("queue") or "（名前不明）",
                              "file": ev["file"], "line": ev["line"]})
    elif kind == "env":
        env_by_file.setdefault(rel, [])
        if u["name"] not in env_by_file[rel]:
            env_by_file[rel].append(u["name"])
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
                b.relate(e, "integrations", integ["_uid"], e["evidence"][0] if e["evidence"] else None)
        for rel in e.get("_rel", {}).get("integrations", {}):
            integ = b.get(rel)
            if integ and "inbound" not in integ["directions"]:
                integ["directions"].append("inbound")


def apply_env_examples(b, apps):
    """`.env.example` に並ぶ変数名のうち、外部連携の名前で始まるものを結び付ける。"""
    from .adapters.integrations import env_example_names
    for app in apps:
        for name, rel in env_example_names(app).items():
            for integ in b.items("integrations"):
                prefix = re.sub(r"[^A-Z0-9]", "_", integ["key"].split(":")[-1].upper())
                if name.startswith(prefix + "_") and name not in integ["envVars"]:
                    integ["envVars"].append(name)
                    integ["evidence"].append({"repo": app.repo.key, "file": rel, "rule": "env.example"})
