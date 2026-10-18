"""Project Model の形（スキーマ・版・ID・参照）のテスト。"""

import copy
import json
import os
import shutil
import tempfile
import unittest

from helpers import model_of

from sdocs import MODEL_SCHEMA, MODEL_VERSION
from sdocs.core.model import (ENTRYPOINT_TYPES, load_model, model_path, normalize_path, validate_model,
                         write_json)


class SchemaTest(unittest.TestCase):
    def test_schema_and_version(self):
        m = model_of("shop")
        self.assertEqual(m["schema"], MODEL_SCHEMA)
        self.assertEqual(m["version"], MODEL_VERSION)
        self.assertEqual(validate_model(m), [])

    def test_required_collections(self):
        m = model_of("shop")
        for key in ("repositories", "applications", "entrypoints", "features", "screens", "endpoints",
                    "tables", "integrations", "jobs", "events", "unsupported"):
            self.assertIsInstance(m[key], list, key)

    def test_ids_are_unique_and_prefixed(self):
        m = model_of("nextjs-strapi")
        seen = set()
        for coll, prefix in (("screens", "screen:"), ("endpoints", "endpoint:"), ("tables", "table:"),
                             ("entrypoints", "entrypoint:"), ("features", "feature:")):
            for item in m[coll]:
                self.assertTrue(item["id"].startswith(prefix), item["id"])
                self.assertNotIn(item["id"], seen)
                seen.add(item["id"])

    def test_entrypoint_types_are_known_and_targets_exist(self):
        m = model_of("shop")
        all_ids = {e["id"] for c in ("screens", "endpoints", "tables", "jobs", "events", "commands")
                   for e in m[c]}
        for ep in m["entrypoints"]:
            self.assertIn(ep["type"], ENTRYPOINT_TYPES)
            self.assertIn(ep["target"], all_ids)

    def test_json_roundtrip(self):
        m = model_of("shop")
        self.assertEqual(json.loads(json.dumps(m, ensure_ascii=False)), m)

    def test_deterministic(self):
        """同じ入力からは同じモデル。差分で鮮度を比べるため。"""
        from sdocs.core.analyzer import analyze
        from helpers import fixture
        a = analyze([fixture("shop")], tempfile.gettempdir())
        b = analyze([fixture("shop")], tempfile.gettempdir())
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


class ValidateTest(unittest.TestCase):
    def test_broken_reference_is_rejected(self):
        m = copy.deepcopy(model_of("shop"))
        m["features"][0]["endpoints"].append("endpoint:GET /nope")
        self.assertTrue(any("参照先が存在しない" in e for e in validate_model(m)))

    def test_wrong_version_is_rejected(self):
        m = copy.deepcopy(model_of("shop"))
        m["version"] = 999
        self.assertTrue(validate_model(m))

    def test_unknown_status_is_rejected(self):
        m = copy.deepcopy(model_of("shop"))
        m["tables"][0]["status"] = "guessed"
        self.assertTrue(any("status" in e for e in validate_model(m)))

    def test_load_refuses_broken_model(self):
        out = tempfile.mkdtemp()
        try:
            m = copy.deepcopy(model_of("shop"))
            m["schema"] = "other"
            write_json(model_path(out), m)
            with self.assertRaises(SystemExit):
                load_model(out)
        finally:
            shutil.rmtree(out)

    def test_load_without_model(self):
        with self.assertRaises(SystemExit):
            load_model(os.path.join(tempfile.gettempdir(), "sd-no-such-dir"))


class NormalizePathTest(unittest.TestCase):
    def test_param_styles_are_equal(self):
        for p in ("/orders/:id", "/orders/{id}", "/orders/[id]", "/orders/<id>", "/orders/${id}"):
            self.assertEqual(normalize_path(p), "/orders/{}", p)

    def test_query_and_trailing_slash(self):
        self.assertEqual(normalize_path("/a/b/?x=1"), "/a/b")


if __name__ == "__main__":
    unittest.main()
