"""注釈（annotations.json）の検査と、機能への反映のテスト。"""

import copy
import unittest

from helpers import ShopDocs

from sdocs.validators.annotations import check, normalize_evidence
from sdocs.core.features import apply_annotations
from sdocs.core.resolve import resolve


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


class NewAnnotationFormsTest(unittest.TestCase):
    """辞書形式の features・入口の省略形・Capability・異議（disputes）・未確認の種類。"""

    @classmethod
    def setUpClass(cls):
        cls.docs = ShopDocs()
        cls.base = cls.docs.annotations()

    @classmethod
    def tearDownClass(cls):
        cls.docs.cleanup()

    def test_dict_features_with_short_entrypoint_refs(self):
        ann = {"features": {"feature:checkout-flow": {
            "name": "購入", "entrypoints": ["screen:/checkout", "http:POST /api/orders"]}}}
        result = check(self.docs.model, dict(self.base, **ann), self.docs.out)
        self.assertEqual(result.errors, [])
        f = {f["id"]: f for f in apply_annotations(self.docs.model, ann)}["feature:checkout-flow"]
        self.assertEqual(f["name"], "購入")
        self.assertIn("entrypoint:screen:/checkout", f["entrypoints"])
        self.assertIn("entrypoint:http:POST /api/orders", f["entrypoints"])

    def test_capability_groups_features(self):
        view = resolve(self.docs.model, dict(self.base, capabilities={
            "capability:sales": {"name": "販売", "features": ["feature:order-confirm", "feature:ghost"]}}))
        cap = view["capabilities"][0]
        self.assertEqual(cap["features"], ["feature:order-confirm"])
        self.assertEqual(cap["unknownFeatures"], ["feature:ghost"])

    def test_dispute_moves_fact_out_of_body(self):
        """異議を出した関係は本文から外れ、未確定（未解決）として出る。解析器の誤りを直しただけなので FAILED にしない。"""
        dispute = {"entity": "endpoint:POST /api/orders", "field": "tables", "value": "table:orders",
                   "reason": "テスト用の異議", "evidence": ["src/app/api/orders/route.ts"]}
        ann = dict(self.base, disputes=[dispute])
        self.assertEqual(check(self.docs.model, ann, self.docs.out).errors, [])
        view = resolve(self.docs.model, ann)
        ep = {e["id"]: e for e in view["endpoints"]}["endpoint:POST /api/orders"]
        self.assertNotIn("table:orders", [r["id"] for r in ep["tables"]])
        self.assertIn({"field": "tables", "value": "table:orders", "status": "unresolved"},
                      [{k: p[k] for k in ("field", "value", "status")} for p in ep["pending"]])
        self.assertIn("dispute.tables", [d["code"] for d in view["diagnostics"] if d["severity"] == "WARNING"])
        self.assertEqual(view["quality"]["conflict"], 0)

    def test_dispute_requires_reason_and_evidence(self):
        ann = dict(self.base, disputes=[{"entity": "endpoint:POST /api/orders", "field": "tables",
                                         "value": "table:orders"}])
        errors = check(self.docs.model, ann, self.docs.out).errors
        self.assertTrue(any("reason" in e for e in errors), errors)
        self.assertTrue(any("根拠" in e for e in errors), errors)

    def test_unverified_kind(self):
        data = copy.deepcopy(self.base)
        data["entities"]["table:orders"]["unverified"] = [
            {"item": "保持期間", "searched": "migration と README", "kind": "runtime-dependent"}]
        self.assertEqual(check(self.docs.model, data, self.docs.out).errors, [])
        data["entities"]["table:orders"]["unverified"][0]["kind"] = "guess"
        self.assertTrue(check(self.docs.model, data, self.docs.out).errors)
