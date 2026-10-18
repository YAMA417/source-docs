"""フレームワーク・言語ごとの検出器の一覧。

新しいスタックに対応するときは、`base.Adapter` を継承したクラスを作り、
ここの `all_adapters()` に足す。中核（analyzer / relations / features / render）は
フレームワーク名を知らないので、触らなくてよい。
"""

from .auth import Auth
from .drizzle import Drizzle
from .expo import ExpoRouter
from .go import GoGin
from .integrations import Integrations
from .javascript import JsLanguage
from .jobs import BackgroundJs
from .nextjs import NextJs
from .node_http import JsHttp
from .prisma import Prisma
from .python import PythonWeb
from .sql import Sql
from .strapi import Strapi
from .supabase import Supabase
from .zod import Zod


def all_adapters():
    """毎回新しいインスタンスを返す。アダプタは解析 1 回分の状態を持つため。"""
    return [
        # Sql は ORM より先。両方が同じテーブルを定義するとき、マイグレーションを正とするため
        JsLanguage(), NextJs(), ExpoRouter(), JsHttp(), Strapi(), Supabase(),
        Sql(), Drizzle(), Prisma(), BackgroundJs(), PythonWeb(), GoGin(), Integrations(), Zod(), Auth(),
    ]
