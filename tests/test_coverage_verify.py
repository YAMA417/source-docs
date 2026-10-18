"""coverage（source → docs）と verify（docs → source）のテスト。"""

import json
import os
import re
import shutil
import tempfile
import unittest

from helpers import ShopDocs, analyze_files, write_project

from sdocs.core.analyzer import analyze
from sdocs.validators.coverage import compute, format_text, read_docs
from sdocs.core.model import model_path, write_json
from sdocs.renderers import render_docs
from sdocs.validators.verify import extract_tables, verify
from sdocs.core.resolve import resolve


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.docs = ShopDocs()
        self.docs.render()

    def tearDown(self):
        self.docs.cleanup()

    def cov(self):
        return compute(self.docs.model, self.docs.annotations(), self.docs.out)["detection"]

    def test_freshly_rendered_docs_are_complete(self):
        for kind, r in self.cov().items():
            with self.subTest(kind=kind):
                self.assertEqual(r["undocumented"], [])
                self.assertEqual(r["coverage"], 1.0)

    def test_excluded_is_not_in_denominator(self):
        api = self.cov()["api"]
        self.assertEqual(api["detected"], 4)
        self.assertEqual(api["excluded"], [{"id": "endpoint:GET /api/health",
                                            "reason": "死活監視用で、利用者向けの機能ではない"}])
        self.assertEqual(api["documented"], 3)

    def test_described_counts_annotations(self):
        cov = self.cov()
        self.assertEqual(cov["api"]["described"], 1)
        self.assertEqual(cov["features"]["described"], 2)

    def test_removed_endpoint_is_undocumented(self):
        """資料から API の記述を消すと、undocumented として出る。"""
        for rel in list(read_docs(self.docs.out)):
            path = os.path.join(self.docs.out, *rel.split("/"))
            text = open(path, encoding="utf-8").read()
            text = re.sub(r"[^\n]*GET[^\n]*/api/products[^\n]*\n", "", text)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        api = self.cov()["api"]
        self.assertEqual(api["undocumented"], ["endpoint:GET /api/products"])
        self.assertAlmostEqual(api["coverage"], round(2 / 3, 4))

    def test_new_code_after_render_is_detected(self):
        """資料を作ったあとにコードが増えると、再解析で漏れとして出る（資料の鮮度の検査）。"""
        root = write_project({"package.json": json.dumps({"dependencies": {"next": "15"}}),
                              "app/api/a/route.ts": "export async function GET() {}\n"})
        out = os.path.join(root, "docs", "system")
        try:
            m = analyze([root], out)
            render_docs(resolve(m, {}), out)
            with open(os.path.join(root, "app", "api", "a", "route.ts"), "a", encoding="utf-8") as f:
                f.write("export async function DELETE() {}\n")
            m2 = analyze([root], out)
            api = compute(m2, {}, out)["detection"]["api"]
            self.assertEqual(api["undocumented"], ["endpoint:DELETE /api/a"])
        finally:
            shutil.rmtree(root)

    def test_text_output(self):
        text = format_text(compute(self.docs.model, self.docs.annotations(), self.docs.out))
        self.assertIn("API: detected 4 / documented 3 / coverage 100.0%", text)
        self.assertIn("excluded:", text)


class CoverageCategoriesTest(unittest.TestCase):
    def test_unresolved_and_unsupported_are_separate(self):
        root = write_project({
            "package.json": json.dumps({"dependencies": {"next": "15"}}),
            "pages/api/any.ts": "export default function h(req, res) {}\n",
            "app/api/broken/route.ts": "export const x = 1;\n",
            "app/api/ok/route.ts": "export async function GET() {}\n",
        })
        out = tempfile.mkdtemp()
        try:
            m = analyze([root], out)
            render_docs(resolve(m, {}), out)
            api = compute(m, {}, out)["detection"]["api"]
            self.assertEqual([u["id"] for u in api["unresolved"]], ["endpoint:ANY /api/any"])
            self.assertEqual([u["file"] for u in api["unsupported"]], ["app/api/broken/route.ts"])
            self.assertEqual((api["detected"], api["documented"], api["coverage"]), (2, 1, 1.0))
        finally:
            shutil.rmtree(root)
            shutil.rmtree(out)

    def test_no_entities_means_no_ratio(self):
        out = tempfile.mkdtemp()
        try:
            m = analyze_files({"README.md": "x"})
            self.assertIsNone(compute(m, {}, out)["detection"]["api"]["coverage"])
        finally:
            shutil.rmtree(out)


class VerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = ShopDocs()
        cls.docs.render()

    @classmethod
    def tearDownClass(cls):
        cls.docs.cleanup()

    def test_generated_docs_verify_clean(self):
        result = verify(self.docs.model, self.docs.out, read_docs(self.docs.out))
        self.assertEqual(result["findings"], [])
        self.assertGreater(result["checked"]["endpoint"], 0)
        self.assertGreater(result["checked"]["path"], 0)

    def test_fabrications_are_flagged(self):
        doc = ("| テーブル | 操作 |\n| --- | --- |\n| `ghost_table` | CREATE |\n\n"
               "POST /api/ghost を呼ぶ。`src/ghost/file.ts` を見る。\n")
        result = verify(self.docs.model, self.docs.out, {"detail/x.md": doc})
        kinds = sorted((f["kind"], f["value"]) for f in result["findings"])
        self.assertEqual(kinds, [("endpoint", "POST /api/ghost"), ("path", "src/ghost/file.ts"),
                                 ("table", "ghost_table")])

    def test_header_row_is_not_a_table_name(self):
        """`| テーブル | 操作 | 変わるもの |` の見出し行の「操作」をテーブル名と取り違えない。"""
        doc = "| テーブル | 操作 | 変わるもの |\n| --- | --- | --- |\n| `orders` | INSERT | 1 行 |\n"
        self.assertEqual(extract_tables(doc), ["orders"])

    def test_vertical_table(self):
        doc = "| 項目 | 内容 |\n| --- | --- |\n| テーブル | orders (C), products (R) |\n"
        self.assertEqual(extract_tables(doc), ["orders", "products"])

    def test_count_columns_are_not_tables(self):
        doc = "| アプリ | テーブル数 |\n| --- | --- |\n| shop | 2 |\n"
        self.assertEqual(extract_tables(doc), [])

    def test_mermaid_is_not_verified(self):
        doc = "```mermaid\nsequenceDiagram\n    a->>b: POST /v1/external\n```\n"
        self.assertEqual(verify(self.docs.model, self.docs.out, {"detail/x.md": doc})["findings"], [])

    def test_param_styles_match(self):
        root = write_project({"package.json": json.dumps({"dependencies": {"next": "15"}}),
                              "app/api/orders/[id]/route.ts": "export async function GET() {}\n"})
        out = os.path.join(root, "docs", "system")
        try:
            m = analyze([root], out)
            write_json(model_path(out), m)
            for doc in ("GET /api/orders/{id}", "GET /api/orders/:orderId"):
                self.assertEqual(verify(m, out, {"api.md": doc})["findings"], [], doc)
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
