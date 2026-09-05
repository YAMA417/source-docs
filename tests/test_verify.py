"""verify.py の抽出ロジックのテスト。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "scripts"))
import verify  # noqa: E402


class StripSchemaTest(unittest.TestCase):
    def test_removes_schema_prefix(self):
        self.assertEqual(verify.strip_schema("public.orders"), "orders")

    def test_keeps_plain_name(self):
        self.assertEqual(verify.strip_schema("orders"), "orders")


class ExtractTablesTest(unittest.TestCase):
    def test_header_with_suffix(self):
        doc = """
| 画面 | 操作 | API | テーブル（CRUD） |
| --- | --- | --- | --- |
| カート | 表示 | `GET /api/cart` | cart_items (R) |
"""
        self.assertIn("cart_items", verify.extract_tables(doc))

    def test_heading_form(self):
        doc = "### `public.orders` — 注文\n\n本文\n"
        self.assertIn("public.orders", verify.extract_tables(doc))

    def test_heading_without_schema(self):
        doc = "### `books` — 書籍マスタ\n"
        self.assertIn("books", verify.extract_tables(doc))

    def test_body_cell_containing_table_word_is_not_a_header(self):
        """本文セルに「テーブル」の語があってもヘッダーと誤認しない。

        未確認一覧の「`damage_effect`（3 テーブル）」のような行で、
        以降の全行をテーブル名として拾ってしまう誤検知を防ぐ。
        """
        doc = """
| 対象 | 分からなかったこと |
| --- | --- |
| `damage_effect`（3 テーブル） | JSON の構造 |
| Index の不在 | 意図的かどうか |
"""
        self.assertEqual(verify.extract_tables(doc), [])

    def test_ignores_non_table_heading(self):
        doc = "### 3. テーブル一覧\n"
        self.assertEqual(verify.extract_tables(doc), [])


class EndpointTest(unittest.TestCase):
    def test_extracts_method_and_path(self):
        doc = "| `POST` | `/api/orders` | 注文を確定する |"
        self.assertIn(("POST", "/api/orders"), verify.extract_endpoints(doc))
