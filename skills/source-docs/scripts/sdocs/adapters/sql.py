"""SQL のスキーマ定義（マイグレーション・schema.sql）と、コード中の素の SQL。

マイグレーションはファイル名の順に適用したものとして読む。`DROP TABLE` で消えたものは残さない。
対応する文: CREATE TABLE / ALTER TABLE ADD (COLUMN|CONSTRAINT) / CREATE [UNIQUE] INDEX /
CREATE TYPE ... AS ENUM / CREATE SEQUENCE / CREATE [MATERIALIZED] VIEW / CREATE TRIGGER /
DROP TABLE / pg_cron の cron.schedule。
"""

import re

from .base import Adapter, line_of, split_top_level
from .orm_js import column

IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[\w$]+)'
QNAME = rf"({IDENT}(?:\.{IDENT})?)"

RE_CREATE_TABLE = re.compile(
    rf"^CREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP(?:ORARY)?\s+|UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{QNAME}\s*\(",
    re.I)
RE_ALTER_ADD_COL = re.compile(
    rf"^ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?{QNAME}\s+ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(?!CONSTRAINT\b|PRIMARY\b|FOREIGN\b|UNIQUE\b|CHECK\b)(.+)$",
    re.I | re.S)
RE_ALTER_ADD_CONSTRAINT = re.compile(
    rf"^ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?{QNAME}\s+ADD\s+((?:CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK)\b.+)$",
    re.I | re.S)
RE_CREATE_INDEX = re.compile(
    rf"^CREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?({IDENT})?\s*ON\s+(?:ONLY\s+)?{QNAME}\s*(?:USING\s+\w+\s*)?\(([^;]*)\)",
    re.I | re.S)
RE_CREATE_ENUM = re.compile(rf"^CREATE\s+TYPE\s+{QNAME}\s+AS\s+ENUM\s*\(([^)]*)\)", re.I | re.S)
RE_CREATE_SEQUENCE = re.compile(rf"^CREATE\s+SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?{QNAME}", re.I)
RE_CREATE_VIEW = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?{QNAME}", re.I)
RE_CREATE_TRIGGER = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+({IDENT})\s+(BEFORE|AFTER|INSTEAD\s+OF)\s+(.+?)\s+ON\s+{QNAME}(.*)$",
    re.I | re.S)
RE_DROP_TABLE = re.compile(rf"^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?{QNAME}", re.I)
RE_PG_CRON = re.compile(
    r"""cron\.schedule\(\s*'([^']+)'\s*,\s*'([^']+)'\s*(?:,\s*(\$\$.*?\$\$|'(?:[^']|'')*'))?""", re.S)
RE_EXECUTE_FN = re.compile(r"EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+([\w.]+)", re.I)

COLUMN_STOP = re.compile(
    r"\b(NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|UNIQUE|REFERENCES|CHECK|CONSTRAINT|GENERATED|COLLATE)\b", re.I)


def unquote(name):
    return re.sub(r'^["`\[]|["`\]]$', "", name)


def split_qname(qname):
    parts = [unquote(p) for p in re.findall(IDENT, qname)]
    return (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])


def statements(text):
    """`;` で文に分ける。文字列・`$$` 本体・コメントの中の `;` では切らない。"""
    out, buf, i, n = [], [], 0, len(text)
    start = 0
    quote = None
    while i < n:
        c = text[i]
        if quote:
            if text.startswith(quote, i):
                buf.append(quote)
                i += len(quote)
                quote = None
                continue
            buf.append(c)
            i += 1
            continue
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            if not buf:
                start = i
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i)
            i = n if end == -1 else end + 2
            if not buf:
                start = i
            continue
        dollar = re.match(r"\$\w*\$", text[i:i + 40])
        if dollar:
            quote = dollar.group(0)
            buf.append(quote)
            i += len(quote)
            continue
        if c == "'":
            quote = "'"
        if c == ";":
            out.append(("".join(buf).strip(), start))
            buf = []
            i += 1
            start = i
            continue
        if not buf and c.isspace():
            start = i + 1
        buf.append(c)
        i += 1
    if "".join(buf).strip():
        out.append(("".join(buf).strip(), start))
    return out


def _parse_column(defn):
    m = re.match(rf"\s*({IDENT})\s+(.*)$", defn, re.S)
    if not m:
        return None
    name, rest = unquote(m.group(1)), m.group(2).strip()
    stop = COLUMN_STOP.search(rest)
    type_ = (rest[:stop.start()] if stop else rest).strip()
    upper = rest.upper()
    default = None
    dm = re.search(r"\bDEFAULT\s+((?:\([^)]*\)|'[^']*'|[^\s,]+(?:\([^)]*\))?))", rest, re.I)
    if dm:
        default = dm.group(1)
    ref = re.search(rf"\bREFERENCES\s+{QNAME}\s*(?:\(\s*({IDENT})\s*\))?", rest, re.I)
    references = None
    if ref:
        _, rtable = split_qname(ref.group(1))
        references = {"table": rtable, "column": unquote(ref.group(2)) if ref.group(2) else None}
    primary = "PRIMARY KEY" in re.sub(r"\s+", " ", upper)
    nullable = None
    if re.search(r"\bNOT\s+NULL\b", upper) or primary:
        nullable = False
    elif re.search(r"\bNULL\b", upper):
        nullable = True
    else:
        nullable = True  # SQL では制約が無ければ NULL を許す
    check = re.search(r"\bCHECK\s*\((.*)\)", rest, re.I | re.S)
    col = column(name, type_, nullable=nullable, default=default, primary=primary,
                 unique=bool(re.search(r"\bUNIQUE\b", upper)), references=references)
    return col, (check.group(1).strip() if check else None)


def _cols(raw):
    return [unquote(c.strip().split()[0]) for c in raw.split(",") if c.strip()]


def _table_constraint(table, defn):
    d = re.sub(r"^\s*CONSTRAINT\s+" + IDENT + r"\s+", "", defn, flags=re.I).strip()
    name_m = re.match(r"^\s*CONSTRAINT\s+(" + IDENT + r")", defn, re.I)
    name = unquote(name_m.group(1)) if name_m else None
    pk = re.match(r"PRIMARY\s+KEY\s*\(([^)]*)\)", d, re.I)
    if pk:
        table["primaryKey"] = _cols(pk.group(1))
        for c in table["columns"]:
            if c["name"] in table["primaryKey"]:
                c["primaryKey"] = True
                c["nullable"] = False
        return True
    uq = re.match(r"UNIQUE\s*(?:NULLS\s+(?:NOT\s+)?DISTINCT\s*)?\(([^)]*)\)", d, re.I)
    if uq:
        table["uniques"].append(_cols(uq.group(1)))
        return True
    fk = re.match(rf"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+{QNAME}\s*(?:\(([^)]*)\))?", d, re.I)
    if fk:
        _, rtable = split_qname(fk.group(2))
        table["foreignKeys"].append({"columns": _cols(fk.group(1)),
                                     "references": {"table": rtable,
                                                    "columns": _cols(fk.group(3) or "")}})
        return True
    ck = re.match(r"CHECK\s*\((.*)\)\s*$", d, re.I | re.S)
    if ck:
        table["checks"].append({"name": name, "expression": ck.group(1).strip()})
        return True
    return re.match(r"(EXCLUDE|LIKE)\b", d, re.I) is not None


class Sql(Adapter):
    name = "sql"

    def applies(self, app):
        return any(f.endswith(".sql") for f in app.files)

    def collect(self, app, b):
        files = sorted(f for f in app.files if f.endswith(".sql"))
        for rel in files:
            text = app.read(rel)
            for stmt, offset in statements(text):
                self._statement(app, b, rel, stmt, line_of(text, offset))

    def _statement(self, app, b, rel, stmt, line):
        ev = app.ev(rel, line, "sql")
        m = RE_CREATE_TABLE.match(stmt)
        if m:
            schema, name = split_qname(m.group(1))
            table = b.add_table(app.ref, name, ev, schema=schema)
            body = stmt[m.end():stmt.rfind(")")]
            for part in split_top_level(body):
                part = part.strip()
                if not part:
                    continue
                if re.match(r"(CONSTRAINT|PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK|EXCLUDE|LIKE)\b",
                            part, re.I):
                    _table_constraint(table, part)
                    continue
                parsed = _parse_column(part)
                if parsed:
                    self._add_column(table, *parsed)
            return
        m = RE_ALTER_ADD_CONSTRAINT.match(stmt)
        if m:
            table = self._existing(app, b, m.group(1), ev)
            _table_constraint(table, m.group(2))
            return
        m = RE_ALTER_ADD_COL.match(stmt)
        if m:
            table = self._existing(app, b, m.group(1), ev)
            parsed = _parse_column(m.group(2))
            if parsed:
                self._add_column(table, *parsed)
            return
        m = RE_CREATE_INDEX.match(stmt)
        if m:
            table = self._existing(app, b, m.group(3), ev)
            table["indexes"].append({"name": unquote(m.group(2)) if m.group(2) else None,
                                     "columns": _cols(m.group(4)), "unique": bool(m.group(1))})
            return
        m = RE_CREATE_ENUM.match(stmt)
        if m:
            _, name = split_qname(m.group(1))
            b.add_enum(app.ref, name, re.findall(r"'([^']*)'", m.group(2)), ev)
            return
        m = RE_CREATE_SEQUENCE.match(stmt)
        if m:
            b.add_sequence(app.ref, split_qname(m.group(1))[1], ev)
            return
        m = RE_CREATE_VIEW.match(stmt)
        if m:
            schema, name = split_qname(m.group(2))
            b.add_table(app.ref, name, ev, schema=schema,
                        kind="materialized-view" if m.group(1) else "view")
            return
        m = RE_CREATE_TRIGGER.match(stmt)
        if m:
            name = unquote(m.group(1))
            table = self._existing(app, b, m.group(4), ev)
            events = re.sub(r"\s+", " ", m.group(3)).strip()
            fn = RE_EXECUTE_FN.search(m.group(5))
            trigger = {"name": name, "timing": m.group(2).upper(), "events": events,
                       "function": fn.group(1) if fn else None}
            if trigger not in table["triggers"]:
                table["triggers"].append(trigger)
            b.add_entrypoint(app.ref, "db-trigger", name, ev, target=table["_uid"],
                             location=app.loc(rel, 0, 0, line))
            return
        m = RE_DROP_TABLE.match(stmt)
        if m:
            schema, name = split_qname(m.group(1))
            b.drop_table(app.ref, name, schema)
            return
        for cm in RE_PG_CRON.finditer(stmt):
            job = b.add_job(app.ref, "cron", cm.group(1), ev, schedule=cm.group(2),
                            location=app.loc(rel, 0, 0, line))
            job["_sql"] = cm.group(3) or ""

    @staticmethod
    def _existing(app, b, qname, ev):
        schema, name = split_qname(qname)
        return b.add_table(app.ref, name, ev, schema=schema)

    @staticmethod
    def _add_column(table, col, check):
        table["columns"] = [c for c in table["columns"] if c["name"] != col["name"]]
        table["columns"].append(col)
        if col["primaryKey"] and col["name"] not in table["primaryKey"]:
            table["primaryKey"].append(col["name"])
        if col["references"]:
            table["foreignKeys"].append({"columns": [col["name"]],
                                         "references": {"table": col["references"]["table"],
                                                        "columns": [col["references"]["column"]]
                                                        if col["references"]["column"] else []}})
        if check:
            table["checks"].append({"name": None, "expression": check})

    def usages(self, app, rel, text):
        """コード中の素の SQL。pg_cron のジョブ本体もここで読む。"""
        if rel.endswith(".sql"):
            return []
        out = []
        for rx, op in ((r"\bINSERT\s+INTO\s+" + QNAME, "CREATE"),
                       (r"\bUPDATE\s+" + QNAME + r"\s+SET\b", "UPDATE"),
                       (r"\bDELETE\s+FROM\s+" + QNAME, "DELETE"),
                       (r"\bSELECT\b[^;'\"`]{0,200}?\bFROM\s+" + QNAME, "READ")):
            for m in re.finditer(rx, text, re.I):
                out.append({"kind": "table", "alias": split_qname(m.group(1))[1], "op": op,
                            "offset": m.start(), "strict": True})
        return out


def sql_table_usages(sql_text):
    """SQL 断片（pg_cron の本体など）から、テーブル操作を拾う。"""
    return Sql().usages(None, "inline.sql.txt", sql_text)
