"""Go。Gin のルート（Group による前置パスを含む）。"""

import re

from .base import Adapter, line_of, segments


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
