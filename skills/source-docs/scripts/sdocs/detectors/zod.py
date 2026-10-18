"""zod の入力検証スキーマ。API のリクエストの根拠（precedence の `runtime-schema`）として使う。

拾うもの（どちらも `usages` で、入口からたどったコード片の中から拾う）。

- schema-def  `const postPatchSchema = z.object({ title: z.string().min(3).optional(), ... })`
- schema-use  `postPatchSchema.parse(json)` / `.safeParse(...)` / `.parseAsync(...)` /
              `zValidator("json", schema)`（Hono）

実行時に検証している項目だけを書く。TypeScript の型（`type Body = {...}`）はここでは使わない。
"""

import re

from .base import Adapter, match_brace, split_top_level, strip_comments
from .javascript import is_js

RE_DEF = re.compile(r"\b(?:const|let|var)\s+([\w$]+)\s*(?::[^=]+)?=\s*z\s*\.\s*object\s*\(")
RE_USE = re.compile(r"(?<![\w$.])([\w$]+)\s*\.\s*(?:safeParse|parse|parseAsync|safeParseAsync)\s*\(")
RE_HONO = re.compile(r"""zValidator\(\s*['"](\w+)['"]\s*,\s*([\w$]+)""")
RE_TYPE = re.compile(r"z\s*\.\s*(?:coerce\s*\.\s*)?(\w+)\s*\(")
RE_CONSTRAINT = re.compile(r"\.\s*(min|max|length|email|url|uuid|cuid|regex|int|positive|nonnegative|"
                           r"nonempty|trim|startsWith|endsWith|datetime)\s*\(([^()]*)\)")

TYPE_NAME = {"string": "string", "number": "number", "boolean": "boolean", "date": "date",
             "bigint": "bigint", "array": "array", "object": "object", "enum": "enum", "nativeEnum": "enum",
             "literal": "literal", "any": "any", "unknown": "unknown", "union": "union", "record": "record"}
NESTED_IN = {"params": "path", "searchParams": "query", "query": "query", "body": "body", "headers": "header"}
HONO_IN = {"json": "body", "form": "body", "query": "query", "param": "path", "header": "header",
           "cookie": "cookie"}


class Zod(Adapter):
    name = "zod"

    def applies(self, app):
        return app.has_dep("zod")

    def usages(self, app, rel, text):
        if not is_js(rel):
            return []
        code = strip_comments(text)
        out = []
        for m in RE_DEF.finditer(code):
            open_paren = m.end() - 1
            close = match_brace(code, open_paren, "(", ")")
            if close == -1:
                continue
            body = code[open_paren + 1:close].strip()
            if not body.startswith("{"):
                continue
            out.append({"kind": "schema-def", "name": m.group(1), "fields": parse_fields(body[1:-1]),
                        "offset": m.start()})
        for m in RE_USE.finditer(code):
            close = match_brace(code, m.end() - 1, "(", ")")
            arg = code[m.end():close] if close != -1 else ""
            out.append({"kind": "schema-use", "name": m.group(1), "in": _location(arg), "offset": m.start()})
        for m in RE_HONO.finditer(code):
            out.append({"kind": "schema-use", "name": m.group(2), "in": HONO_IN.get(m.group(1), m.group(1)),
                        "offset": m.start()})
        return out


def parse_fields(body):
    """`{ ... }` の中身から項目を取り出す。入れ子のオブジェクトは型 object として 1 項目にする。"""
    fields = []
    for part in split_top_level(body):
        key, sep, expr = part.partition(":")
        key = key.strip().strip("'\"")
        if not sep or not re.match(r"^[\w$]+$", key):
            continue
        expr = expr.strip()
        # ルートの文脈（`{ params: z.object({ postId: z.string() }) }`）は、中の項目をパスパラメータとして展開する
        nested = re.match(r"z\s*\.\s*object\s*\(", expr)
        if key in NESTED_IN and nested:
            close = match_brace(expr, nested.end() - 1, "(", ")")
            inner = expr[nested.end():close].strip() if close != -1 else ""
            if inner.startswith("{"):
                fields += [dict(f, **{"in": NESTED_IN[key]}) for f in parse_fields(inner[1:-1])]
                continue
        t = RE_TYPE.search(expr)
        type_ = TYPE_NAME.get(t.group(1), t.group(1)) if t else None
        if type_ == "enum":
            values = re.search(r"enum\s*\(\s*\[([^\]]*)\]", expr)
            if values:
                type_ = "enum(" + ", ".join(v.strip().strip("'\"") for v in values.group(1).split(",") if v.strip()) + ")"
        optional = bool(re.search(r"\.\s*(?:optional|nullish)\s*\(|\.\s*default\s*\(", expr))
        constraints = []
        for c in RE_CONSTRAINT.finditer(expr):
            arg = c.group(2).strip()
            constraints.append(f"{c.group(1)}({arg})" if arg and c.group(1) != "regex" else c.group(1))
        fields.append({"name": key, "type": type_, "required": not optional,
                       "constraints": ", ".join(constraints) or None, "in": "auto"})
    return fields


def _location(arg):
    if re.search(r"searchParams|\bquery\b", arg):
        return "query"
    if re.search(r"\bparams\b", arg):
        return "path"
    return "body"
