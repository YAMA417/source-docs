"""バックエンドの基盤ごとの規約。Strapi と Supabase。

規約によって「コードに書かれていないが存在する」もの（Strapi の自動カラム・
コアルータが作る CRUD）は、`rule` に `convention` を含めて区別する。
"""

import json
import re

from .base import Adapter, line_of, segments, strip_comments
from .file_routing import RE_REQ_METHOD, HTTP_METHODS
from .orm_js import column

# --- Strapi --------------------------------------------------------------------

RE_CORE_ROUTER = re.compile(r"""createCoreRouter\(\s*['"](api::[\w.-]+)['"]\s*(?:,\s*\{([^;]*)\})?""", re.S)
RE_ROUTE_OBJ = re.compile(r"\{[^{}]*?method\s*:\s*['\"](\w+)['\"][^{}]*?\}", re.S)
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
CONVENTION_NOTE = "Strapi の規約で自動生成（schema.json には現れない）"


class Strapi(Adapter):
    name = "strapi"

    def applies(self, app):
        return app.has_dep("@strapi/strapi")

    def collect(self, app, b):
        self.content_types = {}
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
        for rel in app.files_matching(r"^config/(cron-tasks|server)\.(js|ts)$"):
            self._cron(app, b, rel)

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
        self.content_types[uid] = table
        table["columns"].append(column("id", "integer", nullable=False, primary=True,
                                       note=CONVENTION_NOTE))
        table["primaryKey"] = ["id"]
        table.setdefault("relations", [])
        for attr, spec in (data.get("attributes") or {}).items():
            if not isinstance(spec, dict):
                continue
            type_ = spec.get("type")
            if type_ == "relation":
                table["relations"].append({"attribute": attr, "relation": spec.get("relation"),
                                           "target": spec.get("target")})
                continue
            if type_ in ("component", "dynamiczone"):
                type_ = f"{type_}({spec.get('component') or ','.join(spec.get('components') or [])})"
            elif type_ == "enumeration":
                type_ = "enumeration(" + ", ".join(spec.get("enum") or []) + ")"
            table["columns"].append(column(
                attr, type_, nullable=not spec.get("required", False),
                default=json.dumps(spec["default"], ensure_ascii=False) if "default" in spec else None,
                unique=bool(spec.get("unique"))))
        extra = ["created_at", "updated_at", "created_by_id", "updated_by_id"]
        if (data.get("options") or {}).get("draftAndPublish"):
            extra.insert(2, "published_at")
        for c in extra:
            table["columns"].append(column(c, "datetime" if c.endswith("_at") else "integer",
                                           nullable=True, note=CONVENTION_NOTE))

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
                b.relate(ep, "tables", table["_uid"],
                         app.ev(rel, line_of(text, m.start()), "strapi.core-controller.convention"),
                         operation=op)
        for m in RE_ROUTE_OBJ.finditer(text):
            obj = m.group(0)
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


def _next_method(text, pos):
    m = re.search(r"\n\s{0,4}(?:async\s+)?\w+\s*(?:\(|:\s*(?:async\s*)?\()", text[pos:])
    return pos + m.start() if m else None


# --- Supabase ------------------------------------------------------------------

RE_SB_FROM = re.compile(r"""(storage\s*\.\s*)?\.?\bfrom\(\s*['"]([\w.]+)['"]\s*\)""")
RE_SB_OP = re.compile(r"\.\s*(select|insert|update|upsert|delete)\s*\(")
SB_OPS = {"select": "READ", "insert": "CREATE", "update": "UPDATE", "upsert": "UPDATE", "delete": "DELETE"}
RE_SB_INVOKE = re.compile(r"""functions\s*\.\s*invoke\(\s*['"]([\w-]+)['"]""")


class Supabase(Adapter):
    name = "supabase"

    def applies(self, app):
        return app.has_dep("@supabase/supabase-js", "supabase", "@supabase/ssr") or any(
            app.app_rel(f).startswith("supabase/") for f in app.files)

    def collect(self, app, b):
        for rel in app.files_matching(r"^supabase/functions/[^/_][^/]*/index\.(ts|js)$"):
            name = app.app_rel(rel).split("/")[2]
            text = app.read(rel)
            methods = sorted({(m.group(1) or m.group(2)).upper()
                              for m in RE_REQ_METHOD.finditer(text)} & set(HTTP_METHODS))
            for method in methods or ["ANY"]:
                b.add_endpoint(app.ref, method, f"/functions/v1/{name}",
                               app.ev(rel, 1, "supabase.edge-function"),
                               location=app.loc(rel, 0, len(text), 1))

    def usages(self, app, rel, text):
        if not rel.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte")):
            return []
        code = strip_comments(text)
        out = []
        for m in RE_SB_FROM.finditer(code):
            if m.group(1):
                out.append({"kind": "integration", "key": "supabase-storage",
                            "name": "Supabase Storage", "offset": m.start()})
                continue
            tail = code[m.end():m.end() + 300]
            op = RE_SB_OP.match(tail) or RE_SB_OP.search(tail.split(";")[0])
            if op:
                out.append({"kind": "table", "alias": m.group(2), "op": SB_OPS[op.group(1)],
                            "offset": m.start()})
        for m in RE_SB_INVOKE.finditer(code):
            out.append({"kind": "call", "method": "POST", "path": f"/functions/v1/{m.group(1)}",
                        "methodSource": "supabase.invoke", "offset": m.start()})
        if re.search(r"\.auth\s*\.\s*(signIn|signUp|signOut|getUser|getSession|onAuthStateChange|admin)", code):
            out.append({"kind": "integration", "key": "supabase-auth", "name": "Supabase Auth",
                        "offset": 0})
        return out

