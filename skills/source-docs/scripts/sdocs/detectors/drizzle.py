"""Drizzle ORM。`pgTable` / `mysqlTable` / `sqliteTable` の定義と、コード中のテーブル操作。"""

import re

from .base import Adapter, add_column, column, line_of, match_brace, split_top_level, strip_comments
from .javascript import is_js


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
            add_column(table, column(
                col_name, self.enum_vars.get(builder, builder), nullable=not not_null, default=default, primary=primary,
                unique=".unique()" in part,
                references={"table": ref.group(1), "column": ref.group(2)} if ref else None), "drizzle")
            if ref:
                # `.references()` は ORM の関係であり、drizzle-kit が FK として作る物理定義でもある。
                # migration が無いときだけ物理 FK の根拠にする（core.dbstate が判断する）
                entry = {"name": None, "columns": [col_name],
                         "references": {"table": ref.group(1), "columns": [ref.group(2)]}, "source": "drizzle"}
                table["ormRelations"].append(dict(entry))
                table["physicalForeignKeys"].append(dict(entry))
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
            table.setdefault("_pk", {})["drizzle"] = cols(pk.group(1))
        for m in RE_EXTRA.finditer(extras):
            kind, name, check_sql, on = m.groups()
            if kind == "index":
                table["indexes"].append({"name": name, "columns": cols(on), "unique": False, "source": "drizzle"})
            elif kind == "uniqueIndex":
                table["indexes"].append({"name": name, "columns": cols(on), "unique": True, "source": "drizzle"})
            elif kind == "unique" and on:
                table["uniques"].append({"name": name, "columns": cols(on), "source": "drizzle"})
            elif kind == "check" and check_sql:
                table["checks"].append({"name": name, "expression": check_sql.strip(), "source": "drizzle"})

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


