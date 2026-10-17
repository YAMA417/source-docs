"""source-docs の解析・検証・描画を担うパッケージ。

処理の流れ。

    ソースコード
      → adapters（フレームワーク固有の検出）
      → analyzer（Project Model へ正規化）
      → relations / features（関係と機能の解決）
      → annotations（Claude が書いた意味づけの検査）
      → render（Markdown / HTML / JSON）
      → coverage / verify（source ↔ docs の突き合わせ）

依存は Python 3 の標準ライブラリだけ。
"""

MODEL_SCHEMA = "source-docs/project-model"
MODEL_VERSION = 1

ANNOTATIONS_SCHEMA = "source-docs/annotations"
ANNOTATIONS_VERSION = 1
