"""Prisma。schema.prisma のモデル・Enum・関係と、コード中の `prisma.model.op()`。"""

import re

from .base import Adapter, add_column, column, line_of, match_brace, strip_comments
from .javascript import is_js


# --- Prisma --------------------------------------------------------------------

PRISMA_PROVIDERS = {"postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mysql": "MySQL",
                    "sqlite": "SQLite", "sqlserver": "SQL Server", "mongodb": "MongoDB",
                    "cockroachdb": "CockroachDB"}
RE_PRISMA_BLOCK = re.compile(r"^(model|view|enum)\s+(\w+)\s*\{", re.M)
RE_PRISMA_FIELD = re.compile(r"^\s*(\w+)\s+(\w+)(\[\])?(\?)?\s*(.*)$")
PRISMA_SCALARS = {"String", "Int", "BigInt", "Float", "Decimal", "Boolean", "DateTime", "Json",
                  "Bytes", "Unsupported"}
PRISMA_OPS = {
    "create": "CREATE", "createMany": "CREATE", "createManyAndReturn": "CREATE",
    "findMany": "READ", "findUnique": "READ", "findFirst": "READ", "findUniqueOrThrow": "READ",
    "findFirstOrThrow": "READ", "count": "READ", "aggregate": "READ", "groupBy": "READ",
    "update": "UPDATE", "updateMany": "UPDATE", "upsert": "UPDATE",
    "delete": "DELETE", "deleteMany": "DELETE",
}
RE_PRISMA_CALL = re.compile(r"\b(?:prisma|tx|db|client|this\.prisma|ctx\.prisma|ctx\.db)\s*\.\s*(\w+)\s*\.\s*("
                            + "|".join(PRISMA_OPS) + r")\s*\(")


def _attr_args(attrs, name):
    m = re.search(r"@" + re.escape(name) + r"\(", attrs)
    if not m:
        return None
    close = match_brace(attrs, m.end() - 1, "(", ")")
    return attrs[m.end():close] if close != -1 else None


class Prisma(Adapter):
    name = "prisma"

    def applies(self, app):
        return app.has_dep("prisma", "@prisma/client") or any(
            f.endswith(".prisma") for f in app.files)

    def collect(self, app, b):
        for rel in app.files:
            if rel.endswith(".prisma"):
                self._schema(app, b, rel, app.read(rel))

    def _schema(self, app, b, rel, text):
        text = re.sub(r"//[^\n]*", "", text)
        ds = re.search(r"datasource\s+\w+\s*\{[^}]*?provider\s*=\s*\"(\w+)\"", text)
        if ds:
            b.add_stack_hint(app.ref, "db", PRISMA_PROVIDERS.get(ds.group(1), ds.group(1)))
        models = {m.group(2) for m in RE_PRISMA_BLOCK.finditer(text) if m.group(1) in ("model", "view")}
        enums = {m.group(2) for m in RE_PRISMA_BLOCK.finditer(text) if m.group(1) == "enum"}
        for m in RE_PRISMA_BLOCK.finditer(text):
            kind, name = m.group(1), m.group(2)
            close = match_brace(text, m.end() - 1)
            body = text[m.end():close]
            ev = app.ev(rel, line_of(text, m.start()), f"prisma.{kind}")
            if kind == "enum":
                values = [ln.strip().split()[0] for ln in body.splitlines()
                          if ln.strip() and not ln.strip().startswith("@")]
                b.add_enum(app.ref, name, values, ev)
                continue
            mapped = re.search(r"@@map\(\s*(?:name\s*:\s*)?\"([^\"]+)\"", body)
            schema = re.search(r"@@schema\(\s*\"([^\"]+)\"", body)
            table = b.add_table(app.ref, mapped.group(1) if mapped else name, ev,
                                schema=schema.group(1) if schema else None,
                                kind="view" if kind == "view" else "table",
                                aliases=(name, name[0].lower() + name[1:]))
            self._fields(table, body, models, enums)

    @staticmethod
    def _fields(table, body, models, enums):
        field_to_col = {}
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("@@"):
                continue
            fm = RE_PRISMA_FIELD.match(line)
            if not fm:
                continue
            fname, ftype, is_list, optional, attrs = fm.groups()
            if ftype in models:
                rel_args = _attr_args(attrs, "relation")
                if rel_args:
                    fields = re.search(r"fields\s*:\s*\[([^\]]*)\]", rel_args)
                    refs = re.search(r"references\s*:\s*\[([^\]]*)\]", rel_args)
                    if fields and refs:
                        # Prisma の relation は ORM 上の関係。DB に FK があるとは限らない
                        # （relationMode = "prisma" や、migration で落とした場合）。物理 FK とは分ける
                        on_delete = re.search(r"onDelete\s*:\s*(\w+)", rel_args)
                        table["ormRelations"].append({
                            "name": fname, "columns": [f.strip() for f in fields.group(1).split(",")],
                            "references": {"table": ftype,
                                           "columns": [r.strip() for r in refs.group(1).split(",")]},
                            "onDelete": on_delete.group(1) if on_delete else None, "source": "prisma"})
                continue
            if ftype not in PRISMA_SCALARS and ftype not in enums:
                continue
            col_map = re.search(r"@map\(\s*(?:name\s*:\s*)?\"([^\"]+)\"", attrs)
            col_name = col_map.group(1) if col_map else fname
            field_to_col[fname] = col_name
            db_type = re.search(r"@db\.(\w+(?:\([^)]*\))?)", attrs)
            default = _attr_args(attrs, "default")
            type_ = ftype + ("[]" if is_list else "")
            if db_type:
                type_ += f" ({db_type.group(1)})"
            primary = bool(re.search(r"@id\b", attrs))
            add_column(table, column(
                col_name, type_, nullable=bool(optional), default=default, primary=primary,
                unique=bool(re.search(r"@unique\b", attrs))), "prisma")
        for rel in table["ormRelations"]:
            if rel["source"] == "prisma":
                rel["columns"] = [field_to_col.get(c, c) for c in rel["columns"]]

        def cols(raw):
            return [field_to_col.get(c.strip().split("(")[0], c.strip().split("(")[0])
                    for c in raw.split(",") if c.strip()]

        for m in re.finditer(r"@@(id|unique|index)\(\s*(?:fields\s*:\s*)?\[([^\]]*)\]([^)]*)\)", body):
            kind, raw, rest = m.groups()
            name = re.search(r"(?:map|name)\s*:\s*\"([^\"]+)\"", rest)
            if kind == "id":
                table.setdefault("_pk", {})["prisma"] = cols(raw)
            elif kind == "unique":
                table["uniques"].append({"name": name.group(1) if name else None, "columns": cols(raw),
                                         "source": "prisma"})
            else:
                table["indexes"].append({"name": name.group(1) if name else None,
                                         "columns": cols(raw), "unique": False, "source": "prisma"})

    def usages(self, app, rel, text):
        if not is_js(rel):
            return []
        return [{"kind": "table", "alias": m.group(1), "op": PRISMA_OPS[m.group(2)],
                 "offset": m.start()}
                for m in RE_PRISMA_CALL.finditer(strip_comments(text))]
