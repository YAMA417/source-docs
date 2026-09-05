"""md2html.py のテスト。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "source-docs", "scripts"))
import md2html  # noqa: E402


class RenderInlineTest(unittest.TestCase):
    def test_code(self):
        self.assertEqual(md2html.render_inline("`x`"), "<code>x</code>")

    def test_bold(self):
        self.assertEqual(md2html.render_inline("**強い**"), "<strong>強い</strong>")

    def test_link(self):
        self.assertEqual(md2html.render_inline("[a](b.md)"), '<a href="b.html">a</a>')

    def test_escapes_html(self):
        self.assertEqual(md2html.render_inline("a < b"), "a &lt; b")

    def test_code_content_is_escaped(self):
        self.assertEqual(md2html.render_inline("`<a>`"), "<code>&lt;a&gt;</code>")


class ParseBlocksTest(unittest.TestCase):
    def test_heading(self):
        self.assertEqual(md2html.parse_blocks("## 概要"), [("heading", 2, "概要")])

    def test_table(self):
        md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        blocks = md2html.parse_blocks(md)
        self.assertEqual(blocks[0][0], "table")
        self.assertEqual(blocks[0][1], [["a", "b"], ["1", "2"]])

    def test_mermaid_block(self):
        md = "```mermaid\nflowchart TD\n  A --> B\n```"
        blocks = md2html.parse_blocks(md)
        self.assertEqual(blocks[0][0], "code")
        self.assertEqual(blocks[0][1], "mermaid")

    def test_unordered_list(self):
        blocks = md2html.parse_blocks("- 一\n- 二")
        self.assertEqual(blocks[0], ("ul", ["一", "二"]))


class ConvertTest(unittest.TestCase):
    def test_title_from_first_h1(self):
        title, body = md2html.convert("# DB 定義\n\n本文")
        self.assertEqual(title, "DB 定義")
        self.assertIn("<p>本文</p>", body)

    def test_mermaid_becomes_pre_mermaid(self):
        _, body = md2html.convert("```mermaid\nflowchart TD\n```")
        self.assertIn('<pre class="mermaid">', body)

    def test_mermaid_quotes_are_not_escaped(self):
        """pre の中で引用符をエスケープしない。Mermaid のラベルが壊れる。"""
        _, body = md2html.convert('```mermaid\nA --> B : "ラベル"\n```')
        self.assertIn('"ラベル"', body)
        self.assertNotIn("&quot;", body)

    def test_code_block_still_escapes_angle_brackets(self):
        _, body = md2html.convert("```ts\nconst a: Array<string> = [];\n```")
        self.assertIn("Array&lt;string&gt;", body)

    def test_table_scrolls(self):
        _, body = md2html.convert("| a |\n| --- |\n| 1 |")
        self.assertIn('class="scroll"', body)


class WriteSiteTest(unittest.TestCase):
    def test_creates_index_and_pages(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            with open(os.path.join(src, "database.md"), "w", encoding="utf-8") as f:
                f.write("# DB 定義\n\n本文\n")
            with open(os.path.join(src, "api.md"), "w", encoding="utf-8") as f:
                f.write("# API 仕様\n\n本文\n")

            written = md2html.write_site(src, out)

            self.assertTrue(os.path.isfile(os.path.join(out, "index.html")))
            self.assertTrue(os.path.isfile(os.path.join(out, "database.html")))
            self.assertTrue(os.path.isfile(os.path.join(out, "style.css")))
            self.assertEqual(len(written), 4)

            with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
                index = f.read()
            self.assertIn("DB 定義", index)
            self.assertIn('href="api.html"', index)


class LinkSafetyTest(unittest.TestCase):
    def test_javascript_scheme_is_neutralized(self):
        out = md2html.render_inline("[click](javascript:alert(1))")
        self.assertNotIn("javascript:", out.lower())

    def test_data_scheme_is_neutralized(self):
        out = md2html.render_inline("[x](data:text/html;base64,PHNjcmlwdD4=)")
        self.assertNotIn("data:", out.lower())

    def test_href_is_attribute_escaped(self):
        """href の中の引用符でタグを抜けられない。"""
        out = md2html.render_inline('[x](https://a.test/" onmouseover="alert(1))')
        self.assertNotIn('onmouseover="alert', out)

    def test_ordinary_links_still_work(self):
        self.assertEqual(
            md2html.render_inline("[a](https://example.test/x)"),
            '<a href="https://example.test/x">a</a>',
        )

    def test_relative_link_still_works(self):
        self.assertEqual(
            md2html.render_inline("[a](database.md)"),
            '<a href="database.html">a</a>',
        )

    def test_link_label_is_escaped(self):
        out = md2html.render_inline("[<img src=x onerror=alert(1)>](a.md)")
        self.assertNotIn("<img", out)


class OutputSafetyTest(unittest.TestCase):
    def test_refuses_output_into_git_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as src:
            with open(os.path.join(src, "a.md"), "w", encoding="utf-8") as f:
                f.write("# A\n")
            with self.assertRaises(SystemExit):
                md2html.write_site(src, os.path.join(src, ".git", "out"))

    def test_reports_overwritten_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            with open(os.path.join(src, "a.md"), "w", encoding="utf-8") as f:
                f.write("# A\n")
            with open(os.path.join(out, "a.html"), "w", encoding="utf-8") as f:
                f.write("既存の内容")
            overwritten = md2html.existing_targets(src, out)
            self.assertIn("a.html", [os.path.basename(p) for p in overwritten])
