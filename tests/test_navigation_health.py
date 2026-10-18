"""目次から詳細設計への導線・資料の状態（Overall Status）と品質ゲートの診断の分離・機能一覧の説明文の回帰テスト。

taxonomy の生成物で、利用者が読み違えた・辿れなかった箇所をそのまま検査する。
"""

import os
import re
import shutil
import tempfile
import unittest

from helpers import ShopDocs, analyze_files

from sdocs.core.quality import STATUS_RULES, status_of, status_reasons
from sdocs.core.resolve import resolve
from sdocs.renderers import html as html_mod, render_docs
from sdocs.validators.health import result_of
from test_generated_docs import TAXONOMY_LIKE, generate, html_section, md_section, read

LEGEND = "「名前未確定」はコードの識別子をそのまま名前にしている候補"


def detail_links(index_html):
    """index.html の「詳細設計」の節にあるリンク先。"""
    m = re.search(r"<h2>詳細設計</h2>(.*?)(?=<h2>|</div>)", index_html, re.S)
    return re.findall(r'href="([^"]+)"', m.group(1)) if m else None


class IndexNavigationTest(unittest.TestCase):
    def test_every_generated_detail_is_listed(self):
        """生成済みの detail をすべて目次に載せる。ファイル名は生成物から取り、テストに書かない。"""
        docs = ShopDocs()
        try:
            docs.render()
            # 2 件目の詳細設計を置き、ファイル名を決め打ちしていないことを確かめる
            detail_dir = os.path.join(docs.out, "detail")
            first = sorted(os.listdir(detail_dir))[0]
            shutil.copy(os.path.join(detail_dir, first), os.path.join(detail_dir, "another-feature.md"))
            generated = sorted(f"detail/{n[:-3]}.html" for n in os.listdir(detail_dir) if n.endswith(".md"))
            self.assertGreaterEqual(len(generated), 2)
            site = os.path.join(docs.out, "html")
            html_mod.write_site(docs.out, site)
            index = read(os.path.join(site, "index.html"))
            self.assertIn("<h2>全体</h2>", index)
            self.assertEqual(sorted(detail_links(index)), generated)
            for link in generated:
                self.assertTrue(os.path.isfile(os.path.join(site, *link.split("/"))), link)
        finally:
            docs.cleanup()

    def test_no_detail_yet(self):
        repo, out, p = generate({})
        try:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            index = read(os.path.join(out, "html", "index.html"))
            self.assertEqual(detail_links(index), [])
            self.assertIn("詳細設計はまだ生成されていません。", index)
        finally:
            shutil.rmtree(repo, ignore_errors=True)


class OverallStatusRuleTest(unittest.TestCase):
    def view(self, **counts):
        q = {"confirmed": 1, "inferred": 0, "unresolved": 0, "conflict": 0, "unsupported": 0, "errors": 0}
        q.update(counts)
        return {"quality": q}

    def test_rule(self):
        cases = [({}, "SAFE"), ({"unresolved": 5}, "WARNING"), ({"unsupported": 1}, "WARNING"),
                 ({"conflict": 1}, "FAILED"), ({"errors": 1}, "FAILED"),
                 ({"errors": 1, "unresolved": 5}, "FAILED"), ({"conflict": 1, "unsupported": 2}, "FAILED")]
        for counts, expected in cases:
            self.assertEqual(status_of(self.view(**counts)), expected, counts)

    def test_reasons_list_every_condition_in_rule_order(self):
        status, reasons = status_reasons(self.view(unresolved=5))
        self.assertEqual(status, "WARNING")
        self.assertEqual([label for label, _, _ in reasons],
                         [label for _, conds in STATUS_RULES for _, label in conds])
        self.assertIn(("未解決の事実（unresolved）", 5, "WARNING"), reasons)

    def test_deterministic(self):
        model = analyze_files(TAXONOMY_LIKE)
        results = {(resolve(model, {})["documentationStatus"],
                    tuple(status_reasons(resolve(model, {}))[1])) for _ in range(3)}
        self.assertEqual(len(results), 1)

    def test_check_does_not_soften_failed(self):
        """check の結果は、資料の状態が FAILED なら診断の内容によらず FAILED。"""
        self.assertEqual(result_of([], {"documentationStatus": "FAILED"}), "FAILED")
        self.assertEqual(result_of([], {"documentationStatus": "WARNING"}), "WARNING")
        self.assertEqual(result_of([{"severity": "WARNING"}], {"documentationStatus": "SAFE"}), "WARNING")
        self.assertEqual(result_of([], {"documentationStatus": "SAFE"}), "SAFE")


class HealthSeparationTest(unittest.TestCase):
    """未解決の事実はあるが、品質ゲートの WARNING は 0 件。この 2 つを別のものとして表示する。"""

    @classmethod
    def setUpClass(cls):
        view = resolve(analyze_files(TAXONOMY_LIKE), {})
        # 品質ゲートの WARNING が 0 件の状態を作る（未解決の事実は残す）
        view["diagnostics"] = [d for d in view["diagnostics"] if d["severity"] != "WARNING"]
        cls.unresolved = view["quality"]["unresolved"]
        assert cls.unresolved > 0 and view["documentationStatus"] == "WARNING"
        cls.out = tempfile.mkdtemp(prefix="sd-health-")
        render_docs(view, cls.out, only=["overview"])
        html_mod.write_site(cls.out, os.path.join(cls.out, "html"))
        cls.md = read(os.path.join(cls.out, "overview.md"))
        cls.html = read(os.path.join(cls.out, "html", "overview.html"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def sections(self):
        return (("md", md_section, self.md), ("html", html_section, self.html))

    def test_overall_status_with_reasons(self):
        for name, section, doc in self.sections():
            overall = section(doc, "資料の状態（Overall Status）")
            self.assertIsNotNone(overall, name)
            self.assertIn("WARNING", overall.split("理由")[0], name)
            self.assertIn(f"未解決の事実（unresolved）: {self.unresolved} 件（1 件以上なら WARNING）", overall, name)
            self.assertIn("根拠どうしの矛盾（conflict）: 0 件", overall, name)
            self.assertIn("品質ゲートの ERROR: 0 件", overall, name)

    def test_fact_status_table(self):
        fact = md_section(self.md, "事実の状態（Fact Status）")
        self.assertIn(f"| Unresolved | 未解決 | {self.unresolved} |", fact)
        self.assertIn("| Conflict | 矛盾 | 0 |", fact)

    def test_quality_gate_diagnostics_are_separate(self):
        diag_md = md_section(self.md, "品質ゲートの診断（Quality Gate Diagnostics）")
        for sev in ("ERROR", "WARNING", "INFO"):
            self.assertIn(f"| {sev} | 0 |", diag_md)
        self.assertIn("資料の状態（Overall Status）とは別のもの", diag_md)
        diag_html = re.search(r"品質ゲートの診断（Quality Gate Diagnostics）</h3>(.*?)<h3", self.html, re.S).group(1)
        cells = [re.sub(r"<[^>]+>", " ", r).split() for r in re.findall(r"<tr>(.*?)</tr>", diag_html, re.S)]
        self.assertEqual(cells, [["Severity", "件数"], ["ERROR", "0"], ["WARNING", "0"], ["INFO", "0"]])
        # 資料の状態の節には、診断の件数を混ぜない
        self.assertNotIn("Severity", md_section(self.md, "資料の状態（Overall Status）"))


class FeatureLegendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = analyze_files(TAXONOMY_LIKE)

    def features_md(self, annotations):
        out = tempfile.mkdtemp(prefix="sd-features-")
        try:
            render_docs(resolve(self.model, annotations), out, only=["features"])
            return read(os.path.join(out, "features.md"))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_legend_only_when_some_feature_is_unnamed(self):
        doc = self.features_md({})
        self.assertIn("（名前未確定）", doc)
        self.assertIn(LEGEND, doc)

    def test_no_legend_when_every_feature_is_named(self):
        ids = [f["id"] for f in resolve(self.model, {})["features"]]
        named = {"features": {fid: {"name": f"機能 {i}"} for i, fid in enumerate(ids)}}
        doc = self.features_md(named)
        self.assertNotIn("（名前未確定）", doc)
        self.assertNotIn(LEGEND, doc)
        self.assertIn("DB 欄の括弧は操作", doc)


if __name__ == "__main__":
    unittest.main()
