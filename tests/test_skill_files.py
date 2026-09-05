"""スキルのファイル構成が壊れていないことを確かめる。"""

import os
import re
import unittest

SKILL = os.path.join(os.path.dirname(__file__), "..", "skill")

TEMPLATES = ["overview", "database", "api", "screens", "integrations"]
REFERENCES = ["detect", "db", "api", "screens", "integrations", "writing"]


class TemplateTest(unittest.TestCase):
    def test_all_templates_exist(self):
        for name in TEMPLATES:
            path = os.path.join(SKILL, "templates", f"{name}.md")
            self.assertTrue(os.path.isfile(path), f"missing: {path}")

    def test_templates_have_headings(self):
        for name in TEMPLATES:
            with open(os.path.join(SKILL, "templates", f"{name}.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertRegex(text, r"(?m)^## ", f"{name}.md に章見出しがない")

    def test_no_heading_deeper_than_h3(self):
        """出力 md の見出しは ### まで。テンプレート自身もそれに従う。"""
        for name in TEMPLATES:
            with open(os.path.join(SKILL, "templates", f"{name}.md"), encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if re.match(r"^#{4,}\s", line):
                        self.fail(f"{name}.md:{lineno} 見出しが h4 以上")
