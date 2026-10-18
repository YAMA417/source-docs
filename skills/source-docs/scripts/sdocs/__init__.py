"""source-docs の解析・検証・描画を担うパッケージ。

処理の流れ。

    ソースコード
      → detectors（フレームワーク固有の検出）
      → core（Project Model・DB の最終状態・関係と機能の解決・Fact・品質ゲート）
      → renderers（Markdown / HTML / JSON）
      → validators（注釈・網羅率・健全性・照合・機密の検査）

依存は Python 3 の標準ライブラリだけ。
"""

MODEL_SCHEMA = "source-docs/project-model"
MODEL_VERSION = 2

ANNOTATIONS_SCHEMA = "source-docs/annotations"
ANNOTATIONS_VERSION = 1
