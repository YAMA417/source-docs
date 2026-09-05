"""候補列挙と CLI のテスト。"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "source-docs", "scripts"))
import inventory  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NEXT = os.path.join(FIXTURES, "next-prisma")
STRAPI = os.path.join(FIXTURES, "strapi")
EXPO = os.path.join(FIXTURES, "expo-app")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "skills", "source-docs", "scripts", "inventory.py")


class FindManifestsTest(unittest.TestCase):
    def test_finds_package_json(self):
        self.assertEqual(inventory.find_manifests(NEXT), ["package.json"])


class FindCandidatesTest(unittest.TestCase):
    def test_next_prisma(self):
        c = inventory.find_candidates(NEXT)
        self.assertIn("prisma/schema.prisma", c["schema"])
        self.assertIn("src/routes/orders.ts", c["route"])
        self.assertIn("app/books/page.tsx", c["screen"])
        self.assertIn("src/gateways/payment.ts", c["integration"])

    def test_strapi_content_types(self):
        c = inventory.find_candidates(STRAPI)
        self.assertIn("src/api/book/content-types/book/schema.json", c["schema"])

    def test_app_root_page_is_a_screen(self):
        """app/ 直下の page.tsx（トップページ）も画面として拾う。"""
        c = inventory.find_candidates(NEXT)
        self.assertIn("app/page.tsx", c["screen"])

    def test_drizzle_schema_under_src(self):
        """src/schema.ts 形式の Drizzle スキーマも拾う。"""
        c = inventory.find_candidates(NEXT)
        self.assertIn("src/schema.ts", c["schema"])

    def test_expo_router_screens(self):
        """Expo Router は app/ 配下の .tsx がそのまま画面。page.tsx 規約ではない。"""
        stack = {"frontend": ["Expo", "React Native"], "backend": [], "orm": [], "db": []}
        c = inventory.find_candidates(EXPO, stack)
        self.assertIn("src/app/index.tsx", c["screen"])
        self.assertIn("src/app/(tabs)/home.tsx", c["screen"])

    def test_expo_layout_is_not_a_screen(self):
        stack = {"frontend": ["Expo", "React Native"], "backend": [], "orm": [], "db": []}
        c = inventory.find_candidates(EXPO, stack)
        self.assertNotIn("src/app/_layout.tsx", c["screen"])

    def test_next_layout_is_not_a_screen(self):
        """Next.js では page.tsx 以外を画面にしない。"""
        c = inventory.find_candidates(NEXT)
        self.assertNotIn("app/layout.tsx", c["screen"])

    def test_edge_function_is_a_route(self):
        """Supabase Edge Function も利用者から見える入口として拾う。"""
        c = inventory.find_candidates(NEXT)
        self.assertIn("supabase/functions/notify/index.ts", c["route"])

    def test_skips_node_modules(self):
        c = inventory.find_candidates(NEXT)
        self.assertFalse(any("node_modules" in p for p in c["schema"]))


class SurveyTest(unittest.TestCase):
    def test_counts_match_candidates(self):
        result = inventory.survey([NEXT])
        repo = result["repos"][0]
        self.assertEqual(repo["counts"]["schema"], len(repo["candidates"]["schema"]))
        self.assertEqual(repo["stack"]["orm"], ["Prisma"])


class CliTest(unittest.TestCase):
    def test_json_output(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--repo", NEXT, "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["repos"]), 1)

    def test_human_output_mentions_counts(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--repo", NEXT],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("schema", proc.stdout)

    def test_missing_repo_exits_2(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--repo", "/no/such/dir"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)


class SecretFileSkipTest(unittest.TestCase):
    def test_candidates_exclude_secret_files(self):
        """鍵や .env を候補に出さない。"""
        import tempfile
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "src", "gateways"))
            for name, body in [
                (os.path.join("src", "gateways", "payment.ts"), "x"),
                (os.path.join("src", "gateways", ".env.production"), "K=v"),
                (os.path.join("src", "gateways", "signing.pem"), "key"),
            ]:
                with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
                    f.write(body)

            c = inventory.find_candidates(repo)
            self.assertIn("src/gateways/payment.ts", c["integration"])
            self.assertNotIn("src/gateways/.env.production", c["integration"])
            self.assertNotIn("src/gateways/signing.pem", c["integration"])

    def test_manifests_exclude_secret_dirs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "credentials"))
            with open(os.path.join(repo, "package.json"), "w", encoding="utf-8") as f:
                f.write('{"dependencies":{"next":"1"}}')
            with open(os.path.join(repo, "credentials", "package.json"), "w", encoding="utf-8") as f:
                f.write('{"dependencies":{"evil":"1"}}')
            self.assertEqual(inventory.find_manifests(repo), ["package.json"])


class GlobDepthTest(unittest.TestCase):
    def test_star_does_not_cross_slash(self):
        """app/*/page.tsx は 1 階層だけに一致する。"""
        self.assertTrue(inventory.match_path("app/a/page.tsx", "app/*/page.tsx"))
        self.assertFalse(inventory.match_path("app/a/b/c/page.tsx", "app/*/page.tsx"))

    def test_leading_star_matches_zero_dirs(self):
        """*/routes/* はリポジトリ直下の routes/ にも一致する。"""
        self.assertTrue(inventory.match_path("routes/orders.ts", "*/routes/*"))
        self.assertTrue(inventory.match_path("src/routes/orders.ts", "*/routes/*"))

    def test_root_level_route_is_a_candidate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "routes"))
            with open(os.path.join(repo, "routes", "orders.ts"), "w", encoding="utf-8") as f:
                f.write("x")
            c = inventory.find_candidates(repo)
            self.assertIn("routes/orders.ts", c["route"])
