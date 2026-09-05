"""スタック判定のテスト。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "scripts"))
import inventory  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class ReadDependenciesTest(unittest.TestCase):
    def test_package_json(self):
        path = os.path.join(FIXTURES, "next-prisma", "package.json")
        deps = inventory.read_dependencies(path)
        self.assertIn("next", deps)
        self.assertIn("@prisma/client", deps)
        self.assertIn("typescript", deps)  # devDependencies も含める

    def test_missing_file_returns_empty(self):
        self.assertEqual(inventory.read_dependencies("/no/such/file.json"), set())


class DetectStackTest(unittest.TestCase):
    def test_next_prisma_postgres(self):
        stack = inventory.detect_stack({"next", "react", "@prisma/client", "pg"})
        self.assertEqual(stack["frontend"], ["Next.js", "React"])
        self.assertEqual(stack["orm"], ["Prisma"])
        self.assertEqual(stack["db"], ["PostgreSQL"])
        self.assertEqual(stack["backend"], [])

    def test_strapi(self):
        stack = inventory.detect_stack({"@strapi/strapi", "pg"})
        self.assertEqual(stack["backend"], ["Strapi"])
        self.assertEqual(stack["db"], ["PostgreSQL"])

    def test_expo(self):
        stack = inventory.detect_stack({"expo", "react-native"})
        self.assertEqual(stack["frontend"], ["Expo", "React Native"])

    def test_unknown_deps_produce_empty_lists(self):
        stack = inventory.detect_stack({"lodash"})
        self.assertEqual(stack, {"frontend": [], "backend": [], "orm": [], "db": []})


class ReadDependenciesOtherManifestsTest(unittest.TestCase):
    def test_pyproject(self):
        path = os.path.join(FIXTURES, "py-fastapi", "pyproject.toml")
        deps = inventory.read_dependencies(path)
        self.assertIn("fastapi", deps)
        self.assertIn("sqlalchemy", deps)

    def test_pubspec(self):
        path = os.path.join(FIXTURES, "flutter-app", "pubspec.yaml")
        deps = inventory.read_dependencies(path)
        self.assertIn("flutter", deps)
        self.assertIn("drift", deps)

    def test_go_mod(self):
        path = os.path.join(FIXTURES, "go-gin", "go.mod")
        deps = inventory.read_dependencies(path)
        self.assertIn("gin-gonic/gin", deps)
