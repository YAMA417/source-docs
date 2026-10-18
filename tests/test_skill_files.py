"""スキル・プラグインのファイル構成が壊れていないことを確かめる。"""

import json
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SKILL = os.path.join(ROOT, "skills", "source-docs")
REFERENCES = ["annotations", "writing", "structure", "flow", "detail"]


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


class SkillMdTest(unittest.TestCase):
    def setUp(self):
        self.text = read(SKILL, "SKILL.md")

    def test_frontmatter(self):
        self.assertTrue(self.text.startswith("---\n"))
        self.assertIn("name: source-docs", self.text)

    def test_under_line_limit(self):
        """常時コンテキストに載るため 250 行以内に収める。"""
        self.assertLessEqual(len(self.text.splitlines()), 250)

    def test_referenced_files_exist(self):
        for match in re.findall(r"`((?:references|scripts)/[\w./-]+)`", self.text):
            if match.endswith("/"):
                continue
            self.assertTrue(os.path.exists(os.path.join(SKILL, match)), f"SKILL.md が参照する {match} が無い")

    def test_natural_language_triggers(self):
        head = self.text.split("---")[1]
        for phrase in ("設計書を作って", "機能一覧を出して", "詳細設計を書いて", "/source-docs"):
            self.assertIn(phrase, head)

    def test_commands_cover_targets(self):
        for target in ("structure", "features", "db", "api", "screens", "integrations", "flow", "detail",
                       "coverage"):
            self.assertIn(f"`{target}", self.text)

    def test_claude_writes_annotations_not_markdown(self):
        self.assertIn("あなたが書くのは `annotations.json` だけ", self.text)

    def test_unknown_first_argument_is_not_guessed(self):
        self.assertIn("勝手に解釈せず", self.text)

    def test_windows_python_fallback(self):
        self.assertIn("`py`", self.text)

    def test_does_not_hardcode_personal_paths(self):
        self.assertNotIn("C:\\Users", self.text)
        self.assertNotIn("/home/", self.text)


class ReferenceTest(unittest.TestCase):
    def test_all_exist(self):
        for name in REFERENCES:
            self.assertTrue(os.path.isfile(os.path.join(SKILL, "references", f"{name}.md")), name)

    def test_writing_states_precedence_and_secrets(self):
        text = read(SKILL, "references", "writing.md")
        self.assertIn("優先順位", text)
        self.assertIn("機密", text)
        self.assertIn("searched", text)

    def test_mode_boundaries(self):
        self.assertIn("内部での実現方法は書かない", read(SKILL, "references", "structure.md"))
        self.assertIn("5 行", read(SKILL, "references", "flow.md"))
        detail = read(SKILL, "references", "detail.md")
        self.assertIn("内部の処理", detail)
        self.assertIn("実装ファイルのパス", detail)

    def test_annotation_enums_match_checker(self):
        """references に書いた選択肢と、検査器が受け付ける値が一致すること。"""
        import sys
        sys.path.insert(0, os.path.join(SKILL, "scripts"))
        from sdocs.validators import annotations as a
        detail = read(SKILL, "references", "detail.md")
        for values in (a.STATUS, a.PRECONDITION_KINDS, a.SIDE_EFFECT_KINDS, a.CONCURRENCY_KINDS,
                       a.STEP_KINDS, a.PARTICIPANT_KINDS):
            self.assertIn("|".join(values), detail)


class PluginTest(unittest.TestCase):
    def test_manifest(self):
        data = json.loads(read(ROOT, ".claude-plugin", "plugin.json"))
        self.assertEqual(data["name"], "source-docs")
        self.assertRegex(data["version"], r"^\d+\.\d+\.\d+$")

    def test_marketplace_points_to_plugin(self):
        data = json.loads(read(ROOT, ".claude-plugin", "marketplace.json"))
        self.assertEqual(data["plugins"][0]["name"], "source-docs")
        self.assertEqual(data["plugins"][0]["source"], "./")


if __name__ == "__main__":
    unittest.main()
