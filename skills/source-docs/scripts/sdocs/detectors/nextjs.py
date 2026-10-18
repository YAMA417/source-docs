"""ファイルの置き場所が URL になるフレームワーク。Next.js（App / Pages Router）と Expo Router。"""

import re

from .base import Adapter, line_of, route_from_dirs, segments, stem, strip_comments

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

RE_EXPORT_METHOD = re.compile(
    r"\bexport\s+(?:async\s+)?(?:function\s+|const\s+|let\s+)(" + "|".join(HTTP_METHODS) + r")\b")
RE_EXPORT_LIST = re.compile(r"\bexport\s*\{([^}]*)\}")
RE_DEFAULT_NAME = re.compile(r"\bexport\s+default\s+(?:async\s+)?function\s+(\w+)")
RE_REQ_METHOD = re.compile(
    r"""(?:req|request)\.method\s*[!=]==?\s*['"](\w+)['"]|case\s+['"](""" + "|".join(HTTP_METHODS) + r""")['"]""")
RE_USE_SERVER = re.compile(r"""^\s*(?:/\*.*?\*/\s*|//[^\n]*\n\s*)*['"]use server['"]""", re.S)
RE_EXPORT_ASYNC_FN = re.compile(r"\bexport\s+(?:async\s+function\s+(\w+)|const\s+(\w+)\s*=\s*async\b)")

NOT_A_PAGE = {"_app", "_document", "_error", "_middleware", "404", "500"}
EXPO_NOT_A_SCREEN = {"_layout", "+not-found", "+html", "+native-intent", "+middleware"}


def exported_methods(text):
    """route ファイルが export している HTTP メソッドと、その位置。"""
    code = strip_comments(text)
    found = [(m.group(1), m.start()) for m in RE_EXPORT_METHOD.finditer(code)]
    for m in RE_EXPORT_LIST.finditer(code):
        for part in m.group(1).split(","):
            local = re.split(r"\s+as\s+", part.strip())[-1].strip()
            if local in HTTP_METHODS:
                found.append((local, m.start()))
    return found


def _app_dir(app, rel):
    """`app/` ディレクトリ配下なら、その配下のパス部分を返す。"""
    m = re.match(r"^(?:src/)?app/(.*)$", app.app_rel(rel))
    return m.group(1) if m else None


def _add_methods(app, b, rel, path, rule):
    text = app.read(rel)
    methods = exported_methods(text)
    if not methods:
        b.add_unsupported(app.ref, rel, "endpoint", "HTTP メソッドの export が見つからない")
        return
    # `export { handler as GET, handler as POST }` は同じ位置から 2 つ出るので、位置で引く
    spans = {s: (s, e) for s, e in segments(text, [s for _, s in methods])}
    for method, start in methods:
        s, e = spans[start]
        b.add_endpoint(app.ref, method, path, app.ev(rel, line_of(text, start), rule),
                       location=app.loc(rel, s, e, line_of(text, start)))


class NextJs(Adapter):
    name = "nextjs"

    def applies(self, app):
        return app.has_dep("next")

    def collect(self, app, b):
        for rel in app.files:
            inner = _app_dir(app, rel)
            if inner is not None:
                self._app_router(app, b, rel, inner)
                continue
            m = re.match(r"^(?:src/)?pages/(.*)$", app.app_rel(rel))
            if m:
                self._pages_router(app, b, rel, m.group(1))
                continue
            if re.search(r"\.(ts|tsx|js|jsx)$", rel):
                self._server_actions(app, b, rel)

    def _app_router(self, app, b, rel, inner):
        parts = inner.split("/")
        name = parts[-1]
        if re.fullmatch(r"page\.(tsx|jsx|ts|js|mdx)", name):
            text = app.read(rel)
            dn = RE_DEFAULT_NAME.search(text)
            b.add_screen(app.ref, route_from_dirs(parts[:-1]), app.ev(rel, rule="nextjs.page"),
                         location=app.loc(rel, 0, len(text), 1), name=dn.group(1) if dn else None)
        elif re.fullmatch(r"route\.(ts|tsx|js|jsx|mjs)", name):  # OG 画像などは route.tsx に書かれる
            _add_methods(app, b, rel, route_from_dirs(parts[:-1]), "nextjs.route")
        elif re.search(r"\.(ts|tsx|js|jsx)$", name):
            self._server_actions(app, b, rel)

    def _pages_router(self, app, b, rel, inner):
        if not re.search(r"\.(tsx|jsx|ts|js)$", inner):
            return
        parts = inner.split("/")
        base = re.sub(r"\.(tsx|jsx|ts|js)$", "", parts[-1])
        if base in NOT_A_PAGE:
            return
        dirs = parts[:-1] + ([] if base == "index" else [base])
        path = route_from_dirs(dirs)
        text = app.read(rel)
        if parts[0] == "api":
            methods = sorted({(m.group(1) or m.group(2)).upper()
                              for m in RE_REQ_METHOD.finditer(text)} & set(HTTP_METHODS))
            ev = app.ev(rel, rule="nextjs.pages-api")
            if not methods:
                item = b.add_endpoint(app.ref, "ANY", path, ev, location=app.loc(rel, 0, len(text), 1))
                b.mark_unresolved(item, "受け付ける HTTP メソッドをコードから特定できない")
            for method in methods:
                b.add_endpoint(app.ref, method, path, ev, location=app.loc(rel, 0, len(text), 1))
        else:
            dn = RE_DEFAULT_NAME.search(text)
            b.add_screen(app.ref, path, app.ev(rel, rule="nextjs.pages"),
                         location=app.loc(rel, 0, len(text), 1), name=dn.group(1) if dn else None)

    def _server_actions(self, app, b, rel):
        text = app.read(rel)
        if not RE_USE_SERVER.match(text):
            return
        fns = [(m.group(1) or m.group(2), m.start()) for m in RE_EXPORT_ASYNC_FN.finditer(text)]
        spans = segments(text, [s for _, s in fns])
        for (fn, start), (s, e) in zip(fns, spans):
            b.add_endpoint(app.ref, "ACTION", f"{stem(rel)}.{fn}",
                           app.ev(rel, line_of(text, start), "nextjs.server-action"),
                           location=app.loc(rel, s, e, line_of(text, start)), protocol="rpc",
                           operation=fn)
