"""フレームワーク・言語ごとの検出器の一覧。

新しいスタックに対応するときは、`base.Adapter` を継承したクラスを作り、
ここの `all_adapters()` に足す。中核（analyzer / relations / features / render）は
フレームワーク名を知らないので、触らなくてよい。
"""

from .background_js import BackgroundJs
from .file_routing import ExpoRouter, NextJs
from .integrations import Integrations
from .js_http import JsHttp
from .lang_js import JsLanguage
from .orm_js import Drizzle, Prisma
from .platforms import Strapi, Supabase
from .python_go import GoGin, PythonWeb
from .sql import Sql


def all_adapters():
    """毎回新しいインスタンスを返す。アダプタは解析 1 回分の状態を持つため。"""
    return [
        JsLanguage(), NextJs(), ExpoRouter(), JsHttp(), Strapi(), Supabase(),
        Drizzle(), Prisma(), Sql(), BackgroundJs(), PythonWeb(), GoGin(), Integrations(),
    ]
