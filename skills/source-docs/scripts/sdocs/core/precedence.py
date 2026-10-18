"""同じ情報を複数の情報源から取れるときの優先順位。情報の種類ごとに決める。

一律の優先順位にはしない。DB の物理状態と API の入力検証では、信頼できる情報源が違うため。
ここに無い組み合わせで新しい情報源を足すときは、まずこの表を書き換える。
"""

# 種類 → 情報源（左ほど強い）
RULES = {
    # DB の物理状態（カラム・NULL 可否・既定値・型・一意・インデックス・物理 FK）。
    # 実際の DB に適用された変更の記録である migration を最優先する。ORM の relation は物理状態の
    # 根拠にしない（DB に FK があるとは限らない）。別の概念 `ormRelations` として持つ
    "db.physical": ("migration", "orm-schema", "framework-schema"),
    "db.relation": ("orm-relation",),
    # API の入力。実行時に検証しているスキーマ（zod など）を最優先する
    "api.request": ("runtime-schema", "handler-validation", "type-definition", "annotation"),
    # 画面遷移。実際の遷移コード（Link / router.push / redirect）を最優先する。部品に渡した href と、
    # 動的ルート（`/*slug`）での受け止めは inferred。命名やディレクトリ構成だけからの推測は使わない
    "screen.transition": ("navigation-code", "component-property", "route-pattern"),
    # 外部連携と環境変数。クライアントの初期化と呼び出しの引数を最優先する。
    # 同じファイルにあるだけ・名前が似ているだけでは確定させない
    "integration.env": ("client-init", "call-site", "init-variable", "same-scope", "same-file", "naming"),
}

# 情報源 → その情報源だけで決めたときの状態
SOURCE_STATUS = {
    "migration": "confirmed", "orm-schema": "confirmed", "framework-schema": "confirmed",
    "orm-relation": "confirmed",
    "runtime-schema": "confirmed", "handler-validation": "confirmed", "type-definition": "inferred",
    "annotation": "confirmed",
    "navigation-code": "confirmed", "component-property": "inferred", "route-pattern": "inferred",
    "client-init": "confirmed", "call-site": "confirmed", "init-variable": "inferred",
    "same-scope": "unresolved", "same-file": "unresolved", "naming": "unresolved",
}

# 解析器ごとの情報源の分類（DB）
DB_SOURCE_KIND = {
    "migration": "migration",
    "prisma": "orm-schema", "drizzle": "orm-schema", "sqlalchemy": "orm-schema",
    "strapi": "framework-schema",
}


def rank(rule, source):
    """小さいほど強い。表に無い情報源は最も弱い。"""
    order = RULES[rule]
    return order.index(source) if source in order else len(order)


def strongest(rule, sources):
    """情報源の集まりのうち、最も強いもの。"""
    sources = [s for s in sources if s]
    return min(sources, key=lambda s: rank(rule, s)) if sources else None


def status_of(source):
    return SOURCE_STATUS.get(source, "unresolved")
