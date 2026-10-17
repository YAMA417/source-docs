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
        p = run("check", "--out", self.out)
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
            p = run("check", "--out", out)
            self.assertEqual(p.returncode, 1)
            self.assertIn("searched", p.stdout)
            p = run("render", "--out", out)
            self.assertEqual(p.returncode, 1)
        finally:
            shutil.rmtree(out)


if __name__ == "__main__":
    unittest.main()
