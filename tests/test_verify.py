"""verify.py の抽出ロジックのテスト。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "source-docs", "scripts"))
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


class VerticalTableTest(unittest.TestCase):
    """flow の「関連」表は縦持ち。行ラベルが `テーブル` の行から名前を拾う。"""

    def test_row_label_table(self):
        doc = """
| 種別 | 名前 |
| --- | --- |
| API | `POST /api/articles` |
| テーブル | `articles` (C)、`files` (C) |
| 外部連携 | Strapi |
"""
        tables = verify.extract_tables(doc)
        self.assertIn("articles", tables)
        self.assertIn("files", tables)
        self.assertNotIn("Strapi", tables)

    def test_label_must_be_exactly_the_word(self):
        """「3 テーブル」のような説明文を行ラベルと誤認しない。"""
        doc = """
| 対象 | 分からなかったこと |
| --- | --- |
| `damage_effect`（3 テーブル） | JSON の構造 |
"""
        self.assertEqual(verify.extract_tables(doc), [])


class EndpointTest(unittest.TestCase):
    def test_extracts_method_and_path(self):
        doc = "| `POST` | `/api/orders` | 注文を確定する |"
        self.assertIn(("POST", "/api/orders"), verify.extract_endpoints(doc))


    def test_bracket_path_parameter(self):
        """`[documentId]` 記法のパスを途中で切らない。

        Next.js のルートをそのまま資料に書く案件がある。`[` を拾えないと
        `/api/articles/` までしか読めず、別のルートとして照合してしまう。
        """
        doc = "| DELETE | `/api/articles/[documentId]` | 削除 |"
        self.assertIn(
            ("DELETE", "/api/articles/[documentId]"), verify.extract_endpoints(doc)
        )


class SecretFileSkipTest(unittest.TestCase):
    def test_load_sources_skips_secret_files(self):
        """.env や鍵ファイルは読み込まない。"""
        import tempfile
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "app.ts"), "w", encoding="utf-8") as f:
                f.write("const table = 'orders';")
            with open(os.path.join(repo, ".env"), "w", encoding="utf-8") as f:
                f.write("SECRET_TOKEN=abcdef0123456789")
            with open(os.path.join(repo, "server.pem"), "w", encoding="utf-8") as f:
                f.write("-----BEGIN PRIVATE KEY-----")

            sources, count = verify.load_sources([repo])
            self.assertIn("orders", sources)
            self.assertNotIn("SECRET_TOKEN", sources)
            self.assertNotIn("BEGIN PRIVATE KEY", sources)
            self.assertEqual(count, 1)


class HttpMethodTest(unittest.TestCase):
    def test_method_mismatch_is_detected(self):
        """GET でしか定義されていないパスを POST と書いたら不一致にする。"""
        sources = 'router.get("/only-get", handler)'
        self.assertFalse(verify.endpoint_exists("POST", "/only-get", sources))

    def test_matching_method_passes(self):
        sources = 'router.post("/orders", handler)'
        self.assertTrue(verify.endpoint_exists("POST", "/orders", sources))

    def test_method_unknown_falls_back_to_path(self):
        """メソッドが読み取れない書き方なら、パスの照合だけで通す。"""
        sources = 'export const routes = { "/orders": handler }'
        self.assertTrue(verify.endpoint_exists("POST", "/orders", sources))


class DividerTest(unittest.TestCase):
    def test_empty_row_is_not_a_divider(self):
        doc = "| テーブル | 用途 |\n| --- | --- |\n|  |  |\n| `orders` | 注文 |\n"
        self.assertIn("orders", verify.extract_tables(doc))


class EnvExampleTest(unittest.TestCase):
    def test_env_example_is_read(self):
        """`.env.example` は変数名の照合に使うので読み込む。

        `os.path.splitext(".env.example")` は `(".env", ".example")` を返すため、
        拡張子の集合では拾えない。ファイル名で判定する。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, ".env.example"), "w", encoding="utf-8") as f:
                f.write("STRIPE_WEBHOOK_ENDPOINT=\n")
            sources, count = verify.load_sources([repo])
            self.assertIn("STRIPE_WEBHOOK_ENDPOINT", sources)
            self.assertEqual(count, 1)

    def test_real_env_is_still_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, ".env.production"), "w", encoding="utf-8") as f:
                f.write("SECRET=zzz\n")
            sources, count = verify.load_sources([repo])
            self.assertEqual(count, 0)
            self.assertNotIn("SECRET", sources)


class MermaidFenceTest(unittest.TestCase):
    """シーケンス図の中身は照合対象にしない。

    detail モードの図には外部サービスへの呼び出しや概念上のパスが出る。
    対象リポジトリに実装が無いのは当たり前なので、拾うと必ず誤検知になる。
    """

    def test_endpoint_inside_mermaid_is_ignored(self):
        doc = (
            "```mermaid\n"
            "sequenceDiagram\n"
            "  画面->>決済サービス: POST /v1/payment_intents\n"
            "```\n"
        )
        self.assertEqual(verify.extract_endpoints(doc), [])

    def test_endpoint_outside_mermaid_is_kept(self):
        doc = (
            "| `POST` | `/api/orders` | 注文 |\n"
            "```mermaid\n"
            "flowchart TD\n"
            "  A-->B\n"
            "```\n"
        )
        self.assertIn(("POST", "/api/orders"), verify.extract_endpoints(doc))

    def test_function_heading_is_not_a_table(self):
        """`### create_order — 注文確定処理` は関数の見出しでテーブルではない。"""
        doc = "### `create_order()` — 注文確定処理\n"
        self.assertEqual(verify.extract_tables(doc), [])


class ErrorTypeTest(unittest.TestCase):
    def test_env_var_name_is_not_an_error_type(self):
        """環境変数名は errorType ではない。名前を書くのは正しい書き方。"""
        doc = "認証には `STRIPE_SECRET_KEY` を使う。`DATABASE_URL` も要る。\n"
        self.assertEqual(verify.extract_error_types(doc), [])

    def test_real_error_type_is_kept(self):
        doc = "`ORDER_ALREADY_PAID` を返す。\n"
        self.assertIn("ORDER_ALREADY_PAID", verify.extract_error_types(doc))


class NextRouteHandlerTest(unittest.TestCase):
    """Next.js App Router は `export async function POST` でメソッドを表す。"""

    @staticmethod
    def _sources(rel, body):
        """load_sources と同じ、ファイルの切れ目を印で示した形にする。"""
        return verify.FILE_MARKER + rel + "\n" + body

    def test_named_export_method_matches(self):
        sources = self._sources(
            "app/api/articles/route.ts", "export async function POST(req) {}\n"
        )
        self.assertTrue(verify.endpoint_exists("POST", "/api/articles", sources))

    def test_named_export_method_conflict_is_detected(self):
        sources = self._sources(
            "app/api/articles/route.ts", "export async function GET(req) {}\n"
        )
        self.assertFalse(verify.endpoint_exists("DELETE", "/api/articles", sources))

    def test_deeper_route_does_not_satisfy_shallower_path(self):
        """`/api/articles/{id}` の DELETE は `/api/articles` の DELETE ではない。

        セグメントの包含だけで判定すると、より深い階層のルートファイルを
        同じパスの定義とみなしてしまう。
        """
        sources = (
            self._sources("app/api/articles/route.ts", "export async function GET() {}\n")
            + self._sources(
                "app/api/articles/[documentId]/route.ts",
                "export async function DELETE() {}\n",
            )
        )
        self.assertFalse(verify.endpoint_exists("DELETE", "/api/articles", sources))
        self.assertTrue(verify.endpoint_exists("DELETE", "/api/articles/{id}", sources))

    def test_test_files_do_not_declare_routes(self):
        """`route.test.ts` は入口ではない。メソッドの宣言とみなさない。"""
        sources = (
            self._sources("app/api/articles/route.ts", "export async function GET() {}\n")
            + self._sources(
                "app/api/articles/route.test.ts",
                "export async function DELETE() {}\n",
            )
        )
        self.assertFalse(verify.endpoint_exists("DELETE", "/api/articles", sources))

    def test_route_group_is_ignored(self):
        """`(marketing)` のようなグループはパスに出ない。"""
        sources = self._sources(
            "app/(shop)/api/cart/route.ts", "export async function POST() {}\n"
        )
        self.assertTrue(verify.endpoint_exists("POST", "/api/cart", sources))

class ImplementationPathTest(unittest.TestCase):
    """detail モードは実装ファイルのパスを書く。そのパスが実在するか確かめる。"""

    def test_extracts_path_like_inline_code(self):
        doc = "処理は `frontend/app/api/articles/route.ts` にある。\n"
        self.assertIn("frontend/app/api/articles/route.ts", verify.extract_paths(doc))

    def test_ignores_endpoint_paths(self):
        doc = "`POST /api/orders` を呼ぶ。\n"
        self.assertEqual(verify.extract_paths(doc), [])

    def test_missing_path_is_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "src"))
            with open(os.path.join(repo, "src", "real.ts"), "w", encoding="utf-8") as f:
                f.write("x")
            self.assertTrue(verify.path_exists("src/real.ts", [repo]))
            self.assertFalse(verify.path_exists("src/ghost.ts", [repo]))


class ModeTest(unittest.TestCase):
    def test_structure_checks_error_types(self):
        self.assertIn("errorType", verify.CHECKS["structure"])

    def test_detail_checks_paths(self):
        self.assertIn("path", verify.CHECKS["detail"])

    def test_flow_does_not_check_error_types(self):
        self.assertNotIn("errorType", verify.CHECKS["flow"])
