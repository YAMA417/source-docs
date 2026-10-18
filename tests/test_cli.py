"""コマンドラインのテスト。Windows（cp932）でも落ちず、UTF-8 で出力することを含む。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import CLI, fixture


def run(*args):
    """出力先の文字コードを cp932 にして走らせる。Windows のパイプと同じ状態を、どの OS でも作る。"""
    env = {**os.environ, "PYTHONIOENCODING": "cp932"}
    env.pop("PYTHONUTF8", None)
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, encoding="utf-8", env=env)


class CliFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="sd-cli-")
        cls.analyze = run("analyze", "--repo", fixture("shop"), "--out", cls.out)
        shutil.copy(fixture("shop-annotations.json"), os.path.join(cls.out, ".source-docs", "annotations.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_analyze(self):
        self.assertEqual(self.analyze.returncode, 0, self.analyze.stderr)
        self.assertIn("FEATURES 8（画面を持たないもの 6）", self.analyze.stdout)
        self.assertIn("判定できず", self.analyze.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.out, ".source-docs", "project-model.json")))

    def test_check_render_coverage_verify(self):
        p = run("lint", "--out", self.out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = run("render", "--out", self.out, "--format", "both")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("SECRETS=0 RESULT=OK", p.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "html", "detail", "order-confirm.html")))
        self.assertTrue(os.path.isfile(os.path.join(self.out, ".source-docs", "coverage.json")))
        p = run("coverage", "--out", self.out, "--fail-under", "100")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("API: detected 4", p.stdout)
        p = run("verify", "--out", self.out)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("RESULT=OK", p.stdout)
        # 生成直後は古くない。--strict と閾値を付けても通る
        p = run("check", "--out", self.out, "--strict", "--min-api-coverage", "100", "--min-screen-coverage", "100")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("RESULT=OK", p.stdout)
        self.assertNotIn("stale", p.stdout)

    def test_features_and_show(self):
        p = run("features", "--out", self.out)
        self.assertIn("注文確定", p.stdout)
        p = run("show", "endpoint:POST /api/orders", "--out", self.out)
        data = json.loads(p.stdout)
        self.assertEqual(data["entity"]["id"], "endpoint:POST /api/orders")
        self.assertIn("feature:order-confirm", data["features"])

    def test_scaffold(self):
        p = run("scaffold", "--feature", "receipts", "--out", self.out)
        self.assertEqual(p.returncode, 0, p.stderr)
        spec = json.loads(p.stdout)["feature:queue-consumer-receipts"]
        self.assertEqual(spec["dataImpact"], [{"table": "table:orders", "operation": "READ",
                                               "columns": [], "meaning": ""}])
        self.assertEqual(spec["transaction"]["status"], "unverified")

    def test_render_feature_writes_only_that_document(self):
        out = tempfile.mkdtemp(prefix="sd-cli-")
        try:
            run("analyze", "--repo", fixture("shop"), "--out", out)
            p = run("render", "--out", out, "--feature", "receipts", "--kind", "detail")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            mds = sorted(os.path.relpath(os.path.join(r, f), out).replace(os.sep, "/")
                         for r, _, fs in os.walk(out) for f in fs if f.endswith(".md"))
            self.assertEqual(mds, ["detail/queue-consumer-receipts.md"])
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_unknown_feature(self):
        p = run("scaffold", "--feature", "存在しない", "--out", self.out)
        self.assertEqual(p.returncode, 2)
        self.assertIn("見つからない", p.stderr)


class CliErrorTest(unittest.TestCase):
    def test_missing_repo(self):
        p = run("analyze", "--repo", "/no/such/dir", "--out", tempfile.gettempdir())
        self.assertEqual(p.returncode, 2)

    def test_render_without_model(self):
        p = run("render", "--out", os.path.join(tempfile.gettempdir(), "sd-none"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("analyze", p.stderr)

    def test_secrets_command(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "x.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("接続先 postgres://admin:hunter2pass@db.internal:5432/app — 本番\n")
            p = run("secrets", "--doc", path)
            self.assertEqual(p.returncode, 1)
            self.assertIn("DB 接続文字列", p.stdout)
            self.assertNotIn("hunter2pass", p.stdout)
            p = run("secrets", "--doc", path, "--reveal")
            self.assertIn("— 本番", p.stdout)
        finally:
            shutil.rmtree(d)

    def test_check_reports_errors(self):
        out = tempfile.mkdtemp()
        try:
            run("analyze", "--repo", fixture("shop"), "--out", out)
            with open(os.path.join(out, ".source-docs", "annotations.json"), "w", encoding="utf-8") as f:
                json.dump({"schema": "source-docs/annotations", "version": 1,
                           "entities": {"table:orders": {"unverified": [{"item": "単位"}]}}}, f)
            p = run("lint", "--out", out)
            self.assertEqual(p.returncode, 1)
            self.assertIn("searched", p.stdout)
            p = run("check", "--out", out)
            self.assertEqual(p.returncode, 1)
            self.assertIn("ERROR   annotation.invalid", p.stdout)
            p = run("render", "--out", out)
            self.assertEqual(p.returncode, 1)
        finally:
            shutil.rmtree(out)


class CheckTest(unittest.TestCase):
    """`check` が、資料を作ったあとのコードの変化を見つけ、--strict で CI を止める。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sd-repo-")
        # アプリの ID はディレクトリ名から決まる。注釈の app:shop と合わせる
        self.repo = os.path.join(self.tmp, "shop")
        shutil.copytree(fixture("shop"), self.repo)
        self.out = os.path.join(self.repo, "docs", "system")
        run("analyze", "--repo", self.repo, "--out", self.out)
        shutil.copy(fixture("shop-annotations.json"), os.path.join(self.out, ".source-docs", "annotations.json"))
        p = run("render", "--out", self.out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_api_after_render_is_stale(self):
        route = os.path.join(self.repo, "src", "app", "api", "refunds", "route.ts")
        os.makedirs(os.path.dirname(route))
        with open(route, "w", encoding="utf-8") as f:
            f.write("export async function POST() { return Response.json({}) }\n")
        p = run("check", "--out", self.out)
        self.assertEqual(p.returncode, 0, p.stdout)  # 古いだけなら WARNING
        self.assertIn("WARNING stale: 新しいAPI 1 件: endpoint:POST /api/refunds", p.stdout)
        p = run("check", "--out", self.out, "--strict")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("ERROR   stale", p.stdout)

    def test_removed_screen_and_coverage_threshold(self):
        os.remove(os.path.join(self.repo, "src", "app", "products", "page.tsx"))
        p = run("check", "--out", self.out, "--json")
        data = json.loads(p.stdout)
        msgs = [d["message"] for d in data["diagnostics"] if d["code"] == "stale"]
        self.assertTrue(any(m.startswith("無くなった画面 1 件: screen:/products") for m in msgs), msgs)

    def test_secret_in_rendered_html_is_an_error(self):
        os.makedirs(os.path.join(self.out, "html"), exist_ok=True)
        with open(os.path.join(self.out, "html", "leak.html"), "w", encoding="utf-8") as f:
            f.write("<p>AKIAIOSFODNN7EXAMPLQ</p>")
        p = run("check", "--out", self.out, "--no-reanalyze")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("secret.docs: html/leak.html", p.stdout)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLQ", p.stdout)


if __name__ == "__main__":
    unittest.main()
