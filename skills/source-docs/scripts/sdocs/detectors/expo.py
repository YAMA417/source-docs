"""Expo Router。`app/` 配下のファイルがそのまま画面になる。`+api.ts` は API ルート。"""

import re

from .base import Adapter, route_from_dirs
from .nextjs import EXPO_NOT_A_SCREEN, RE_DEFAULT_NAME, _add_methods, _app_dir


class ExpoRouter(Adapter):
    name = "expo-router"

    def applies(self, app):
        return app.has_dep("expo-router")

    def collect(self, app, b):
        for rel in app.files:
            inner = _app_dir(app, rel)
            if inner is None or not re.search(r"\.(tsx|jsx|ts|js)$", inner):
                continue
            parts = inner.split("/")
            base = re.sub(r"\.(tsx|jsx|ts|js)$", "", parts[-1])
            if base.endswith("+api"):
                name = base[:-len("+api")]
                dirs = parts[:-1] + ([] if name == "index" else [name])
                _add_methods(app, b, rel, route_from_dirs(dirs), "expo.api-route")
                continue
            if base in EXPO_NOT_A_SCREEN or base.startswith("_") or base.startswith("+"):
                continue
            if not inner.endswith((".tsx", ".jsx")):
                continue
            dirs = parts[:-1] + ([] if base == "index" else [base])
            text = app.read(rel)
            dn = RE_DEFAULT_NAME.search(text)
            b.add_screen(app.ref, route_from_dirs(dirs), app.ev(rel, rule="expo.screen"),
                         location=app.loc(rel, 0, len(text), 1), name=dn.group(1) if dn else None)
