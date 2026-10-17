"""コードでルートを登録する JavaScript の HTTP フレームワーク。

Express / Hono / Fastify / Koa / Elysia は `app.get('/x', handler)` の形が共通なので 1 つで扱う。
別ファイルのルータを `app.route('/api', books)` / `app.use('/api', books)` /
`app.register(books, { prefix: '/api' })` でマウントしている場合は、前置パスを解決する。
"""

import re

from .base import Adapter, line_of, segments, strip_comments
from .lang_js import JsLanguage, is_js, parse_imports

FRAMEWORKS = ("express", "hono", "fastify", "koa", "@koa/router", "koa-router", "elysia",
              "@hono/zod-openapi", "itty-router", "h3")

ROUTE_METHODS = ("get", "post", "put", "patch", "delete", "all", "options", "head")

RE_ROUTER_VAR = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*(?::\s*[\w<>\[\], ]+)?=\s*"
    r"(?:new\s+(?:Hono|OpenAPIHono|Koa|Router|Elysia|KoaRouter)\b[^;\n]*|"
    r"(?:express|express\.Router|Router|Fastify|fastify|createRouter|AutoRouter)\s*\([^;\n]*)")
RE_BASEPATH = re.compile(r"""\b(\w+)\s*=\s*new\s+\w+[^;\n]*?\.basePath\(\s*['"](/[^'"]*)['"]""")
RE_ROUTE = re.compile(
    r"""\b(\w+)\s*\.\s*(""" + "|".join(ROUTE_METHODS) + r""")\s*\(\s*(['"`])(/[^'"`]*)\3""")
RE_ROUTE_DYNAMIC = re.compile(
    r"""\b(\w+)\s*\.\s*(""" + "|".join(ROUTE_METHODS) + r""")\s*\(\s*([A-Za-z_][\w.]*)\s*,""")
RE_CHAINED = re.compile(r"""\b(\w+)\s*\.\s*route\(\s*['"](/[^'"]*)['"]\s*\)((?:\s*\.\s*\w+\s*\()?)""")
RE_CHAIN_METHOD = re.compile(r"\.\s*(" + "|".join(ROUTE_METHODS) + r")\s*\(")
RE_MOUNT = re.compile(r"""\b(\w+)\s*\.\s*(?:use|route)\(\s*['"](/[^'"]*)['"]\s*,\s*(\w+)""")
RE_REGISTER = re.compile(
    r"""\b(\w+)\s*\.\s*register\(\s*(\w+)\s*,\s*\{[^}]*?prefix\s*:\s*['"](/[^'"]*)['"]""")

# ルータとして扱ってよい慣習名。HTTP クライアント（axios / api）と取り違えないよう狭くとる
CONVENTIONAL = {"app", "router", "fastify", "server", "instance", "routes", "r"}


def _join_path(*parts):
    path = "/".join(p.strip("/") for p in parts if p and p != "/")
    return "/" + path if path else "/"


class JsHttp(Adapter):
    name = "js-http"

    def __init__(self):
        self.js = JsLanguage()

    def applies(self, app):
        return app.has_dep(*FRAMEWORKS)

    def collect(self, app, b):
        files = {}
        for rel in app.files:
            if not is_js(rel):
                continue
            text = app.read(rel)
            if not text or not self._imports_framework(text):
                continue
            files[rel] = self._scan(app, rel, text)

        prefixes = self._resolve_prefixes(files)
        for rel, info in files.items():
            text = app.read(rel)
            starts = [r["start"] for r in info["routes"]]
            spans = {s: (s, e) for s, e in segments(text, starts)}
            for r in info["routes"]:
                var_base = info["base"].get(r["var"], "")
                for prefix in sorted(prefixes.get(rel, {""})):
                    path = _join_path(prefix, var_base, r["path"])
                    s, e = spans[r["start"]]
                    b.add_endpoint(app.ref, "ANY" if r["method"] == "ALL" else r["method"], path,
                                   app.ev(rel, line_of(text, r["start"]), "js-http.route"),
                                   location=app.loc(rel, s, e, line_of(text, r["start"])))
            for line, var in info["dynamic"]:
                b.add_unsupported(app.ref, rel, "endpoint",
                                  f"{line} 行目: パスが変数（{var}）で、値をコードから確定できない")

    @staticmethod
    def _imports_framework(text):
        for _, spec in parse_imports(text):
            if spec.split("/")[0] in FRAMEWORKS or spec in FRAMEWORKS or spec.startswith("hono/"):
                return True
        return False

    def _scan(self, app, rel, text):
        code = strip_comments(text)
        router_vars = {m.group(1) for m in RE_ROUTER_VAR.finditer(code)}
        base = {m.group(1): m.group(2) for m in RE_BASEPATH.finditer(code)}
        allowed = router_vars | CONVENTIONAL
        routes = []
        for m in RE_ROUTE.finditer(code):
            if m.group(1) in allowed:
                routes.append({"var": m.group(1), "method": m.group(2).upper(),
                               "path": m.group(4), "start": m.start()})
        for m in RE_CHAINED.finditer(code):
            if m.group(1) not in allowed:
                continue
            tail = code[m.end(2):m.end(2) + 400]
            stop = re.search(r";|\n\s*\n", tail)
            for cm in RE_CHAIN_METHOD.finditer(tail[:stop.start() if stop else 400]):
                routes.append({"var": m.group(1), "method": cm.group(1).upper(),
                               "path": m.group(2), "start": m.start()})
        dynamic = [(line_of(code, m.start()), m.group(3)) for m in RE_ROUTE_DYNAMIC.finditer(code)
                   if m.group(1) in allowed]
        imports = self.js.imports(app, rel, text)
        mounts = []
        for m in RE_MOUNT.finditer(code):
            target = imports.get(m.group(3))
            if target:
                mounts.append((m.group(1), m.group(2), target))
        for m in RE_REGISTER.finditer(code):
            target = imports.get(m.group(2))
            if target:
                mounts.append((m.group(1), m.group(3), target))
        return {"routes": routes, "base": base, "mounts": mounts, "dynamic": dynamic}

    @staticmethod
    def _resolve_prefixes(files):
        """マウントをたどって、ファイルごとの前置パスの集合を求める。"""
        mounted = {t for info in files.values() for _, _, t in info["mounts"]}
        prefixes = {rel: {""} for rel in files if rel not in mounted}
        for _ in range(6):
            changed = False
            for rel, info in files.items():
                for var, path, target in info["mounts"]:
                    for p in prefixes.get(rel, set()):
                        full = _join_path(p, info["base"].get(var, ""), path)
                        full = "" if full == "/" else full
                        if full not in prefixes.setdefault(target, set()):
                            prefixes[target].add(full)
                            changed = True
            if not changed:
                break
        return prefixes
