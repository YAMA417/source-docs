"""Python と Go の検出器。対応範囲は狭い。新しい言語を足すときの見本でもある。

- Python: FastAPI のルート / SQLAlchemy のモデル / Celery のタスク / import の解決
- Go: Gin のルート（Group による前置パスを含む）
"""

import re

from .base import Adapter, dirname, join, line_of, segments

# --- Python --------------------------------------------------------------------

RE_PY_ROUTE = re.compile(
    r"""@(\w+)\.(get|post|put|patch|delete|api_route)\(\s*['"](/[^'"]*)['"]""")
RE_PY_ROUTER_PREFIX = re.compile(r"""\b(\w+)\s*=\s*APIRouter\([^)]*?prefix\s*=\s*['"](/[^'"]*)['"]""")
RE_PY_INCLUDE = re.compile(
    r"""\binclude_router\(\s*([\w.]+)\s*(?:,[^)]*?prefix\s*=\s*['"](/[^'"]*)['"])?""")
RE_PY_DEF = re.compile(r"^(?:async\s+)?def\s+(\w+)", re.M)
RE_SA_MODEL = re.compile(r"^class\s+(\w+)\(([^)]*)\)\s*:", re.M)
RE_SA_TABLENAME = re.compile(r"""__tablename__\s*=\s*['"](\w+)['"]""")
RE_SA_COLUMN = re.compile(
    r"^\s+(\w+)\s*(?::\s*[^=\n]+)?=\s*(?:Column|mapped_column|db\.Column)\((.*)\)\s*$", re.M)
RE_CELERY_TASK = re.compile(r"@(?:\w+\.)?(?:task|shared_task)\b[^\n]*\n\s*(?:async\s+)?def\s+(\w+)")
RE_PY_FROM_IMPORT = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+([\w, ()]+)", re.M)
SA_OPS = [(r"\bsession\.add\(\s*(\w+)\(", "CREATE"), (r"\bselect\(\s*(\w+)\b", "READ"),
          (r"\.query\(\s*(\w+)\b", "READ"), (r"\bupdate\(\s*(\w+)\b", "UPDATE"),
          (r"\bdelete\(\s*(\w+)\b", "DELETE"), (r"\b(\w+)\.objects\.create\(", "CREATE"),
          (r"\b(\w+)\.objects\.(?:filter|get|all)\(", "READ")]


class PythonWeb(Adapter):
    name = "python"

    def applies(self, app):
        return any(f.endswith(".py") for f in app.files)

    def collect(self, app, b):
        py = [f for f in app.files if f.endswith(".py")]
        include_prefix = {}
        for rel in py:
            for m in RE_PY_INCLUDE.finditer(app.read(rel)):
                if m.group(2):
                    include_prefix[m.group(1).split(".")[0]] = m.group(2)
        for rel in py:
            text = app.read(rel)
            if app.has_dep("fastapi"):
                self._fastapi(app, b, rel, text, include_prefix)
            if app.has_dep("sqlalchemy", "flask-sqlalchemy", "sqlmodel"):
                self._sqlalchemy(app, b, rel, text)
            if app.has_dep("celery"):
                for m in RE_CELERY_TASK.finditer(text):
                    start = m.start()
                    end = RE_PY_DEF.search(text, m.end())
                    b.add_job(app.ref, "queue-consumer", m.group(1),
                              app.ev(rel, line_of(text, start), "celery.task"), queue=m.group(1),
                              location=app.loc(rel, start, end.start() if end else None,
                                               line_of(text, start)))

    def _fastapi(self, app, b, rel, text, include_prefix):
        prefixes = {m.group(1): m.group(2) for m in RE_PY_ROUTER_PREFIX.finditer(text)}
        module_prefix = include_prefix.get(rel.rsplit("/", 1)[-1][:-3], "")
        routes = list(RE_PY_ROUTE.finditer(text))
        spans = {s: (s, e) for s, e in segments(text, [m.start() for m in routes])}
        for m in routes:
            method = "ANY" if m.group(2) == "api_route" else m.group(2).upper()
            path = "/" + "/".join(p.strip("/") for p in (module_prefix, prefixes.get(m.group(1), ""),
                                                         m.group(3)) if p.strip("/"))
            s, e = spans[m.start()]
            b.add_endpoint(app.ref, method, path, app.ev(rel, line_of(text, m.start()), "fastapi.route"),
                           location=app.loc(rel, s, e, line_of(text, m.start())))

    def _sqlalchemy(self, app, b, rel, text):
        classes = list(RE_SA_MODEL.finditer(text))
        for i, m in enumerate(classes):
            end = classes[i + 1].start() if i + 1 < len(classes) else len(text)
            body = text[m.end():end]
            tn = RE_SA_TABLENAME.search(body)
            if not tn:
                continue
            table = b.add_table(app.ref, tn.group(1), app.ev(rel, line_of(text, m.start()),
                                                              "sqlalchemy.model"),
                                aliases=(m.group(1),))
            for cm in RE_SA_COLUMN.finditer(body):
                args = cm.group(2)
                type_m = re.match(r"\s*(?:['\"]\w+['\"]\s*,\s*)?([A-Z]\w*(?:\([^)]*\))?)", args)
                fk = re.search(r"ForeignKey\(\s*['\"](\w+)\.(\w+)['\"]", args)
                primary = "primary_key=True" in args
                nullable_m = re.search(r"nullable\s*=\s*(True|False)", args)
                default = re.search(r"(?:server_)?default\s*=\s*([^,)]+)", args)
                table["columns"].append({
                    "name": cm.group(1), "type": type_m.group(1) if type_m else None,
                    "nullable": (nullable_m.group(1) == "True") if nullable_m else (None if not primary else False),
                    "default": default.group(1).strip() if default else None,
                    "primaryKey": primary, "unique": "unique=True" in args,
                    "references": {"table": fk.group(1), "column": fk.group(2)} if fk else None})
                if primary:
                    table["primaryKey"].append(cm.group(1))
                if fk:
                    table["foreignKeys"].append({"columns": [cm.group(1)],
                                                 "references": {"table": fk.group(1),
                                                                "columns": [fk.group(2)]}})

    def imports(self, app, rel, text):
        if not rel.endswith(".py"):
            return {}
        out = {}
        for m in RE_PY_FROM_IMPORT.finditer(text):
            mod = m.group(1)
            dots = len(mod) - len(mod.lstrip("."))
            base = dirname(rel)
            for _ in range(max(dots - 1, 0)):
                base = dirname(base)
            parts = mod.lstrip(".").split(".") if mod.lstrip(".") else []
            root = base if dots else app.root_rel
            target_base = join(root, "/".join(parts)) if parts else root
            for name in re.split(r"[,\s()]+", m.group(2)):
                if not name:
                    continue
                for cand in (join(target_base or "", name + ".py"), (target_base or "") + ".py",
                             join(target_base or "", "__init__.py")):
                    if cand and app.repo.exists(cand):
                        out[name] = cand
                        break
        return out

    def usages(self, app, rel, text):
        if not rel.endswith(".py"):
            return []
        out = []
        for rx, op in SA_OPS:
            for m in re.finditer(rx, text):
                out.append({"kind": "table", "alias": m.group(1), "op": op, "offset": m.start()})
        for m in re.finditer(r"\b(\w+)\.(delay|apply_async)\(", text):
            out.append({"kind": "enqueue", "queue": m.group(1), "offset": m.start()})
        return out


# --- Go ------------------------------------------------------------------------

RE_GIN_GROUP = re.compile(r"""\b(\w+)\s*:?=\s*(\w+)\.Group\(\s*"(/[^"]*)"\s*""")
RE_GIN_ROUTE = re.compile(r"""\b(\w+)\.(GET|POST|PUT|PATCH|DELETE|Any)\(\s*"(/[^"]*)"\s*""")


class GoGin(Adapter):
    name = "gin"

    def applies(self, app):
        return app.has_dep("gin-gonic/gin", "github.com/gin-gonic/gin")

    def collect(self, app, b):
        for rel in app.files:
            if not rel.endswith(".go"):
                continue
            text = app.read(rel)
            groups = {}
            for m in RE_GIN_GROUP.finditer(text):
                groups[m.group(1)] = groups.get(m.group(2), "") + m.group(3)
            routes = list(RE_GIN_ROUTE.finditer(text))
            spans = {s: (s, e) for s, e in segments(text, [m.start() for m in routes])}
            for m in routes:
                path = (groups.get(m.group(1), "") + m.group(3)).replace("//", "/")
                method = "ANY" if m.group(2) == "Any" else m.group(2)
                s, e = spans[m.start()]
                b.add_endpoint(app.ref, method, path, app.ev(rel, line_of(text, m.start()), "gin.route"),
                               location=app.loc(rel, s, e, line_of(text, m.start())))
