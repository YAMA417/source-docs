"""Supabase。Edge Function と、クライアント（`supabase.from()` / `functions.invoke` / auth / storage）。"""

import re

from .base import Adapter, strip_comments
from .nextjs import HTTP_METHODS, RE_REQ_METHOD


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

