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


class ReferenceTest(unittest.TestCase):
    def test_all_references_exist(self):
        for name in REFERENCES:
            path = os.path.join(SKILL, "references", f"{name}.md")
            self.assertTrue(os.path.isfile(path), f"missing: {path}")

    def test_stacks_dir_exists(self):
        self.assertTrue(os.path.isdir(os.path.join(SKILL, "references", "stacks")))


class SkillMdTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            self.text = f.read()

    def test_has_frontmatter_with_name(self):
        self.assertTrue(self.text.startswith("---\n"))
        self.assertIn("name: source-docs", self.text)

    def test_under_line_limit(self):
        """常時コンテキストに載るため 250 行以内に収める。"""
        self.assertLessEqual(len(self.text.splitlines()), 250)

    def test_referenced_files_exist(self):
        """SKILL.md が名前を挙げる references / templates / scripts が実在すること。"""
        pattern = r"`(references/[\w./-]+|templates/[\w./-]+|scripts/[\w./-]+)`"
        for match in re.findall(pattern, self.text):
            if match.endswith("/"):
                continue
            path = os.path.join(SKILL, match)
            self.assertTrue(
                os.path.exists(path), f"SKILL.md が参照する {match} が存在しない"
            )

    def test_does_not_hardcode_personal_paths(self):
        """個人環境のパスを使用例に書かない。"""
        self.assertNotIn("ymnk417", self.text)
        self.assertNotIn("poke-dex-battle", self.text)
