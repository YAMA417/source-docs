"""事実の状態・未確認の種類・診断の重大度。

すべての事実（実体・関係・環境変数の紐付け・カラム）は次のどれかの状態を持つ。
資料の本文に確定情報として書くのは `ACCEPTED` だけ。それ以外は「未確定」として別に示す。

- confirmed   コード上の十分な根拠で確定できる（宣言そのもの、入口のコードで直接使っている）
- inferred    直接の宣言ではないが、実装上の関係から根拠付きで判断できる（import をたどった先、規約）
- unresolved  候補はあるが確定できない（行き先が複数、行き先が見つからない、同じファイルにあるだけ）
- conflict    複数の根拠が矛盾している（注釈での異議、同じ優先度の情報源どうしの不一致）
- unsupported 現在の解析器では判断できない
"""

STATUSES = ("confirmed", "inferred", "unresolved", "conflict", "unsupported")
ACCEPTED = ("confirmed", "inferred")
PENDING = ("unresolved", "conflict", "unsupported")

# 未確認の種類。「調査不足」と「リポジトリの外の責務」を区別する
UNVERIFIED_KINDS = (
    "unresolved",          # 候補はあるが決められない
    "library-owned",       # ライブラリの内部で決まる（リポジトリに無い）
    "runtime-dependent",   # 実行時の値・環境で決まる
    "framework-default",   # フレームワークの既定値で決まる
    "conflict",            # 根拠どうしが矛盾している
    "unsupported",         # 解析器が読めない書き方
    "not-found",           # 探したが見つからなかった
)

UNVERIFIED_LABEL = {
    "unresolved": "未解決", "library-owned": "ライブラリ側", "runtime-dependent": "実行時に決まる",
    "framework-default": "フレームワーク既定", "conflict": "矛盾", "unsupported": "解析対象外",
    "not-found": "見つからない",
}

STATUS_LABEL = {
    "confirmed": "確定", "inferred": "推定（根拠あり）", "unresolved": "未解決",
    "conflict": "矛盾", "unsupported": "解析対象外",
}

SEVERITIES = ("INFO", "WARNING", "ERROR")

_RANK = {s: i for i, s in enumerate(STATUSES)}


def stronger(a, b):
    """2 つの状態のうち、確からしい方。同じ事実を別の根拠でも見つけたときに使う。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if _RANK[a] <= _RANK[b] else b


def accepted(status):
    return status in ACCEPTED


def diagnostic(severity, code, message, entity=None, evidence=None):
    assert severity in SEVERITIES, severity
    out = {"severity": severity, "code": code, "message": message}
    if entity:
        out["entity"] = entity
    if evidence:
        out["evidence"] = evidence
    return out


# 未確定の項目（pending の field / Fact の述語）の表示名
PENDING_FIELD = {"calls": "呼び出す API", "links": "遷移先", "tables": "テーブル操作",
                 "integrations": "外部連携", "publishes": "イベント発行", "enqueues": "キュー投入",
                 "envVars": "環境変数", "columns": "カラム", "physicalForeignKeys": "外部キー",
                 "ormRelations": "ORM の関係", "request": "リクエスト",
                 "unauthenticated_status": "未ログイン時の応答"}
