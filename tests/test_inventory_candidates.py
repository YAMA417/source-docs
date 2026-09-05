"""候補列挙と CLI のテスト。"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "scripts"))
import inventory  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NEXT = os.path.join(FIXTURES, "next-prisma")
STRAPI = os.path.join(FIXTURES, "strapi")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "skill", "scripts", "inventory.py")


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
