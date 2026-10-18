"""バックエンドの基盤ごとの規約。Strapi と Supabase。

規約によって「コードに書かれていないが存在する」もの（Strapi の自動カラム・
コアルータが作る CRUD）は、`rule` に `convention` を含めて区別する。
"""

import json
import re

from .base import Adapter, line_of, segments, strip_comments
from .base import add_column, column
from .strapi_spec import core_spec, strapi_major

# --- Strapi --------------------------------------------------------------------

RE_CORE_ROUTER = re.compile(r"""createCoreRouter\(\s*['"](api::[\w.-]+)['"]\s*(?:,\s*\{([^;]*)\})?""", re.S)
RE_ROUTE_METHOD = re.compile(r"\bmethod\s*:\s*['\"](\w+)['\"]")
RE_STRAPI_USAGE = re.compile(
    r"""strapi\s*\.\s*(?:entityService\s*\.\s*(\w+)\(\s*['"](api::[\w.-]+)['"]|"""
    r"""documents\(\s*['"](api::[\w.-]+)['"]\s*\)\s*\.\s*(\w+)\(|"""
    r"""db\s*\.\s*query\(\s*['"](api::[\w.-]+)['"]\s*\)\s*\.\s*(\w+)\()""")
LIFECYCLE_HOOK = re.compile(
    r"\b((?:before|after)(?:Create|Update|Delete|FindOne|FindMany|Count|CreateMany|UpdateMany|DeleteMany))\s*\(")
STRAPI_OPS = {"create": "CREATE", "createMany": "CREATE", "update": "UPDATE", "updateMany": "UPDATE",
              "publish": "UPDATE", "unpublish": "UPDATE", "delete": "DELETE", "deleteMany": "DELETE",
              "findMany": "READ", "findOne": "READ", "findFirst": "READ", "findPage": "READ",
              "count": "READ", "findWithCount": "READ"}
CORE_ACTIONS = [  # (アクション, メソッド, 個別か, 操作)
    ("find", "GET", False, "READ"), ("findOne", "GET", True, "READ"),
    ("create", "POST", False, "CREATE"), ("update", "PUT", True, "UPDATE"),
    ("delete", "DELETE", True, "DELETE"),
]
CONVENTION_NOTE = "Strapi が自動で作る"
SOURCE = "strapi"


class Strapi(Adapter):
    name = "strapi"

    def applies(self, app):
        return app.has_dep("@strapi/strapi")

    def collect(self, app, b):
        self.content_types = {}
        self.major = None
        manifest = "/".join(p for p in (app.root_rel, "package.json") if p)
        try:
            deps = (json.loads(app.read(manifest) or "{}").get("dependencies") or {})
            self.major = strapi_major(deps.get("@strapi/strapi"))
        except ValueError:
            pass
        for rel in app.files_matching(r"^src/(api|extensions)/[^/]+/content-types/[^/]+/schema\.json$"):
            self._content_type(app, b, rel)
        routed = set()
        for rel in app.files_matching(r"^src/api/[^/]+/routes/[^/]+\.(js|ts)$"):
            routed |= self._routes(app, b, rel)
        # ルートファイルが無い content-type は、API が公開されているかをコードから確定できない
        for uid, table in self.content_types.items():
            if uid.startswith("api::") and uid not in routed:
                b.add_unsupported(app.ref, table["evidence"][0]["file"], "endpoint",
                                  f"{uid} にルート定義（createCoreRouter）が無い。API が公開されているかを確定できない")
        for rel in app.files_matching(r"^src/api/[^/]+/content-types/[^/]+/lifecycles\.(js|ts)$"):
            self._lifecycles(app, b, rel)
        jobs_before = len(b.items("jobs"))
        for rel in app.files_matching(r"^config/(cron-tasks|server)\.(js|ts)$"):
            self._cron(app, b, rel)
        if len(b.items("jobs")) == jobs_before:
            for rel in app.files_matching(r"^config/server\.(js|ts)$"):
                if re.search(r"cron\s*:\s*\{[^}]*enabled\s*:\s*(?:true|env\.bool)", strip_comments(app.read(rel))):
                    b.add_unsupported(app.ref, rel, "worker",
                                      "cron が有効だが、タスクの定義を読み取れない（外部プラグインや変数経由の可能性）")

    def _uid(self, app, rel):
        parts = app.app_rel(rel).split("/")
        return f"api::{parts[2]}.{parts[4]}" if parts[1] == "api" else f"plugin::{parts[2]}.{parts[4]}"

    def _content_type(self, app, b, rel):
        try:
            data = json.loads(app.read(rel) or "{}")
        except ValueError:
            b.add_unsupported(app.ref, rel, "table", "schema.json を JSON として読めない")
            return
        info = data.get("info") or {}
        uid = self._uid(app, rel)
        singular = info.get("singularName") or uid.rsplit(".", 1)[-1]
        plural = info.get("pluralName") or singular + "s"
        name = data.get("collectionName") or plural
        ev = app.ev(rel, 1, "strapi.content-type")
        table = b.add_table(app.ref, name, ev, aliases=(uid, singular, plural))
        table["strapi"] = {"uid": uid, "kind": data.get("kind"), "singularName": singular,
                           "pluralName": plural}
        table["_schema"] = data
        self.content_types[uid] = table
        add_column(table, column("id", "integer", nullable=False, primary=True, note=CONVENTION_NOTE),
                   SOURCE)["status"] = "inferred"
        for attr, spec in (data.get("attributes") or {}).items():
            if not isinstance(spec, dict):
                continue
            type_ = spec.get("type")
            if type_ == "relation":
                table["ormRelations"].append({"name": attr, "relation": spec.get("relation"),
                                              "target": spec.get("target"), "columns": [],
                                              "references": {"table": spec.get("target"), "columns": []},
                                              "source": SOURCE})
                continue
            if type_ in ("component", "dynamiczone"):
                type_ = f"{type_}({spec.get('component') or ','.join(spec.get('components') or [])})"
            elif type_ == "enumeration":
                type_ = "enumeration(" + ", ".join(spec.get("enum") or []) + ")"
            add_column(table, column(
                attr, type_, nullable=not spec.get("required", False),
                default=json.dumps(spec["default"], ensure_ascii=False) if "default" in spec else None,
                unique=bool(spec.get("unique"))), SOURCE)
        extra = ["created_at", "updated_at", "created_by_id", "updated_by_id"]
        if (data.get("options") or {}).get("draftAndPublish"):
            extra.insert(2, "published_at")
        for c in extra:
            # 規約で作られるカラム。schema.json には現れないので inferred
            add_column(table, column(c, "datetime" if c.endswith("_at") else "integer",
                                     nullable=True, note=CONVENTION_NOTE), SOURCE)["status"] = "inferred"

    def _controller(self, app, api, name):
        for ext in ("ts", "js"):
            rel = "/".join(p for p in (app.root_rel, "src/api", api, "controllers", f"{name}.{ext}") if p)
            if app.repo.exists(rel):
                return rel
        return None

    def _routes(self, app, b, rel):
        """ルート定義を読み、ルータを持つ content-type の uid の集合を返す。"""
        text = strip_comments(app.read(rel))
        api = app.app_rel(rel).split("/")[2]
        routed = set()
        for m in RE_CORE_ROUTER.finditer(text):
            uid = m.group(1)
            table = self.content_types.get(uid)
            if table is None:
                b.add_unsupported(app.ref, rel, "endpoint", f"{uid} の content-type が見つからない")
                continue
            routed.add(uid)
            opts = m.group(2) or ""
            only = re.search(r"only\s*:\s*\[([^\]]*)\]", opts)
            except_ = re.search(r"except\s*:\s*\[([^\]]*)\]", opts)
            allowed = set(re.findall(r"['\"](\w+)['\"]", only.group(1))) if only else None
            denied = set(re.findall(r"['\"](\w+)['\"]", except_.group(1))) if except_ else set()
            single = table["strapi"]["kind"] == "singleType"
            base = "/api/" + (table["strapi"]["singularName"] if single else table["strapi"]["pluralName"])
            ctrl = self._controller(app, api, uid.rsplit(".", 1)[-1])
            ctext = app.read(ctrl) if ctrl else ""
            for action, method, by_id, op in CORE_ACTIONS:
                if (allowed is not None and action not in allowed) or action in denied:
                    continue
                if single and action in ("findOne", "create"):
                    continue
                path = base + ("/:id" if by_id and not single else "")
                # コアのアクションを上書きしていれば、そのメソッドを読む。していなければ
                # 規約どおりの CRUD なので、コントローラの他の処理を混ぜない
                override = re.search(r"(?<![\w$])(?:async\s+)?" + action + r"\s*\(", ctext)
                location = None
                if override:
                    location = app.loc(ctrl, override.start(), _next_method(ctext, override.end()),
                                       line_of(ctext, override.start()))
                ep = b.add_endpoint(app.ref, method, path,
                                    app.ev(rel, line_of(text, m.start()), "strapi.core-router.convention"),
                                    location=location, operation=action)
                if not override:
                    spec = core_spec(action, table["_schema"].get("attributes") or {}, table["_schema"],
                                     self.major)
                    if spec:
                        ep["spec"] = spec
                # コアのコントローラが既定の CRUD でテーブルを操作するのは規約。コードで確かめた関係ではない
                b.relate(ep, "tables", table["_uid"],
                         app.ev(rel, line_of(text, m.start()), "strapi.core-controller.convention"),
                         operation=op, status="inferred")
        for m, obj in _route_objects(text):
            path = re.search(r"path\s*:\s*['\"](/[^'\"]*)['\"]", obj)
            handler = re.search(r"handler\s*:\s*['\"]([\w.:-]+)['\"]", obj)
            if not path:
                continue
            location = None
            if handler:
                ctrl_name, _, action = handler.group(1).rsplit(":", 1)[-1].rpartition(".")
                ctrl = self._controller(app, api, ctrl_name.rsplit(".", 1)[-1])
                if ctrl:
                    ctext = app.read(ctrl)
                    am = re.search(r"\b" + re.escape(action) + r"\s*(?:\(|:\s*(?:async\s*)?\()", ctext)
                    start = am.start() if am else 0
                    location = app.loc(ctrl, start, None if not am else _next_method(ctext, am.end()),
                                       line_of(ctext, start))
            b.add_endpoint(app.ref, m.group(1), "/api" + path.group(1),
                           app.ev(rel, line_of(text, m.start()), "strapi.custom-route"),
                           location=location, operation=handler.group(1) if handler else None)
        return routed

    def _lifecycles(self, app, b, rel):
        text = strip_comments(app.read(rel))
        parts = app.app_rel(rel).split("/")
        uid = f"api::{parts[2]}.{parts[4]}"
        table = self.content_types.get(uid)
        hooks = [(m.group(1), m.start()) for m in LIFECYCLE_HOOK.finditer(text)]
        spans = {s: (s, e) for s, e in segments(text, [s for _, s in hooks])}
        for hook, start in hooks:
            s, e = spans[start]
            b.add_entrypoint(app.ref, "lifecycle-hook", f"{parts[4]}.{hook}",
                             app.ev(rel, line_of(text, start), "strapi.lifecycle"),
                             target=table["_uid"] if table else None,
                             location=app.loc(rel, s, e, line_of(text, start)))

    def _cron(self, app, b, rel):
        text = strip_comments(app.read(rel))
        tasks = list(re.finditer(r"['\"]?([\w-]+)['\"]?\s*:\s*\{\s*task\s*:", text))
        for m in re.finditer(r"""rule\s*:\s*['"]([^'"]+)['"]""", text):
            before = [t for t in tasks if t.start() < m.start()]
            name = before[-1].group(1) if before else f"line{line_of(text, m.start())}"
            start = before[-1].start() if before else m.start()
            b.add_job(app.ref, "cron", name, app.ev(rel, line_of(text, m.start()), "strapi.cron"),
                      schedule=m.group(1), location=app.loc(rel, start, None, line_of(text, start)))
        for m in re.finditer(r"""['"]((?:[\d*/,\-?]+\s+){4,5}[\d*/,\-?]+)['"]\s*:""", text):
            b.add_job(app.ref, "cron", m.group(1), app.ev(rel, line_of(text, m.start()), "strapi.cron"),
                      schedule=m.group(1),
                      location=app.loc(rel, m.start(), None, line_of(text, m.start())))

    def usages(self, app, rel, text):
        out = []
        for m in RE_STRAPI_USAGE.finditer(text):
            if m.group(2):
                uid, method = m.group(2), m.group(1)
            elif m.group(3):
                uid, method = m.group(3), m.group(4)
            else:
                uid, method = m.group(5), m.group(6)
            op = STRAPI_OPS.get(method)
            if op:
                out.append({"kind": "table", "alias": uid, "op": op, "offset": m.start()})
        return out


def _route_objects(text):
    """カスタムルートの 1 件ずつ（method を含む `{ ... }`）。`config: { policies: [] }` のような入れ子があっても切らない。"""
    out = []
    for m in RE_ROUTE_METHOD.finditer(text):
        depth, start = 0, None
        for i in range(m.start(), -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                if depth == 0:
                    start = i
                    break
                depth -= 1
        if start is None:
            continue
        depth = 0
        for j in range(start, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append((m, text[start:j + 1]))
                    break
    return out


def _next_method(text, pos):
    m = re.search(r"\n\s{0,4}(?:async\s+)?\w+\s*(?:\(|:\s*(?:async\s*)?\()", text[pos:])
    return pos + m.start() if m else None
