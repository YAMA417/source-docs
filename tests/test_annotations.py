"""注釈（annotations.json）の検査と、機能への反映のテスト。"""

import copy
import unittest

from helpers import ShopDocs

from sdocs.annotations import check, normalize_evidence
from sdocs.features import apply_annotations


class ShopAnnotationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = ShopDocs()
        cls.base = cls.docs.annotations()

    @classmethod
    def tearDownClass(cls):
        cls.docs.cleanup()

    def errors(self, mutate):
        data = copy.deepcopy(self.base)
        mutate(data)
        return check(self.docs.model, data, self.docs.out).errors

    def test_fixture_annotations_pass(self):
        result = check(self.docs.model, self.base, self.docs.out)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.secret_findings, [])

    def warnings(self, mutate):
        data = copy.deepcopy(self.base)
        mutate(data)
        return check(self.docs.model, data, self.docs.out).warnings

    def test_stale_entity_is_a_warning_not_an_error(self):
        """コードの変更で消えた ID への注釈は、再生成を止めずに警告する。"""
        mutate = lambda d: d["entities"].update({"endpoint:GET /ghost": {"summary": "x"}})  # noqa: E731
        self.assertEqual(self.errors(mutate), [])
        self.assertTrue(any("古い注釈" in w for w in self.warnings(mutate)))

    def test_stale_candidate_rename_is_a_warning(self):
        mutate = lambda d: d["features"].append({"id": "feature:api-gone", "name": "消えた機能"})  # noqa: E731
        self.assertEqual(self.errors(mutate), [])
        self.assertTrue(any("古い注釈" in w for w in self.warnings(mutate)))

    def test_wrong_shape_is_an_error(self):
        """描画で落ちる形（fields に文字列）は check で止める。"""
        def m(d):
            d["entities"]["endpoint:POST /api/orders"]["responses"][0]["fields"] = "id: number"
        self.assertTrue(any("fields はオブジェクトの配列" in e for e in self.errors(m)))

    def test_wrong_column_notes_shape(self):
        def m(d):
            d["entities"]["table:orders"]["columns"] = ["status"]
        self.assertTrue(any("columns" in e for e in self.errors(m)))

    def test_unverified_requires_searched(self):
        def m(d):
            d["entities"]["table:orders"]["unverified"] = [{"item": "単位"}]
        self.assertTrue(any("searched" in e for e in self.errors(m)))

    def test_implemented_requires_evidence(self):
        def m(d):
            d["details"]["feature:order-confirm"]["transaction"] = {"status": "implemented"}
        self.assertTrue(any("根拠" in e for e in self.errors(m)))

    def test_unverified_status_requires_searched(self):
        def m(d):
            d["details"]["feature:order-confirm"]["idempotency"] = {"status": "unverified"}
        self.assertTrue(any("searched" in e for e in self.errors(m)))

    def test_evidence_file_must_exist(self):
        def m(d):
            d["entities"]["table:orders"]["evidence"] = ["src/db/nope.ts"]
        self.assertTrue(any("存在しない" in e for e in self.errors(m)))

    def test_evidence_must_not_be_secret_file(self):
        def m(d):
            d["entities"]["table:orders"]["evidence"] = [".env"]
        self.assertTrue(any("開いてはいけない" in e for e in self.errors(m)))

    def test_evidence_must_stay_inside_repo(self):
        def m(d):
            d["entities"]["table:orders"]["evidence"] = ["../../README.md"]
        self.assertTrue(self.errors(m))

    def test_flow_step_limit(self):
        def m(d):
            d["flows"]["feature:order-confirm"]["steps"] = ["a"] * 6
        self.assertTrue(any("5 行まで" in e for e in self.errors(m)))

    def test_state_transition_must_use_declared_states(self):
        def m(d):
            d["details"]["feature:order-confirm"]["stateTransition"]["transitions"].append(
                {"from": "paid", "to": "refunded"})
        self.assertTrue(any("refunded" in e for e in self.errors(m)))

    def test_sequence_participants(self):
        def m(d):
            d["details"]["feature:order-confirm"]["sequence"]["steps"].append(
                {"from": "user", "to": "ghost", "message": "x"})
        self.assertTrue(any("ghost" in e for e in self.errors(m)))

    def test_data_impact_operation(self):
        def m(d):
            d["details"]["feature:order-confirm"]["dataImpact"][0]["operation"] = "UPSERT"
        self.assertTrue(any("operation" in e for e in self.errors(m)))

    def test_exclusion_requires_reason(self):
        def m(d):
            d["exclusions"] = [{"id": "endpoint:GET /api/health"}]
        self.assertTrue(any("理由" in e for e in self.errors(m)))

    def test_duplicate_feature_name(self):
        def m(d):
            d["features"][1]["name"] = d["features"][0]["name"]
        self.assertTrue(any("重複" in e for e in self.errors(m)))

    def test_detail_for_missing_feature(self):
        def m(d):
            d["details"]["feature:nope"] = {}
        self.assertEqual(self.errors(m), [])
        self.assertTrue(any("機能が存在しない" in w for w in self.warnings(m)))

    def test_detail_list_fields_must_be_objects(self):
        def m(d):
            d["details"]["feature:order-confirm"]["branches"] = ["productId が無いと 400"]
        self.assertTrue(any("branches" in e for e in self.errors(m)))

    def test_secret_value_is_reported(self):
        data = copy.deepcopy(self.base)
        data["system"]["summary"] = "接続は postgres://admin:hunter2@db.example.com/app"
        result = check(self.docs.model, data, self.docs.out)
        self.assertTrue(result.secret_findings)


class ApplyAnnotationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = ShopDocs()

    @classmethod
    def tearDownClass(cls):
        cls.docs.cleanup()

    def test_new_feature_replaces_covered_candidates(self):
        features = {f["id"]: f for f in apply_annotations(self.docs.model, self.docs.annotations())}
        self.assertIn("feature:order-confirm", features)
        self.assertNotIn("feature:screen-checkout", features)
        f = features["feature:order-confirm"]
        self.assertEqual((f["name"], f["status"], f["nameSource"]), ("注文確定", "confirmed", "annotation"))
        self.assertEqual(f["tables"], [{"id": "table:orders", "operations": ["CREATE"]}])

    def test_rename_keeps_relations(self):
        features = {f["id"]: f for f in apply_annotations(self.docs.model, self.docs.annotations())}
        f = features["feature:webhook-post-api-webhooks-stripe"]
        self.assertEqual(f["name"], "決済結果の受信")
        self.assertEqual(f["events"], ["event:order.paid"])

    def test_order_is_stable(self):
        a = [f["id"] for f in apply_annotations(self.docs.model, self.docs.annotations())]
        b = [f["id"] for f in apply_annotations(self.docs.model, self.docs.annotations())]
        self.assertEqual(a, b)

    def test_without_annotations_candidates_remain(self):
        self.assertEqual(apply_annotations(self.docs.model, {}), self.docs.model["features"])


class EvidenceFormTest(unittest.TestCase):
    def test_string_and_object(self):
        self.assertEqual(normalize_evidence("src/a.ts:12"), {"file": "src/a.ts", "line": 12})
        self.assertEqual(normalize_evidence("src/a.ts"), {"file": "src/a.ts"})
        self.assertEqual(normalize_evidence({"file": "x"}), {"file": "x"})


if __name__ == "__main__":
    unittest.main()
