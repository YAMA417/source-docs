"""解決済みの view（`core.resolve`）と注釈から Markdown の資料を描く。

**ここでは何も推測しない。** 本文に書くのは状態が confirmed / inferred の事実と、根拠付きの注釈だけ。

- 未確定（unresolved / conflict / unsupported の事実）は、資料末尾の「未確定」に送る。本文には出さない
- 未確認（資料に書くべきなのに分からないこと）は、種類（ライブラリ側・実行時に決まる…）を付けて「未確認」に送る

資料どうしは同じモデルを見ているので、API の一覧と機能一覧と画面一覧で数や名前が食い違わない。
"""

from ..core.features import TYPE_LABEL, feature_slug
from ..core.model import OPERATIONS, index
from ..core.status import PENDING_FIELD, STATUS_LABEL as FACT_STATUS, UNVERIFIED_LABEL
from . import mermaid as mm

UNK = "**未確認**"
NA = "—"
OP_SHORT = {"CREATE": "C", "READ": "R", "UPDATE": "U", "DELETE": "D"}
DIRECTION = {"outbound": "送信", "inbound": "受信"}
STATUS_LABEL = {"implemented": "実装あり", "not-implemented": "実装なし（コード上に見当たらない）",
                "unverified": UNK}
CONCURRENCY_LABEL = {"optimistic-lock": "楽観ロック", "pessimistic-lock": "悲観ロック",
                     "unique-constraint": "一意制約", "duplicate-check": "重複チェック",
                     "advisory-lock": "アドバイザリロック", "other": "その他"}
SIDE_EFFECT_LABEL = {"email": "メール", "notification": "通知", "event": "イベント発行",
                     "queue": "キュー投入", "cache": "キャッシュ更新", "file": "ファイル作成",
                     "external-api": "外部 API 呼び出し", "other": "その他"}
AUTH_KINDS = ("authentication", "authorization", "role", "ownership")
PRECONDITION_LABEL = {"authentication": "認証", "authorization": "認可", "role": "ロール",
                      "ownership": "所有者の確認", "business": "業務上の前提", "other": "その他"}

DOCS = [
    ("overview", "overview.md", "システム概要"),
    ("features", "features.md", "機能一覧"),
    ("database", "database.md", "DB 定義"),
    ("api", "api.md", "API 仕様"),
    ("screens", "screens.md", "画面"),
    ("integrations", "integrations.md", "外部連携"),
]


# --- 書式の道具 -----------------------------------------------------------------

def cell(value):
    if value is None or value == "" or value == []:
        return NA
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def code(value):
    text = str(value)
    if "`" in text:
        return "`` " + text + " ``"
    return f"`{text}`"


def table(headers, rows):
    if not rows:
        return ""
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(cell(c) for c in r) + " |")
    return "\n".join(out)


def or_unk(value):
    return UNK if value in (None, "", []) else value


def ops(operations):
    return "".join(OP_SHORT[o] for o in operations) if operations else ""


class Ctx:
    """描画に使う共有状態。読むのは Resolved Project Model（`core.resolve`）だけ。

    注釈ファイル・解析の生の結果・根拠の比較はここでは扱わない。何が正しいかは描画の前に決まっている。
    """

    def __init__(self, resolved, details_present=(), flows_present=(), prefix=""):
        self.model = resolved
        self.ann = resolved.get("narrative") or {}
        self.idx = index(resolved)
        features = resolved["features"]
        self.features = features
        self.feature_by_id = {f["id"]: f for f in features}
        self.details = set(details_present)
        self.flows = set(flows_present)
        self.prefix = prefix  # 下位ディレクトリの資料から上位へリンクするときの `../`
        self.excluded = {e["id"]: e.get("reason") for e in self.ann.get("exclusions") or []}
        self.features_of = {}
        for f in features:
            for key in ("screens", "endpoints", "integrations", "jobs", "events", "entrypoints"):
                for ref in f[key]:
                    self.features_of.setdefault(ref, []).append(f)
            for t in f["tables"]:
                self.features_of.setdefault(t["id"], []).append(f)

    def apps(self):
        """資料に出すアプリ。中身の無い入れ物（モノレポの直下など）は出さない。"""
        out = [a for a in self.model["applications"]
               if any(a["counts"].values()) or a["kind"] != "unknown"]
        return out or self.model["applications"]

    def app_label(self, app_id):
        """アプリの表示名。同じ名前のアプリが複数あれば場所を付けて区別する。"""
        app = self.idx.get(app_id) or {}
        name = app.get("name") or app.get("key") or app_id
        same = [a for a in self.model["applications"] if (a.get("name") or a.get("key")) == name]
        return f"{name}（{app.get('path')}）" if len(same) > 1 else name

    def note(self, entity_id):
        return (self.idx.get(entity_id) or {}).get("notes") or {}

    def summary(self, entity_id):
        return self.note(entity_id).get("summary")

    def feature_names(self, entity_id):
        return ", ".join(sorted({feature_name(f) for f in self.features_of.get(entity_id, [])}))

    def link(self, rel_path):
        return self.prefix + rel_path


def feature_name(f):
    return f["name"] if f["nameSource"] == "annotation" else f"{f['name']}（名前未確定）"


def header(ctx, title, purpose):
    commit = ctx.model.get("generatedCommit")
    lines = [f"# {title}", "",
             f"生成コミット: {code(commit[:12]) if commit else UNK}", "",
             purpose, "",
             "> この資料は source-docs が Project Model（コードから機械で取り出した事実）と注釈"
             "（根拠付きの意味づけ）から生成した。直すときは `.source-docs/annotations.json` を直して"
             "再生成する。", ""]
    return lines


ENTITY_KIND = {"endpoint": "API", "screen": "画面", "table": "テーブル", "integration": "外部連携", "job": "ジョブ"}


class Unverified:
    """資料末尾の 2 つの一覧。

    - 未確定: 候補や根拠はあるが確定できなかった事実（状態つき）。本文には出していない
    - 未確認: 資料に書くべきなのに分からないこと（種類と、調べた範囲つき）
    """

    def __init__(self):
        self.rows = []
        self.pending = []

    def add(self, target, what, searched, kind="unresolved"):
        row = (target, what, UNVERIFIED_LABEL.get(kind, kind), searched)
        if row not in self.rows:
            self.rows.append(row)

    def add_pending(self, ctx, target, p):
        value = p["value"]
        shown = entity_label(ctx, value) if isinstance(value, str) and value in ctx.idx else value
        cands = ", ".join(entity_label(ctx, c) for c in p.get("candidates", []))
        reason = (p.get("reason") or "確定できない") + (f"（候補: {cands}）" if cands else "")
        value_cell = code(shown) if shown is not None else "（値を決められない）"
        row = (target, PENDING_FIELD.get(p["field"], p["field"]), value_cell,
               FACT_STATUS.get(p["status"], p["status"]), reason, _ev_cell(p.get("evidence")))
        if row not in self.pending:
            self.pending.append(row)

    def from_entity(self, ctx, entity_id, label=None):
        target = label or entity_id
        self.pending_of(ctx, entity_id, target)
        for u in ctx.note(entity_id).get("unverified") or []:
            self.add(target, u["item"], u.get("searched"), u.get("kind") or "unresolved")

    def pending_of(self, ctx, entity_id, target):
        """実体の未確定（実体そのものと、その関係・値）を未確定の一覧に足す。"""
        item = ctx.idx.get(entity_id) or {}
        # 実体そのものが確定できない（HTTP メソッドが決まらない など）。未解決の事実なので未確定に置く
        for reason in item.get("unresolved", []):
            row = (target, ENTITY_KIND.get(entity_id.split(":", 1)[0], "実体"), code(entity_label(ctx, entity_id)),
                   FACT_STATUS.get(item.get("status"), FACT_STATUS["unresolved"]), reason,
                   _ev_cell(item.get("evidence", [])[:3]))
            if row not in self.pending:
                self.pending.append(row)
        for p in item.get("pending", []):
            self.add_pending(ctx, target, p)

    def from_list(self, target, items):
        for u in items or []:
            self.add(target, u["item"], u.get("searched"), u.get("kind") or "unresolved")

    def render(self):
        lines = ["## 未確定", "",
                 "コードに候補や根拠はあるが、確定できなかった事実。本文では確定した事実として扱っていない。", ""]
        if self.pending:
            lines += [table(["対象", "項目", "値", "状態", "理由", "根拠"], self.pending), ""]
        else:
            lines += ["なし。", ""]
        lines += ["## 未確認", ""]
        if not self.rows:
            lines.append("なし。")
            return lines
        lines.append(table(["対象", "分からなかったこと", "種類", "調べた範囲"], self.rows))
        return lines


def _searched(item):
    rules = sorted({e.get("rule") for e in item.get("evidence", []) if e.get("rule")})
    files = sorted({e["file"] for e in item.get("evidence", [])})[:3]
    where = "、".join(code(f) for f in files) if files else "コード全体"
    return f"自動解析（{', '.join(rules) or '規則なし'}）で {where} を読んだ範囲"


def entity_label(ctx, entity_id):
    item = ctx.idx.get(entity_id)
    if not item:
        return entity_id
    kind = entity_id.split(":", 1)[0]
    if kind == "table":
        return item["key"]
    if kind == "screen":
        return item["route"]
    if kind == "integration":
        return item["name"]
    if kind == "job":
        return f"{item['kind']} {item['name']}"
    return item.get("key", entity_id)


def labels(ctx, ids):
    return ", ".join(entity_label(ctx, i) for i in ids)


def tables_cell(ctx, rels):
    parts = []
    for r in rels:
        name = entity_label(ctx, r["id"])
        o = ops(r.get("operations"))
        parts.append(f"{name} ({o})" if o else name)
    return ", ".join(parts)


def missing_summary(unverified, kind_label, items, ctx):
    """注釈の無い実体は、1 行ずつではなく件数で未確認一覧に載せる。"""
    missing = [i for i in items if not ctx.summary(i["id"]) and i["id"] not in ctx.excluded]
    if missing:
        names = ", ".join(entity_label(ctx, i["id"]) for i in missing[:5])
        more = f" ほか {len(missing) - 5} 件" if len(missing) > 5 else ""
        unverified.add(f"{kind_label} {len(missing)} 件", f"概要・仕様の注釈が無い（{names}{more}）",
                       "注釈未作成。`coverage` の described で一覧できる")


# --- overview.md ----------------------------------------------------------------

def render_overview(ctx):
    m, a = ctx.model, ctx.ann
    sysn = a.get("system") or {}
    uv = Unverified()
    lines = header(ctx, "システム概要",
                   "このシステムが何で、主要な部分がどうつながっているかを示す。各資料への入口。")

    lines += ["## このシステムは何か", ""]
    if sysn.get("name"):
        lines += [f"**{sysn['name']}**", ""]
    lines += [or_unk(sysn.get("summary")), ""]
    if not sysn.get("summary"):
        uv.add("システム", "何のためのシステムか", "注釈未作成。README・docs・画面文言から書く")
    lines += ["### 主な利用者", ""]
    if sysn.get("users"):
        lines += [f"- {u}" for u in sysn["users"]]
    else:
        lines.append(UNK)
        uv.add("システム", "主な利用者", "注釈未作成")
    lines.append("")
    uv.from_list("システム", sysn.get("unverified"))

    lines += ["## 全体構成図", "", _architecture_diagram(ctx), ""]

    lines += ["## アプリケーションと責務", ""]
    rows = []
    for app in ctx.apps():
        note = (a.get("applications") or {}).get(app["id"]) or {}
        c = app["counts"]
        rows.append([ctx.app_label(app["id"]), code(app["path"]), app["kind"],
                     or_unk(note.get("responsibility")), c["screens"], c["endpoints"], c["tables"],
                     c["jobs"]])
        if not note.get("responsibility"):
            uv.add(ctx.app_label(app["id"]), "アプリケーションの責務", "注釈未作成")
    lines += [table(["アプリケーション", "場所", "種別", "責務", "画面数", "API 数", "テーブル数", "ジョブ数"], rows), ""]
    lines += ["種別はコードから見つかったもので決めている（画面と API の両方があれば fullstack）。", ""]

    lines += ["## 技術スタック", ""]
    rows = []
    for app in ctx.apps():
        s = app["stack"]
        rows.append([ctx.app_label(app["id"]), ", ".join(s["frontend"]) or NA,
                     ", ".join(s["backend"]) or NA, ", ".join(s["orm"]) or NA,
                     ", ".join(s["db"]) or NA, ", ".join(code(x) for x in app["manifests"]) or NA])
    lines += [table(["アプリケーション", "フロント", "バック", "ORM", "DB", "判定の根拠"], rows), ""]

    lines += ["## 主要コンポーネント", ""]
    counts = {}
    for ep in m["entrypoints"]:
        counts[ep["type"]] = counts.get(ep["type"], 0) + 1
    rows = [[_EP_LABEL.get(t, t), n] for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    rows += [["テーブル・ビュー", len(m["tables"])], ["外部連携", len(m["integrations"])],
             ["イベント", len(m["events"])]]
    lines += [table(["入口・構成要素", "件数"], rows), ""]

    lines += ["## 主要機能", ""]
    ranked = sorted(ctx.features, key=lambda f: (f["nameSource"] != "annotation", f["type"] == "api"))
    rows = []
    for f in ranked[:15]:
        rows.append([feature_name(f), TYPE_LABEL.get(f["type"], f["type"]), or_unk(f.get("summary"))])
    lines += [table(["機能", "種別", "概要"], rows) or "機能は見つからなかった。", ""]
    if len(ctx.features) > 15:
        lines += [f"ほか {len(ctx.features) - 15} 件。全体は [機能一覧]({ctx.link('features.md')})。", ""]
    no_screen = [f for f in ctx.features if not f["screens"]]
    lines += [f"機能 {len(ctx.features)} 件のうち、画面を持たないもの（API・Webhook・定期実行・"
              f"キュー・イベント処理など）は {len(no_screen)} 件。", ""]

    lines += _health_section(ctx)

    lines += ["## 資料の案内", ""]
    for key, file, title in DOCS[1:]:
        lines.append(f"- [{title}]({ctx.link(file)}) — {_DOC_PURPOSE[key]}")
    lines += ["- `.source-docs/project-model.json` — 上の資料すべての元データ（機械可読）",
              "- `.source-docs/evidence.json` — 各項目がどのソースを根拠にしているか",
              "- `.source-docs/coverage.json` — コードにあるもののうち、資料に載っている割合", ""]

    lines += ["## 解析の範囲", ""]
    rows = [[r["key"], r["files"], code(r["commit"][:12]) if r.get("commit") else UNK]
            for r in m["repositories"]]
    lines += [table(["リポジトリ", "読んだファイル数", "コミット"], rows), ""]
    rows = [[ctx.app_label(app["id"]), ", ".join(app["adapters"])] for app in ctx.apps()]
    lines += [table(["アプリケーション", "適用した解析器"], rows), ""]
    if m["unsupported"]:
        lines += [f"解析器が読み取れなかった箇所が {len(m['unsupported'])} 件ある（未確認一覧に載せた）。", ""]
        for u in m["unsupported"]:
            uv.add(code(u["file"]), u["reason"], "自動解析")
    lim = m.get("limits") or {}
    if lim.get("truncated"):
        uv.add("リポジトリ " + ", ".join(lim["truncated"]), "読み込み上限を超えたため一部を読んでいない",
               "50MB まで読んだ")
    for f in lim.get("skippedLargeFiles", []):
        uv.add(code(f), "1MB を超えるため読んでいない", "ファイルサイズで除外")

    lines += uv.render()
    return "\n".join(lines) + "\n"


def _health_section(ctx):
    """Documentation Health。資料をどこまで信用してよいかを、件数で示す。"""
    from ..core.quality import status_reasons
    from ..validators.coverage import health
    h = health(ctx.model)
    status, reasons = status_reasons(ctx.model)
    lines = ["## 資料の健全性（Documentation Health）", "",
             "コードから見つけたもののうち、どれだけが確定し、どれだけに説明が付いているか。"
             "`coverage.json` と `check` が同じ数字を使う。", "",
             "### 資料の状態（Overall Status）", "",
             f"**{status}** — {_STATUS_TEXT[status]}", "", "理由:", ""]
    for label, n, decides in reasons:
        lines.append(f"- {label}: {n} 件" + (f"（1 件以上なら {decides}）" if n else ""))
    lines += ["", "判定は上から順に当てはめる。品質ゲートの ERROR か矛盾が 1 件以上なら FAILED、"
              "未解決か解析の対象外が 1 件以上なら WARNING、どれも 0 件なら SAFE。"
              "資料の古さ（生成後のコードの変更）と、書き出した資料の機密検査は `source_docs.py check` が重ねる"
              "（古ければ WARNING、機密があれば FAILED）。", ""]
    q = h["quality"]
    rows = [[k.capitalize(), FACT_STATUS[k], q[k]] for k in
            ("confirmed", "inferred", "unresolved", "conflict", "unsupported")]
    lines += ["### 事実の状態（Fact Status）", "", table(["状態", "意味", "件数"], rows), ""]
    d = h["diagnostics"]
    lines += ["### 品質ゲートの診断（Quality Gate Diagnostics）", "",
              table(["Severity", "件数"], [[s, d[s]] for s in ("ERROR", "WARNING", "INFO")]), "",
              "品質ゲートが出した診断メッセージの数。資料の状態（Overall Status）とは別のもので、"
              "未解決の事実の数は上の「事実の状態」で数える。", ""]
    rows = []
    for key, r in h["detection"].items():
        rows.append([r["label"], r["detected"], r["described"], r["pending"]])
    lines += ["### 検出", "", table(["種別", "検出数", "説明あり（注釈）", "未確定"], rows), ""]
    rows = []
    for key, r in h["relationships"].items():
        pct = "—" if r["coverage"] is None else f"{r['coverage'] * 100:.0f}%"
        rows.append([r["label"], r["resolved"], r["unresolved"], pct])
    lines += ["### 関係", "", table(["関係", "確定", "未確定", "確定の割合"], rows), "",
              "「確定」は confirmed と inferred（根拠付きの推定）の合計。", ""]
    return lines


_STATUS_TEXT = {
    "SAFE": "矛盾も未確定も無い。",
    "WARNING": "矛盾は無い。確定できなかった事実があり、各資料末尾の「未確定」に分けてある。",
    "FAILED": "根拠どうしの矛盾か、品質ゲートの誤りがある。矛盾した事実は本文から外してある。`check` で中身を見る。",
}


_EP_LABEL = {"screen": "画面", "http": "HTTP API", "webhook": "Webhook", "graphql": "GraphQL",
             "rpc": "RPC", "websocket": "WebSocket", "cron": "定期実行（Cron）", "scheduler": "スケジューラ",
             "queue-consumer": "キュー処理", "worker": "ワーカー", "event-handler": "イベント処理",
             "cli": "CLI", "db-trigger": "DB トリガ", "lifecycle-hook": "ライフサイクルフック"}
_DOC_PURPOSE = {
    "features": "機能ごとに、入口・画面・API・テーブル・外部連携のつながりを示す",
    "database": "テーブル定義・制約・インデックス・ER 図",
    "api": "API の共通仕様と、エンドポイントごとの入出力",
    "screens": "画面一覧・画面遷移・画面項目",
    "integrations": "外部サービスとの送受信と認証情報の所在",
}


def _architecture_diagram(ctx):
    m = ctx.model
    lines = ["flowchart LR", '    user(["利用者"])']
    app_nodes = {}
    for i, app in enumerate(ctx.apps()):
        nid = f"app{i}"
        app_nodes[app["id"]] = nid
        lines.append(f'    {nid}["{mm.label(ctx.app_label(app["id"]) + " (" + app["kind"] + ")")}"]')
    for app in ctx.apps():
        if app["counts"]["screens"]:
            lines.append(f"    user -->|画面を操作| {app_nodes[app['id']]}")
    # アプリをまたぐ API 呼び出し
    cross = set()
    for s in m["screens"]:
        for rel in s.get("calls", []):
            target = ctx.idx.get(rel["id"])
            if target and target["app"] != s["app"]:
                cross.add((s["app"], target["app"]))
    for src, dst in sorted(cross):
        if src in app_nodes and dst in app_nodes:
            lines.append(f"    {app_nodes[src]} -->|API を呼ぶ| {app_nodes[dst]}")
    db_apps = sorted({t["app"] for t in m["tables"]})
    if db_apps:
        lines.append('    db[("データベース")]')
        for app_id in db_apps:
            if app_id in app_nodes:
                lines.append(f"    {app_nodes[app_id]} -->|スキーマを定義| db")
    for i, integ in enumerate(m["integrations"]):
        nid = f"ext{i}"
        lines.append(f'    {nid}(["{mm.label(integ["name"])}"])')
        users = {e["app"] for coll in ("screens", "endpoints", "jobs", "entrypoints")
                 for e in m[coll] if any(r["id"] == integ["id"] for r in e.get("integrations", []))}
        for app_id in sorted(users):
            if app_id not in app_nodes:
                continue
            if "outbound" in integ["directions"]:
                lines.append(f"    {app_nodes[app_id]} -->|呼び出す| {nid}")
            if "inbound" in integ["directions"]:
                lines.append(f"    {nid} -.->|Webhook| {app_nodes[app_id]}")
    return mm.block(lines)


# --- features.md ----------------------------------------------------------------

def primary_entrypoint(ctx, f):
    eps = [ctx.idx[e] for e in f["entrypoints"] if e in ctx.idx]
    for preferred in ("screen",):
        for ep in eps:
            if ep["type"] == preferred:
                return ep
    for ep in eps:
        if ep["type"] not in ("http", "graphql", "rpc", "websocket"):
            return ep
    return eps[0] if eps else None


def render_features(ctx):
    uv = Unverified()
    lines = header(ctx, "機能一覧",
                   "システムにある機能と、それぞれが画面・API・DB・外部連携とどうつながるかを示す。"
                   "機能の入口は画面に限らない（Webhook・定期実行・キュー・イベント処理も機能として扱う）。")
    lines += ["## 一覧", ""]
    rows = []
    for f in ctx.features:
        ep = primary_entrypoint(ctx, f)
        start = f"{_EP_LABEL.get(ep['type'], ep['type'])}: {ep['key']}" if ep else NA
        rows.append([
            feature_name(f), TYPE_LABEL.get(f["type"], f["type"]), start,
            labels(ctx, f["screens"]), labels(ctx, f["endpoints"]), tables_cell(ctx, f["tables"]),
            labels(ctx, f["integrations"]), _doc_links(ctx, f),
        ])
    lines += [table(["機能", "種別", "起点", "Screen", "API", "DB", "外部連携", "詳細設計"], rows)
              or "機能は見つからなかった。", ""]
    legend = "DB 欄の括弧は操作（C 作成 / R 参照 / U 更新 / D 削除）。"
    if any(f["nameSource"] != "annotation" for f in ctx.features):
        legend += "「名前未確定」はコードの識別子をそのまま名前にしている候補で、業務上の名前は注釈で付ける。"
    lines += [legend, ""]

    counts = {}
    for f in ctx.features:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    lines += ["### 種別ごとの件数", "",
              table(["種別", "件数"], [[TYPE_LABEL.get(t, t), n] for t, n in sorted(
                  counts.items(), key=lambda kv: list(TYPE_LABEL).index(kv[0]) if kv[0] in TYPE_LABEL else 99)]), ""]

    lines += ["## 機能ごとのつながり", ""]
    for f in ctx.features:
        lines += [f"### {feature_name(f)}", ""]
        lines.append(or_unk(f.get("summary")))
        lines.append("")
        eps = [ctx.idx[e] for e in f["entrypoints"] if e in ctx.idx]
        origins = [e for e in eps if e["type"] not in ("http", "graphql", "rpc", "websocket")] or eps
        rows = [["起点", "、".join(f"{_EP_LABEL.get(e['type'], e['type'])} {code(e['key'])}" for e in origins)],
                ["画面", labels(ctx, f["screens"])], ["API", labels(ctx, f["endpoints"])],
                ["テーブル", tables_cell(ctx, f["tables"])], ["外部連携", labels(ctx, f["integrations"])],
                ["ジョブ・キュー", labels(ctx, f["jobs"])], ["イベント", labels(ctx, f["events"])],
                ["資料", _doc_links(ctx, f)]]
        lines += [table(["項目", "内容"], rows), ""]
        if len(f["entrypoints"]) + len(f["tables"]) + len(f["integrations"]) + len(f["events"]) >= 3:
            lines += [_feature_diagram(ctx, f), ""]
        for eid in f.get("pendingFrom", []):
            uv.pending_of(ctx, eid, f"{feature_name(f)}（{code(entity_label(ctx, eid))}）")
    unnamed = [feature_name(f) for f in ctx.features if f["nameSource"] != "annotation"]
    if unnamed:
        more = f" ほか {len(unnamed) - 5} 件" if len(unnamed) > 5 else ""
        uv.add(f"名前未確定の機能 {len(unnamed)} 件", f"業務上の機能名（{', '.join(unnamed[:5])}{more}）",
               "コードの命名からは決められない。画面の文言・README・docs に根拠があれば注釈で付ける")
    lines += uv.render()
    return "\n".join(lines) + "\n"


def _doc_links(ctx, f):
    parts = []
    slug = feature_slug(f["id"])
    if f["id"] in ctx.flows:
        parts.append(f"[処理概要]({ctx.link('flow/' + slug + '.md')})")
    if f["id"] in ctx.details:
        parts.append(f"[詳細設計]({ctx.link('detail/' + slug + '.md')})")
    return " / ".join(parts) or "未作成"


def _feature_diagram(ctx, f):
    lines = ["flowchart LR"]
    nodes = {}

    def node(entity_id, shape):
        if entity_id in nodes:
            return nodes[entity_id]
        nid = f"n{len(nodes)}"
        nodes[entity_id] = nid
        text = mm.label(entity_label(ctx, entity_id))
        lines.append("    " + shape.format(nid=nid, text=text))
        return nid

    for ep_id in f["entrypoints"]:
        ep = ctx.idx.get(ep_id)
        if not ep or len(nodes) >= mm.MAX_NODES:
            continue
        src = node(ep_id, '{nid}["{text}"]')
        target = ctx.idx.get(ep.get("target")) if ep.get("target") else None
        sources = [ep] + ([target] if target else [])
        for item in sources:
            for rel in item.get("tables", []):
                dst = node(rel["id"], '{nid}[("{text}")]')
                lines.append(f"    {src} -->|{ops(rel.get('operations')) or '参照'}| {dst}")
            for rel in item.get("integrations", []):
                dst = node(rel["id"], '{nid}(["{text}"])')
                lines.append(f"    {src} -->|連携| {dst}")
            for rel in item.get("calls", []):
                callee = [e for e in ctx.model["entrypoints"] if e.get("target") == rel["id"]]
                if callee:
                    dst = node(callee[0]["id"], '{nid}["{text}"]')
                    lines.append(f"    {src} -->|呼び出し| {dst}")
            for rel in item.get("publishes", []):
                dst = node(rel["id"], '{nid}{{{{"{text}"}}}}')
                lines.append(f"    {src} -.->|発行| {dst}")
            for rel in item.get("enqueues", []):
                dst = node(rel["id"], '{nid}[/"{text}"/]')
                lines.append(f"    {src} -.->|投入| {dst}")
    seen, out = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return mm.block(out)


# --- database.md ----------------------------------------------------------------

def render_database(ctx):
    m = ctx.model
    uv = Unverified()
    lines = header(ctx, "DB 定義",
                   "宣言されたスキーマ（テーブル・カラム・制約・インデックス・Enum・ビュー・トリガの存在）を示す。"
                   "トリガや RLS の中身、データの使われ方は書かない。")
    tables = [t for t in m["tables"] if t["kind"] == "table"]
    views = [t for t in m["tables"] if t["kind"] != "table"]

    lines += ["## 概要", ""]
    dbs = sorted({d for app in m["applications"] for d in app["stack"]["db"]})
    lines.append(f"- DB: {', '.join(dbs) if dbs else UNK}")
    if not dbs:
        uv.add("DB", "DB の種類", "manifest の依存名から判定できなかった")
    lines.append(f"- テーブル {len(tables)} 件、ビュー {len(views)} 件、Enum {len(m['enums'])} 件、"
                 f"シーケンス {len(m['sequences'])} 件")
    lines.append("")
    schemas = {}
    for t in m["tables"]:
        key = t.get("schema") or "（既定）"
        rules = {e.get("rule", "").split(".")[0] for e in t["evidence"]}
        entry = schemas.setdefault(key, [0, set()])
        entry[0] += 1
        entry[1] |= rules
    lines += ["### スキーマ一覧", "",
              table(["スキーマ", "テーブル・ビュー数", "定義の形式"],
                    [[k, v[0], ", ".join(sorted(r for r in v[1] if r))] for k, v in sorted(schemas.items())])
              or "テーブルは見つからなかった。", ""]

    lines += ["## テーブル一覧", ""]
    rows = []
    for t in m["tables"]:
        rows.append([code(t["key"]), _KIND.get(t["kind"], t["kind"]), or_unk(ctx.summary(t["id"])),
                     len(t["columns"]), ctx.feature_names(t["id"])])
    lines += [table(["テーブル", "種別", "概要", "カラム数", "関連する機能"], rows) or "なし。", ""]
    missing_summary(uv, "テーブル", m["tables"], ctx)

    lines += ["## ER 図", ""]
    lines += _er_diagrams(ctx, tables)

    lines += ["## テーブル定義", ""]
    for t in m["tables"]:
        lines += _table_section(ctx, t, uv)

    if m["enums"]:
        lines += ["## Enum", "", table(["名前", "値"], [[code(e["name"]), ", ".join(e["values"])]
                                                     for e in m["enums"]]), ""]
    if m["sequences"]:
        lines += ["## シーケンス", "", table(["名前"], [[code(s["name"])] for s in m["sequences"]]), ""]
    triggers = [(t, tr) for t in m["tables"] for tr in t.get("triggers", [])]
    if triggers:
        lines += ["## トリガ", "", "存在と起動条件だけを書く。処理の中身は解析していない。", "",
                  table(["トリガ", "テーブル", "タイミング", "イベント", "関数"],
                        [[code(tr["name"]), code(t["key"]), tr["timing"], tr["events"],
                          code(tr["function"]) if tr.get("function") else UNK] for t, tr in triggers]), ""]
    lines += uv.render()
    return "\n".join(lines) + "\n"


# ER 図の記法。左がこのテーブル、右が参照先。実線は DB の外部キー、点線は ORM の関係
_ORM_LABEL = {"prisma": "Prisma", "drizzle": "Drizzle", "sqlalchemy": "SQLAlchemy", "strapi": "Strapi",
              "migration": "migration"}
ER_RELATION = {"fk": "}o--||", "orm": "}o..||", "oneToOne": "||..||", "oneToMany": "||..o{",
               "manyToOne": "}o..||", "manyToMany": "}o..o{", "morphToMany": "}o..o{", "morphToOne": "}o..||"}
_PHYSICAL_FK = {True: "あり", False: "無い", None: "確かめられない"}
_KIND = {"table": "テーブル", "view": "ビュー", "materialized-view": "マテリアライズドビュー"}


def _orm_label(rel):
    """ER 図の点線のラベル。DB の外部キーではないことが分かるように「ORM relation」と書く。"""
    source = _ORM_LABEL.get(rel.get("source"), rel.get("source") or "ORM")
    detail = ", ".join(x for x in (source, rel.get("relation")) if x)
    return f"ORM relation ({detail})"


def _er_diagrams(ctx, tables):
    if not tables:
        return ["テーブルが無いため ER 図は無い。", ""]
    names = [t["key"] for t in tables]
    by_name = {t["key"]: t for t in tables}
    edges = []
    for t in tables:
        orm = {(tuple(r["columns"]), r["references"]["table"]): r for r in t["ormRelations"]}
        physical = set()
        for fk in t["physicalForeignKeys"]:
            target = fk["references"]["table"]
            if target in by_name:
                physical.add((tuple(fk["columns"]), target))
                label = f"Physical FK ({', '.join(fk['columns'])})"
                # DB の外部キーと ORM の関係が同じカラムなら 1 本にする（実線を優先）
                same = orm.get((tuple(fk["columns"]), target))
                if same:
                    label += " / " + _orm_label(same)
                edges.append((t["key"], target, label, "fk"))
        for rel in t["ormRelations"]:
            target = rel["references"]["table"]
            if target not in by_name or (tuple(rel["columns"]), target) in physical:
                continue
            edges.append((t["key"], target, _orm_label(rel), rel.get("relation") or "orm"))
    groups = mm.pack(mm.components(names, [(a, b) for a, b, _, _ in edges]))
    out = []
    if edges:
        out += ["実線（Physical FK）は DB にある外部キー、点線（ORM relation）はアプリ側の ORM の関係で、"
                "DB に外部キーがあるとは限らない。カラムの `FK` は、DB に外部キーがあるものにだけ付ける。", ""]
    if len(groups) > 1:
        out += [f"テーブルが多いため、つながりの塊ごとに {len(groups)} 枚に分けた。", ""]
    for i, group in enumerate(groups, 1):
        if len(groups) > 1:
            out += [f"### ER 図 {i}", ""]
        members = set(group)
        lines = ["erDiagram"]
        for name in group:
            t = by_name[name]
            lines.append(f"    {mm.ident(name, 't')} {{")
            for c in t["columns"][:30]:
                keys = []
                if c["primaryKey"]:
                    keys.append("PK")
                if any(c["name"] in fk["columns"] for fk in t["physicalForeignKeys"]):
                    keys.append("FK")
                if c["unique"]:
                    keys.append("UK")
                lines.append(f"        {mm.er_type(c['type'])} {mm.ident(c['name'], 'c')}"
                             + (f" {','.join(keys)}" if keys else ""))
            lines.append("    }")
        drawn = set()
        for a, b, label, kind in edges:
            if a not in members or b not in members:
                continue
            # 双方向に宣言された ORM の関係（oneToMany と manyToOne の組）は 1 本だけ描く
            pair = frozenset((a, b))
            if kind != "fk" and pair in drawn:
                continue
            drawn.add(pair)
            rel = ER_RELATION.get(kind, "}o--o{")
            lines.append(f'    {mm.ident(a, "t")} {rel} {mm.ident(b, "t")} : "{mm.label(label)}"')
        out += [mm.block(lines), ""]
    return out


def _table_section(ctx, t, uv):
    note = ctx.note(t["id"])
    lines = [f"### {code(t['key'])} — {note.get('summary') or UNK}", ""]
    source = t.get("physicalSource")
    if source:
        lines += [f"定義の根拠: {_ORM_LABEL.get(source, source)}"
                  + ("（migration を順に適用した最終状態）" if source == "migration" else ""), ""]
    col_notes = note.get("columns") or {}
    rows = []
    fk_cols = {c: fk for fk in t["physicalForeignKeys"] for c in fk["columns"]}
    pk_set = set(t["primaryKey"])
    for c in t["columns"]:
        keys = []
        if c["primaryKey"] or c["name"] in pk_set:
            keys.append("PK")
        fk = fk_cols.get(c["name"])
        if fk:
            keys.append(f"FK → {fk['references']['table']}.{', '.join(fk['references']['columns']) or '?'}")
        if c["unique"]:
            keys.append("UK")
        nullable = UNK if c["nullable"] is None else ("可" if c["nullable"] else "不可")
        desc = col_notes.get(c["name"]) or c.get("note") or ""
        if c.get("status") == "inferred" and not desc:
            desc = "規約から推定"
        rows.append([code(c["name"]), c["type"] or UNK, nullable,
                     code(c["default"]) if c.get("default") else NA, " / ".join(keys), desc])
    lines += [table(["カラム", "型", "NULL", "既定値", "キー", "説明"], rows)
              or "カラムを読み取れなかった。", ""]
    if not t["columns"]:
        uv.add(code(t["key"]), "カラム定義", _searched(t), "not-found")
    if t["indexes"]:
        lines += [table(["インデックス", "カラム", "一意"],
                        [[code(i["name"]) if i.get("name") else "（名前なし）", ", ".join(i["columns"]),
                          "はい" if i["unique"] else "いいえ"] for i in t["indexes"]]), ""]
    extra = []
    for u in t["uniques"]:
        extra.append(f"- UNIQUE{(' ' + code(u['name'])) if u.get('name') else ''}: {', '.join(u['columns'])}")
    if len(t["primaryKey"]) > 1:
        extra.append(f"- 複合主キー: {', '.join(t['primaryKey'])}")
    for ck in t["checks"]:
        extra.append(f"- CHECK{(' ' + code(ck['name'])) if ck.get('name') else ''}: {code(ck['expression'])}")
    for tr in t.get("triggers", []):
        extra.append(f"- トリガ {code(tr['name'])}（{tr['timing']} {tr['events']}）")
    for name in t.get("droppedForeignKeys", []):
        extra.append(f"- 外部キー {code(name)} は migration で削除済み（DB には無い）")
    for old in t.get("renamedFrom", []):
        extra.append(f"- migration で {code(old)} から名前を変えた")
    if extra:
        lines += extra + [""]
    physical = {(tuple(f["columns"]), f["references"]["table"]) for f in t["physicalForeignKeys"]}
    orm_only = [r for r in t["ormRelations"]
                if (tuple(r["columns"]), r["references"]["table"]) not in physical]
    if orm_only:
        lines += ["**ORM の関係**（アプリ側の定義。DB の外部キーとは別の事実）", "",
                  table(["名前", "カラム", "参照先", "種類", "定義", "DB の外部キー"], [
                      [code(r["name"]) if r.get("name") else NA, ", ".join(r["columns"]) or NA,
                       r["references"]["table"] + (f".{', '.join(r['references']['columns'])}"
                                                   if r["references"]["columns"] else ""),
                       r.get("relation") or NA, _ORM_LABEL.get(r["source"], r["source"]),
                       _PHYSICAL_FK.get(r.get("physicalForeignKey"), NA)
                       + (f"（{r['physicalForeignKeyReason']}）" if r.get("physicalForeignKeyReason") else "")]
                      for r in orm_only]), ""]
    uv.from_entity(ctx, t["id"], code(t["key"]))
    return lines


# --- api.md ---------------------------------------------------------------------

def render_api(ctx):
    m = ctx.model
    common = ctx.ann.get("api") or {}
    uv = Unverified()
    lines = header(ctx, "API 仕様",
                   "呼び出す側が必要とする情報（入口・入力・出力・ステータス）を定義する。"
                   "中でどう処理するかは書かない（機能ごとの詳細設計の担当）。")
    endpoints = sorted(m["endpoints"], key=_endpoint_order)
    lines += ["## API 構成", ""]
    rows = {}
    for e in endpoints:
        key = (ctx.app_label(e["app"]), e["protocol"])
        rows[key] = rows.get(key, 0) + 1
    lines += [table(["アプリケーション", "方式", "件数"], [[a, _PROTO.get(p, p), n] for (a, p), n in sorted(rows.items())])
              or "API は見つからなかった。", ""]

    lines += ["## 共通仕様", ""]
    items = [("ベース URL", common.get("baseUrl")), ("認証", common.get("auth")),
             ("認可", common.get("authorization")), ("ページング", common.get("pagination")),
             ("エラー形式", common.get("errorFormat")), ("バージョニング", common.get("versioning"))]
    rows = [[k, or_unk(v)] for k, v in items]
    rows.insert(2, ["未ログイン時の応答", _unauth_summary(ctx)])
    lines += [table(["項目", "内容"], rows), ""]
    for k, v in items:
        if not v:
            uv.add("共通仕様", k, "注釈未作成。ミドルウェア・共通ハンドラ・API クライアントを読んで書く")
    if common.get("headers"):
        lines += ["### 共通ヘッダー", "", table(["ヘッダー", "説明"],
                                            [[code(h["name"]), h.get("description")] for h in common["headers"]]), ""]
    uv.from_list("共通仕様", common.get("unverified"))

    lines += ["## エンドポイント一覧", ""]
    for proto in ("http", "graphql", "rpc", "websocket"):
        group = [e for e in endpoints if e["protocol"] == proto]
        if not group:
            continue
        if len({e["protocol"] for e in endpoints}) > 1:
            lines += [f"### {_PROTO[proto]}", ""]
        rows = []
        for e in group:
            rows.append([e["method"], code(e["path"]) + (" （Webhook）" if e.get("webhook") else ""),
                         _endpoint_summary(ctx, e), _endpoint_auth(ctx, e),
                         tables_cell(ctx, e.get("tables", [])), ctx.feature_names(e["id"])])
        lines += [table(["Method", "Path", "概要", "認証", "関連テーブル", "機能"], rows), ""]
    missing_summary(uv, "API", endpoints, ctx)

    lines += ["## エンドポイント定義", ""]
    for e in endpoints:
        lines += _endpoint_section(ctx, e, uv)
    lines += uv.render()
    return "\n".join(lines) + "\n"


_PROTO = {"http": "HTTP", "graphql": "GraphQL", "rpc": "RPC", "websocket": "WebSocket"}
_METHOD_ORDER = ["GET", "POST", "PUT", "PATCH", "DELETE"]
_ACTION_LABEL = {"find": "一覧を取得する", "findOne": "1 件を取得する", "create": "作成する",
                 "update": "更新する", "delete": "削除する"}


def _endpoint_order(e):
    """同じリソースの API がまとまるよう、パス → メソッドの順に並べる。"""
    path = e["path"]
    method = _METHOD_ORDER.index(e["method"]) if e["method"] in _METHOD_ORDER else len(_METHOD_ORDER)
    return (e["protocol"], path.rstrip("/").split("/:")[0].split("/{")[0], path, method)


def _endpoint_summary(ctx, e):
    note = ctx.note(e["id"])
    if note.get("summary"):
        return note["summary"]
    if e.get("spec") and e.get("operation") in _ACTION_LABEL:
        return f"{_ACTION_LABEL[e['operation']]}（Strapi の既定の API）"
    return UNK


def _unauth_summary(ctx):
    """個別の API の事実を集めた結果をそのまま書く（apiCommon は品質ゲートが作る）。個別より強く断定しない。"""
    agg = (ctx.model.get("apiCommon") or {}).get("unauthenticated") or {}
    if not agg.get("sessionEndpoints"):
        return "ログイン状態を確かめる API は見つからなかった"
    def names(ids, limit=5):
        shown = ", ".join(code(entity_label(ctx, i)) for i in ids[:limit])
        return shown + (f" ほか {len(ids) - limit} 件" if len(ids) > limit else "")

    parts = []
    for status, ids in sorted((agg.get("confirmed") or {}).items()):
        parts.append(f"未ログイン時に {status} を返すことをコードの分岐で確認できた API（{len(ids)} 件）: {names(ids)}")
    unknown = (agg.get("unresolved") or []) + (agg.get("conflict") or [])
    if unknown:
        parts.append(f"未ログイン時の挙動をリポジトリ内だけでは確定できない API（{len(unknown)} 件）: "
                     + names(unknown, limit=len(unknown)))
    return "ログイン状態を確かめる API " + str(agg["sessionEndpoints"]) + " 件のうち、" + "。".join(parts)


def _unauth_cell(e):
    a = e.get("authCheck") or {}
    if a.get("session") and a.get("status") in ("confirmed", "inferred"):
        return f"未ログイン時 {a['unauthenticatedStatus']}"
    return None


def _endpoint_auth(ctx, e):
    note = ctx.note(e["id"])
    if note.get("auth"):
        return note["auth"]
    if (ctx.ann.get("api") or {}).get("auth"):
        return "共通仕様のとおり"
    return UNK


def _endpoint_section(ctx, e, uv):
    note = ctx.note(e["id"])
    spec = e.get("spec") or {}
    title = f"{e['method']} {e['path']}" if e["protocol"] == "http" else e["path"]
    lines = [f"### {code(title)} — {_endpoint_summary(ctx, e)}", ""]
    meta = []
    if e.get("webhook"):
        meta.append("外部サービスから呼ばれる受け口（Webhook）")
    meta.append(f"認証: {_endpoint_auth(ctx, e)}")
    if _unauth_cell(e):
        meta.append(_unauth_cell(e) + f"（コードの分岐 {_ev_cell(e['authCheck'].get('evidence'))}）")
    if e["status"] == "unresolved":
        meta.append("一部を確定できていない（未確認一覧を参照）")
    lines += [" / ".join(meta), ""]
    resolved = e.get("resolvedRequest") or {}
    request = resolved.get("fields")
    responses = note.get("responses") or spec.get("responses")
    if spec and not (note.get("request") and note.get("responses")):
        lines += [f"入出力: {spec['source']}", ""]
    if resolved.get("source") == "runtime-schema":
        lines += [f"リクエストの根拠: 実行時の入力検証（{resolved['library']}） {_ev_cell(resolved['evidence'])}", ""]
    if request:
        lines += ["**リクエスト**", "", table(["区分", "名前", "型", "必須", "制約", "説明"], [
            [r.get("in"), code(r["name"]), r.get("type"), "はい" if r.get("required") else "いいえ",
             r.get("constraints"), r.get("description")] for r in request]), ""]
    elif note.get("noRequest"):
        lines += ["リクエストなし。", ""]
    for resp in responses or []:
        lines += [f"**レスポンス（{resp.get('status')}）** {resp.get('description') or ''}".rstrip(), ""]
        if resp.get("fields"):
            lines += [table(["フィールド", "型", "説明"],
                            [[code(f["name"]), f.get("type"), f.get("description")] for f in resp["fields"]]), ""]
    statuses = [(r.get("status"), r.get("description")) for r in responses or []]
    statuses += [(x.get("status"), x.get("condition")) for x in note.get("errors") or []]
    if statuses:
        lines += ["**ステータスコード**", "", table(["ステータス", "条件"], statuses), ""]
    if note.get("validation"):
        lines += ["**バリデーション**", "", table(["条件", "不正時"],
                                             [[v.get("rule"), v.get("onInvalid")] for v in note["validation"]]), ""]
    if e["id"] not in ctx.excluded:
        if not request and not note.get("noRequest"):
            uv.add(code(title), "リクエストの形", "注釈未作成。ハンドラとスキーマ定義を読んで書く", "not-found")
        if not responses:
            uv.add(code(title), "レスポンスの形", "注釈未作成。ハンドラと型定義を読んで書く", "not-found")
    if e.get("tables"):
        lines += [f"関連テーブル: {tables_cell(ctx, e['tables'])}", ""]
    uv.from_entity(ctx, e["id"], code(title))
    return lines


# --- screens.md -----------------------------------------------------------------

def render_screens(ctx):
    m = ctx.model
    uv = Unverified()
    lines = header(ctx, "画面", "画面の一覧・遷移・各画面の項目を示す。状態管理やコンポーネント構成は書かない。")
    screens = m["screens"]
    lines += ["## 概要", ""]
    rules = sorted({e.get("rule") for s in screens for e in s["evidence"] if e.get("rule")})
    lines += [f"- 画面 {len(screens)} 件。ルーティングの判定: {', '.join(rules) or NA}", ""]

    lines += ["## 画面一覧", ""]
    rows = []
    for s in screens:
        note = ctx.note(s["id"])
        api = ", ".join(entity_label(ctx, r["id"]) for r in s.get("calls", []))
        tbls = _screen_tables(ctx, s)
        rows.append([or_unk(note.get("name")), code(s["route"]), or_unk(note.get("summary")),
                     or_unk(note.get("auth")), api, tbls, ctx.feature_names(s["id"])])
    lines += [table(["画面名", "Route", "概要", "認証", "使用 API", "関連テーブル", "機能"], rows)
              or "画面は見つからなかった。", ""]
    missing_summary(uv, "画面", screens, ctx)

    lines += ["## 画面遷移図", ""]
    lines += _transition_diagrams(ctx, screens)

    lines += ["## 画面定義", ""]
    for s in screens:
        note = ctx.note(s["id"])
        lines += [f"### {note.get('name') or UNK} {code(s['route'])}", ""]
        lines += [or_unk(note.get("summary")), ""]
        if note.get("inputs"):
            lines += ["**入力項目**", "", table(["項目", "型", "必須", "制約"], [
                [i.get("name"), i.get("type"), "はい" if i.get("required") else "いいえ", i.get("constraints")]
                for i in note["inputs"]]), ""]
        if note.get("displays"):
            lines += ["**表示項目**", "", table(["項目", "出所"], [[d.get("name"), d.get("source")]
                                                            for d in note["displays"]]), ""]
        if note.get("actions"):
            lines += ["**操作**", "", table(["操作", "結果", "API", "遷移先"], [
                [x.get("label"), x.get("result"), entity_label(ctx, x["api"]) if x.get("api") else NA,
                 entity_label(ctx, x["to"]) if x.get("to") else NA] for x in note["actions"]]), ""]
        if not any(note.get(k) for k in ("inputs", "displays", "actions")) and s["id"] not in ctx.excluded:
            uv.add(code(s["route"]), "入力項目・表示項目・操作", "注釈未作成。画面のコンポーネントを読んで書く")
        links = s.get("links", [])
        if links:
            lines += ["遷移先: " + ", ".join(code(entity_label(ctx, r["id"])) for r in links), ""]
        uv.from_entity(ctx, s["id"], code(s["route"]))
    lines += uv.render()
    return "\n".join(lines) + "\n"


def _screen_tables(ctx, s):
    rels = {}
    for r in s.get("tables", []):
        rels.setdefault(r["id"], set()).update(r.get("operations", []))
    for call in s.get("calls", []):
        target = ctx.idx.get(call["id"]) or {}
        for r in target.get("tables", []):
            rels.setdefault(r["id"], set()).update(r.get("operations", []))
    return tables_cell(ctx, [{"id": k, "operations": sorted(v, key=OPERATIONS.index)} for k, v in sorted(rels.items())])


def _transition_diagrams(ctx, screens):
    if not screens:
        return ["画面が無いため遷移図は無い。", ""]
    ids = [s["id"] for s in screens]
    edges = [(s["id"], r["id"], ",".join(r.get("tags", []))) for s in screens for r in s.get("links", [])]
    if not edges:
        return ["コードから読み取れた画面遷移は無い（`router.push` / `Link href` / `redirect` などを探した）。", ""]
    groups = mm.pack(mm.components(ids, [(a, b) for a, b, _ in edges]))
    out = []
    for i, group in enumerate(groups, 1):
        members = set(group)
        if not any(a in members for a, _, _ in edges):
            continue
        if len(groups) > 1:
            out += [f"### 遷移図 {i}", ""]
        lines = ["flowchart TD"]
        for sid in group:
            s = ctx.idx[sid]
            name = ctx.note(sid).get("name")
            text = f"{name} {s['route']}" if name else s["route"]
            lines.append(f'    {mm.ident(sid, "s")}["{mm.label(text)}"]')
        for a, b, how in edges:
            if a in members and b in members:
                arrow = "-.->" if how == "redirect" else "-->"
                label = "・".join(_NAV_LABEL.get(h, h) for h in how.split(",")) if how else "遷移"
                lines.append(f"    {mm.ident(a, 's')} {arrow}|{mm.label(label)}| {mm.ident(b, 's')}")
        out += [mm.block(lines), ""]
    isolated = [ctx.idx[s]["route"] for s in ids if not any(s in (a, b) for a, b, _ in edges)]
    if isolated:
        out += ["遷移が読み取れなかった画面: " + ", ".join(code(r) for r in isolated), ""]
    return out


# --- integrations.md ------------------------------------------------------------

_NAV_LABEL = {"link": "リンク", "push": "移動", "replace": "置き換えて移動", "navigate": "移動",
              "redirect": "リダイレクト"}


def render_integrations(ctx):
    m = ctx.model
    uv = Unverified()
    lines = header(ctx, "外部連携",
                   "外部サービスとの送受信を、方向（送信 / 受信）ごとに示す。"
                   "認証情報は環境変数の名前と所在だけを書き、値は書かない。")
    lines += ["## 連携先一覧", ""]
    rows = []
    for i in m["integrations"]:
        note = ctx.note(i["id"])
        rows.append([i["name"], " / ".join(DIRECTION.get(d, d) for d in i["directions"]) or UNK,
                     or_unk(note.get("protocol") or ", ".join(i.get("protocols", [])) or None),
                     or_unk(note.get("auth")), ", ".join(code(v) for v in i["envVars"]) or NA,
                     ctx.feature_names(i["id"])])
    lines += [table(["連携先", "方向", "プロトコル", "認証方式", "環境変数名", "関連する機能"], rows)
              or "外部連携は見つからなかった（外部サービスの SDK の import と、外部ホストへの HTTP 呼び出しを探した）。", ""]
    missing_summary(uv, "外部連携", m["integrations"], ctx)

    if m["integrations"]:
        lines += ["## 連携定義", ""]
    for i in m["integrations"]:
        note = ctx.note(i["id"])
        webhooks = [e for e in m["endpoints"] if e.get("webhook")
                    and any(r["id"] == i["id"] for r in e.get("integrations", []))]
        files = sorted({e["file"] for e in i["evidence"]})
        rows = [
            ["目的", or_unk(note.get("purpose") or note.get("summary"))],
            ["方向", " / ".join(DIRECTION.get(d, d) for d in i["directions"]) or UNK],
            ["プロトコル", or_unk(note.get("protocol") or ", ".join(i.get("protocols", [])) or None)],
            ["接続先", ", ".join(code(h) for h in i.get("hosts", [])) or ("SDK: " + ", ".join(code(s) for s in i["sdk"]) if i["sdk"] else UNK)],
            ["認証方式", or_unk(note.get("auth"))],
            ["環境変数名", _env_cell(i)],
            ["送る項目", or_unk(note.get("dataSent"))],
            ["受け取る項目", or_unk(note.get("dataReceived"))],
            ["リトライ", or_unk(note.get("retry"))],
            ["タイムアウト", or_unk(note.get("timeout"))],
            ["失敗時", or_unk(note.get("failureHandling"))],
            ["Webhook の受け口", ", ".join(code(e["key"]) for e in webhooks) or NA],
            ["根拠", ", ".join(code(f) for f in files[:6]) + (f" ほか {len(files) - 6} 件" if len(files) > 6 else "")],
        ]
        lines += [f"### {i['name']}", "", table(["項目", "内容"], rows), ""]
        for k in ("purpose", "auth", "retry", "timeout", "failureHandling"):
            if not note.get(k) and not (k == "purpose" and note.get("summary")) and i["id"] not in ctx.excluded:
                uv.add(i["name"], _INTEG_FIELD[k], "注釈未作成。クライアントの生成箇所と呼び出し箇所を読んで書く")
        uv.from_entity(ctx, i["id"], i["name"])
    lines += uv.render()
    return "\n".join(lines) + "\n"


def _env_cell(i):
    """確定した環境変数だけを、どこで結び付けたかと一緒に書く。名前が似ているだけのものは未確定へ。"""
    parts = []
    for e in i.get("envVarDetails", []):
        how = {"client-init": "初期化", "call-site": "呼び出し", "init-variable": "引数（変数経由）"}.get(
            e["source"], e["source"])
        parts.append(f"{code(e['name'])}（{how}）")
    return ", ".join(parts) or NA


_INTEG_FIELD = {"purpose": "目的", "auth": "認証方式", "retry": "リトライ", "timeout": "タイムアウト",
                "failureHandling": "失敗時の扱い"}


# --- flow / detail ---------------------------------------------------------------

def _related_table(ctx, f):
    rows = [["入口", ", ".join(code(ctx.idx[e]["key"]) for e in f["entrypoints"] if e in ctx.idx)],
            ["画面", labels(ctx, f["screens"])], ["API", labels(ctx, f["endpoints"])],
            ["テーブル", tables_cell(ctx, f["tables"])], ["外部連携", labels(ctx, f["integrations"])],
            ["ジョブ・キュー", labels(ctx, f["jobs"])], ["イベント", labels(ctx, f["events"])]]
    return table(["項目", "内容"], rows)


def render_flow(ctx, f, spec):
    uv = Unverified()
    lines = header(ctx, f"{feature_name(f)} — 処理概要",
                   "この機能に何を入れると、何をして、何が出るかを示す。内部の実装は書かない（詳細設計の担当）。")
    lines += [or_unk(spec.get("summary") or f.get("summary")), ""]
    lines += ["## 入力", "", table(["入力", "説明"], [[i.get("name"), i.get("description")]
                                                for i in spec.get("inputs") or []]) or UNK, ""]
    lines += ["## 処理の要点", ""]
    steps = spec.get("steps") or []
    lines += [f"{n}. {s}" for n, s in enumerate(steps, 1)] or [UNK]
    lines.append("")
    lines += ["## 出力", "", table(["出力", "説明"], [[o.get("name"), o.get("description")]
                                                for o in spec.get("outputs") or []]) or UNK, ""]
    lines += ["## 失敗したとき", "", table(["条件", "利用者から見える結果"],
                                          [[x.get("condition"), x.get("result")] for x in spec.get("failures") or []]) or UNK, ""]
    lines += ["## 図", "", _flow_diagram(spec), ""]
    lines += ["## 関連", "", _related_table(ctx, f), ""]
    files = f["implementationFiles"]
    if files:
        lines += ["関連ファイル: " + ", ".join(code(x) for x in files), ""]
    for key, what in (("inputs", "入力"), ("steps", "処理の要点"), ("outputs", "出力")):
        if not spec.get(key):
            uv.add(feature_name(f), what, "注釈未作成")
    uv.from_list(feature_name(f), spec.get("unverified"))
    lines += uv.render()
    return "\n".join(lines) + "\n"


def _flow_diagram(spec):
    lines = ["flowchart LR"]
    ins = (spec.get("inputs") or [])[:3]
    outs = (spec.get("outputs") or [])[:3]
    steps = (spec.get("steps") or [])[:5]
    if not (ins or steps or outs):
        return UNK
    prev = []
    for i, x in enumerate(ins):
        lines.append(f'    in{i}["{mm.label(x.get("name"))}"]')
        prev.append(f"in{i}")
    for i, s in enumerate(steps):
        lines.append(f'    st{i}["{mm.label(s)}"]')
        for p in prev:
            lines.append(f"    {p} -->|次へ| st{i}")
        prev = [f"st{i}"]
    for i, x in enumerate(outs):
        lines.append(f'    out{i}(["{mm.label(x.get("name"))}"])')
        for p in prev:
            lines.append(f"    {p} -->|出力| out{i}")
    return mm.block(lines)


def render_detail(ctx, f, d):
    uv = Unverified()
    name = feature_name(f)
    lines = header(ctx, f"{name} — 詳細設計",
                   "この機能が内部でどう動いているかを、入口から永続化まで示す。改修する人が、コードを開く前に"
                   "全体像を掴むための資料。")

    lines += ["## 概要", "", or_unk(d.get("summary") or f.get("summary")), ""]
    lines += [table(["項目", "内容"], [["どこから始まるか", or_unk(d.get("start"))],
                                     ["どこで終わるか", or_unk(d.get("end"))]]), ""]
    if not d.get("summary") and not f.get("summary"):
        uv.add(name, "機能の概要", "注釈未作成")

    lines += ["## 入口", ""]
    eps = [ctx.idx[e] for e in f["entrypoints"] if e in ctx.idx]
    lines += [table(["種別", "入口", "定義"], [[_EP_LABEL.get(e["type"], e["type"]), code(e["key"]),
                                             code(e["file"]) + (f":{e['line']}" if e.get("line") else "")
                                             if e.get("file") else NA] for e in eps]), ""]

    # 認証・認可・ロール・所有者は次の「認証・権限」に出す。ここは業務上の前提だけ（同じ表を 2 回出さない）
    lines += ["## 前提条件", ""]
    pre = d.get("preconditions") or []
    business = [p for p in pre if p.get("kind") not in AUTH_KINDS]
    if business:
        lines += [table(["種類", "内容", "根拠"], [[PRECONDITION_LABEL.get(p.get("kind"), p.get("kind")),
                                                p.get("description"), _ev_cell(p.get("evidence"))]
                                               for p in business]), ""]
    elif pre:
        lines += ["認証・権限のほかには無い。", ""]
    else:
        lines += [UNK, ""]
        uv.add(name, "前提条件（認証・認可・所有者・業務上の前提）", "注釈未作成")

    lines += ["## 認証・権限", ""]
    auth = [p for p in pre if p.get("kind") in AUTH_KINDS]
    if auth:
        lines += [table(["種類", "内容", "根拠"], [[PRECONDITION_LABEL[p["kind"]], p.get("description"),
                                                _ev_cell(p.get("evidence"))] for p in auth]), ""]
    else:
        lines += [UNK, ""]
        uv.add(name, "認証・権限（誰が実行できるか）", "注釈未作成。ミドルウェア・ハンドラ冒頭の検査を読んで書く",
               "not-found")

    lines += ["## 入力", ""]
    lines += [table(["入力", "型", "必須", "制約", "出所"], [
        [code(i.get("name")), i.get("type"), "はい" if i.get("required") else "いいえ", i.get("constraints"),
         i.get("source")] for i in d.get("inputs") or []]) or UNK, ""]

    lines += ["## バリデーション", ""]
    lines += [table(["条件", "不正時の動き", "利用者から見える結果"], [
        [v.get("condition"), v.get("onInvalid"), v.get("userVisible")] for v in d.get("validations") or []])
        or UNK, ""]

    lines += ["## シーケンス", ""]
    seq = d.get("sequence") or {}
    if seq.get("participants") and seq.get("steps"):
        lines += [mm.sequence(seq["participants"], seq["steps"]), ""]
    else:
        lines += [UNK, ""]
        uv.add(name, "処理の順序（シーケンス）", "注釈未作成")

    lines += ["## 分岐とエラー", ""]
    lines += [table(["条件", "分岐・結果", "ステータス", "利用者から見える結果"], [
        [b.get("condition"), b.get("result"), b.get("status"), b.get("userVisible")]
        for b in d.get("branches") or []]) or UNK, ""]

    lines += ["## データへの影響", ""]
    impact = d.get("dataImpact") or []
    if impact:
        lines += [table(["テーブル", "操作", "変わるカラム", "意味"], [
            [code(entity_label(ctx, x["table"])), x.get("operation"), ", ".join(x.get("columns") or []),
             x.get("meaning")] for x in impact]), ""]
    else:
        lines += ["自動解析で見つけた操作（意味は未記入）:", "",
                  table(["テーブル", "操作"], [[code(entity_label(ctx, t["id"])), ", ".join(t["operations"]) or "参照のみ"]
                                            for t in f["tables"]]) or NA, ""]
        if f["tables"]:
            uv.add(name, "データへの影響の意味（どのカラムが何のために変わるか）", "注釈未作成")
    model_ops = {(t["id"], o) for t in f["tables"] for o in t["operations"]}
    for x in impact:
        if (x.get("table"), x.get("operation")) not in model_ops and f["tables"]:
            uv.add(name, f"{entity_label(ctx, x.get('table'))} の {x.get('operation')} は自動解析では見つからなかった",
                   "入口から import を 3 段までたどった範囲。注釈の根拠を人が確かめる")

    for key, title in (("transaction", "トランザクション"), ("idempotency", "冪等性")):
        lines += [f"## {title}", ""]
        block = d.get(key)
        if block:
            rows = [["状態", STATUS_LABEL.get(block.get("status"), UNK)]]
            for k, label_ in (("boundary", "境界"), ("rollback", "ロールバック"), ("nested", "入れ子"),
                              ("savepoint", "セーブポイント"), ("mechanism", "仕組み")):
                if block.get(k):
                    rows.append([label_, block[k]])
            rows.append(["根拠", _ev_cell(block.get("evidence"))])
            lines += [table(["項目", "内容"], rows), ""]
            if block.get("status") == "unverified":
                uv.add(name, title, block.get("searched"))
        else:
            lines += [UNK, ""]
            uv.add(name, title, "注釈未作成")

    lines += ["## 同時実行・ロック", ""]
    conc = d.get("concurrency")
    if conc:
        rows = [["状態", STATUS_LABEL.get(conc.get("status"), UNK)]]
        if conc.get("description"):
            rows.append(["内容", conc["description"]])
        if conc.get("searched"):
            rows.append(["調べた範囲", conc["searched"]])
        rows.append(["根拠", _ev_cell(conc.get("evidence"))])
        lines += [table(["項目", "内容"], rows), ""]
        mechanisms = conc.get("mechanisms") or []
        if mechanisms:
            lines += [table(["仕組み", "内容", "根拠"], [
                [CONCURRENCY_LABEL.get(x.get("kind"), x.get("kind")), x.get("description"),
                 _ev_cell(x.get("evidence"))] for x in mechanisms]), ""]
        if conc.get("status") == "unverified":
            uv.add(name, "同時実行・ロック", conc.get("searched"))
    else:
        lines += ["確認できた範囲では記述なし。", ""]

    st = d.get("stateTransition")
    if st:
        lines += ["## 状態遷移", "", f"対象: {code(st.get('entity'))}", "",
                  mm.state(st.get("states") or [], st.get("transitions") or []), ""]

    lines += ["## 外部連携", ""]
    calls = d.get("externalCalls") or []
    if calls:
        lines += [table(["連携先", "送るもの", "受け取るもの", "リトライ", "タイムアウト", "失敗時"], [
            [entity_label(ctx, x.get("integration")), x.get("request"), x.get("response"), or_unk(x.get("retry")),
             or_unk(x.get("timeout")), or_unk(x.get("failure"))] for x in calls]), ""]
    elif f["integrations"]:
        lines += ["自動解析で見つけた連携先: " + labels(ctx, f["integrations"]) + "（送受信の内容は未記入）", ""]
        uv.add(name, "外部連携の送受信・リトライ・タイムアウト・失敗時", "注釈未作成")
    else:
        lines += ["なし（自動解析で連携先が見つからなかった）。", ""]

    lines += ["## 副作用", ""]
    effects = d.get("sideEffects") or []
    auto = [f"イベント発行 {entity_label(ctx, e)}" for e in f["events"]] + \
           [f"キュー投入 {entity_label(ctx, j)}" for j in f["jobs"]
            if (ctx.idx.get(j) or {}).get("kind") == "queue-consumer" and not any(
                ep for ep in f["entrypoints"] if (ctx.idx.get(ep) or {}).get("target") == j)]
    if effects:
        lines += [table(["種類", "内容", "根拠"], [[SIDE_EFFECT_LABEL.get(x.get("kind"), x.get("kind")),
                                                x.get("description"), _ev_cell(x.get("evidence"))]
                                               for x in effects]), ""]
    elif auto:
        lines += ["自動解析で見つけたもの: " + "、".join(auto), ""]
    else:
        lines += ["確認できた範囲では無い。", ""]

    lines += ["## 注意点", "",
              "コードから読み取れる、改修・運用で気を付けること。仕様の良し悪しや不具合の判定ではない。", ""]
    risks = d.get("risks") or []
    if risks:
        lines += [table(["内容", "根拠"], [[r.get("description"), _ev_cell(r.get("evidence"))] for r in risks]), ""]
    else:
        lines += ["確認できた範囲では記述なし。", ""]

    lines += ["## 関連ファイル", ""]
    files = d.get("files") or []
    if files:
        lines += [table(["役割", "パス"], [[x.get("role"), code(x.get("path"))] for x in files]), ""]
    else:
        lines += [table(["パス"], [[code(x)] for x in f["implementationFiles"]]) or NA, ""]

    lines += ["## 根拠", "", f"機能 ID: {code(f['id'])}。各項目の根拠は `.source-docs/evidence.json` を参照。", ""]
    for eid in f.get("pendingFrom", []):
        uv.pending_of(ctx, eid, code(entity_label(ctx, eid)))
    uv.from_list(name, d.get("unverified"))
    lines += uv.render()
    return "\n".join(lines) + "\n"


def _ev_cell(evidence):
    if not evidence:
        return NA
    out = []
    for ev in evidence:
        if isinstance(ev, str):
            out.append(code(ev))
        else:
            out.append(code(ev["file"] + (f":{ev['line']}" if ev.get("line") else "")))
    return ", ".join(out)


__all__ = ["DOCS", "Ctx", "render_overview", "render_features", "render_database", "render_api",
           "render_screens", "render_integrations", "render_flow", "render_detail"]
