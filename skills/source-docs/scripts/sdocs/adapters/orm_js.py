"""JavaScript の ORM。Drizzle と Prisma のスキーマ定義と、コード中のテーブル操作。"""

import re

from .base import Adapter, line_of, match_brace, split_top_level, strip_comments
from .lang_js import is_js


def column(name, type_, nullable=None, default=None, primary=False, unique=False,
           references=None, note=None):
    col = {"name": name, "type": type_, "nullable": nullable, "default": default,
           "primaryKey": primary, "unique": unique, "references": references}
    if note:
        col["note"] = note
    return col


# --- Drizzle -------------------------------------------------------------------

RE_DRIZZLE_TABLE = re.compile(
    r"\bexport\s+const\s+(\w+)\s*=\s*(?:(\w+)\.table|(pg|mysql|sqlite)Table)\(\s*['\"]([\w.]+)['\"]\s*,\s*\{")
RE_DRIZZLE_SCHEMA = re.compile(r"\b(?:export\s+)?const\s+(\w+)\s*=\s*pgSchema\(\s*['\"](\w+)['\"]")
RE_DRIZZLE_ENUM = re.compile(
    r"\b(?:export\s+)?const\s+(\w+)\s*=\s*(?:pg|mysql)Enum\(\s*['\"](\w+)['\"]\s*,\s*\[([^\]]*)\]")
RE_DRIZZLE_VIEW = re.compile(
    r"\bexport\s+const\s+(\w+)\s*=\s*(pg|mysql|sqlite)(View|MaterializedView)\(\s*['\"](\w+)['\"]")
RE_DRIZZLE_COL = re.compile(r"^\s*(\w+)\s*:\s*(\w+)\s*\(\s*(?:['\"](\w+)['\"])?")
RE_REFERENCES = re.compile(r"\.references\(\s*\(\)\s*(?::\s*\w+\s*)?=>\s*(\w+)\.(\w+)")
RE_DEFAULT = re.compile(r"\.(default|defaultNow|defaultRandom|\$defaultFn)\(([^)]*)\)")
RE_EXTRA = re.compile(
    r"\b(index|uniqueIndex|unique|primaryKey|check)\(\s*(?:['\"](\w+)['\"])?\s*(?:,\s*sql`([^`]*)`)?\)?"
    r"(?:\.on\(([^)]*)\))?")
RE_PK_COLUMNS = re.compile(r"primaryKey\(\s*\{\s*columns\s*:\s*\[([^\]]*)\]")


class Drizzle(Adapter):
    name = "drizzle"

    def applies(self, app):
        return app.has_dep("drizzle-orm")

    def collect(self, app, b):
        files = []
        for rel in app.files:
            if not is_js(rel):
                continue
            text = app.read(rel)
            if "Table(" in text or ".table(" in text or "Enum(" in text or "View(" in text:
                files.append((rel, strip_comments(text)))
        # Enum は別ファイルで定義されることがあるので、先に全体から集める。
        # カラムの型には変数名ではなく Enum の名前を書く
        self.enum_vars = {}
        for rel, code in files:
            for m in RE_DRIZZLE_ENUM.finditer(code):
                values = re.findall(r"['\"]([^'\"]+)['\"]", m.group(3))
                b.add_enum(app.ref, m.group(2), values,
                           app.ev(rel, line_of(code, m.start()), "drizzle.enum"))
                self.enum_vars[m.group(1)] = m.group(2)
        for rel, code in files:
            schemas = {m.group(1): m.group(2) for m in RE_DRIZZLE_SCHEMA.finditer(code)}
            for m in RE_DRIZZLE_VIEW.finditer(code):
                kind = "materialized-view" if m.group(3) == "MaterializedView" else "view"
                b.add_table(app.ref, m.group(4), app.ev(rel, line_of(code, m.start()), "drizzle.view"),
                            kind=kind, aliases=(m.group(1),))
            for m in RE_DRIZZLE_TABLE.finditer(code):
                self._table(app, b, rel, code, m, schemas)

    def _table(self, app, b, rel, code, m, schemas):
        var, schema_var, name = m.group(1), m.group(2), m.group(4)
        schema = schemas.get(schema_var) if schema_var else None
        table = b.add_table(app.ref, name, app.ev(rel, line_of(code, m.start()), "drizzle.table"),
                            schema=schema, aliases=(var,))
        body_open = m.end() - 1
        body_close = match_brace(code, body_open)
        if body_close == -1:
            b.mark_unresolved(table, "カラム定義の終わりを読み取れない")
            return
        key_to_col = {}
        for part in split_top_level(code[body_open + 1:body_close]):
            cm = RE_DRIZZLE_COL.match(part.strip("\n"))
            if not cm:
                continue
            key, builder, col_name = cm.group(1), cm.group(2), cm.group(3) or cm.group(1)
            key_to_col[key] = col_name
            ref = RE_REFERENCES.search(part)
            dm = RE_DEFAULT.search(part)
            default = None
            if dm:
                default = {"defaultNow": "now()", "defaultRandom": "random()"}.get(
                    dm.group(1), dm.group(2).strip() or dm.group(1))
            primary = ".primaryKey()" in part
            not_null = ".notNull()" in part or primary
            table["columns"].append(column(
                col_name, self.enum_vars.get(builder, builder), nullable=not not_null, default=default, primary=primary,
                unique=".unique()" in part,
                references={"table": ref.group(1), "column": ref.group(2)} if ref else None))
            if primary:
                table["primaryKey"].append(col_name)
            if ref:
                table["foreignKeys"].append({"columns": [col_name],
                                             "references": {"table": ref.group(1),
                                                            "columns": [ref.group(2)]}})
        # 第 3 引数（インデックス・複合キー・CHECK）
        tail = code[body_close + 1:body_close + 1500]
        close = match_brace("(" + tail, 0, "(", ")")
        extras = tail[:close] if close > 0 else ""
        if "=>" in extras:
            self._extras(table, extras, key_to_col)

    @staticmethod
    def _extras(table, extras, key_to_col):
        def cols(raw):
            names = re.findall(r"\w+\.(\w+)", raw or "")
            return [key_to_col.get(n, n) for n in names]

        pk = RE_PK_COLUMNS.search(extras)
        if pk:
            table["primaryKey"] = cols(pk.group(1))
        for m in RE_EXTRA.finditer(extras):
            kind, name, check_sql, on = m.groups()
            if kind == "index":
                table["indexes"].append({"name": name, "columns": cols(on), "unique": False})
            elif kind == "uniqueIndex":
                table["indexes"].append({"name": name, "columns": cols(on), "unique": True})
            elif kind == "unique" and on:
                table["uniques"].append(cols(on))
            elif kind == "check" and check_sql:
                table["checks"].append({"name": name, "expression": check_sql.strip()})

    def usages(self, app, rel, text):
        if not is_js(rel):
            return []
        code = strip_comments(text)
        out = []
        for rx, op in ((r"\.insert\(\s*(\w+)\s*\)", "CREATE"), (r"\.update\(\s*(\w+)\s*\)", "UPDATE"),
                       (r"\.delete\(\s*(\w+)\s*\)", "DELETE"), (r"\.from\(\s*(\w+)\s*\)", "READ"),
                       (r"\bquery\.(\w+)\.find(?:Many|First)\(", "READ"),
                       (r"\.(?:inner|left|right|full)?[jJ]oin\(\s*(\w+)\s*,", "READ")):
            for m in re.finditer(rx, code):
                out.append({"kind": "table", "alias": m.group(1), "op": op, "offset": m.start()})
        return out


# --- Prisma --------------------------------------------------------------------

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
            mapped = re.search(r"@@map\(\s*\"([^\"]+)\"", body)
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
                        table["foreignKeys"].append({
                            "columns": [f.strip() for f in fields.group(1).split(",")],
                            "references": {"table": ftype,
                                           "columns": [r.strip() for r in refs.group(1).split(",")]}})
                continue
            if ftype not in PRISMA_SCALARS and ftype not in enums:
                continue
            col_map = re.search(r"@map\(\s*\"([^\"]+)\"", attrs)
            col_name = col_map.group(1) if col_map else fname
            field_to_col[fname] = col_name
            db_type = re.search(r"@db\.(\w+(?:\([^)]*\))?)", attrs)
            default = _attr_args(attrs, "default")
            type_ = ftype + ("[]" if is_list else "")
            if db_type:
                type_ += f" ({db_type.group(1)})"
            primary = bool(re.search(r"@id\b", attrs))
            table["columns"].append(column(
                col_name, type_, nullable=bool(optional), default=default, primary=primary,
                unique=bool(re.search(r"@unique\b", attrs))))
            if primary:
                table["primaryKey"].append(col_name)
        for fk in table["foreignKeys"]:
            fk["columns"] = [field_to_col.get(c, c) for c in fk["columns"]]

        def cols(raw):
            return [field_to_col.get(c.strip().split("(")[0], c.strip().split("(")[0])
                    for c in raw.split(",") if c.strip()]

        for m in re.finditer(r"@@(id|unique|index)\(\s*(?:fields\s*:\s*)?\[([^\]]*)\]([^)]*)\)", body):
            kind, raw, rest = m.groups()
            name = re.search(r"(?:map|name)\s*:\s*\"([^\"]+)\"", rest)
            if kind == "id":
                table["primaryKey"] = cols(raw)
            elif kind == "unique":
                table["uniques"].append(cols(raw))
            else:
                table["indexes"].append({"name": name.group(1) if name else None,
                                         "columns": cols(raw), "unique": False})

    def usages(self, app, rel, text):
        if not is_js(rel):
            return []
        return [{"kind": "table", "alias": m.group(1), "op": PRISMA_OPS[m.group(2)],
                 "offset": m.start()}
                for m in RE_PRISMA_CALL.finditer(strip_comments(text))]
