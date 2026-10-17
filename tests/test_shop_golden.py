"""入口が複数ある shop fixture の検出結果を固定する（回帰テスト）。

shop は 画面 2・HTTP API 3（＋Webhook 1）・テーブル 2・Stripe・Cron 1・キュー処理 1・
イベント 1・ワーカー 1 を持つ。ここで期待値を変えるときは、検出の仕様を変えたときだけ。
"""

import json
import os
import unittest

from helpers import by_id, fixture, ids, model_of

EXPECTED_FEATURES = {
    # (機能 ID, 種別): (入口, テーブル操作, 外部連携, ジョブ, イベント)
    ("feature:screen-checkout", "screen"): (
        ["entrypoint:http:POST /api/orders", "entrypoint:screen:/checkout"],
        {"table:orders": ["CREATE"]}, ["integration:stripe"], ["job:queue-consumer:receipts"], []),
    ("feature:screen-products", "screen"): (
        ["entrypoint:http:GET /api/products", "entrypoint:screen:/products"],
        {"table:products": ["READ"]}, [], [], []),
    ("feature:api-get-api-health", "api"): (
        ["entrypoint:http:GET /api/health"], {}, [], [], []),
    ("feature:webhook-post-api-webhooks-stripe", "webhook"): (
        ["entrypoint:webhook:POST /api/webhooks/stripe"],
        {"table:orders": ["UPDATE"]}, ["integration:stripe"], [], ["event:order.paid"]),
    ("feature:cron-cleanup-cron", "cron"): (
        ["entrypoint:cron:cleanup-cron"], {"table:orders": ["DELETE"]}, [], ["job:cron:cleanup-cron"], []),
    ("feature:queue-consumer-receipts", "queue-consumer"): (
        ["entrypoint:queue-consumer:receipts"], {"table:orders": ["READ"]}, [],
        ["job:queue-consumer:receipts"], []),
    ("feature:worker-stock-sync", "worker"): (
        ["entrypoint:worker:stock-sync"], {"table:products": ["UPDATE"]},
        ["integration:host:api.example-warehouse.test"], ["job:worker:stock-sync"], []),
    ("feature:event-handler-order-paid-src-events-handlers-ts", "event-handler"): (
        ["entrypoint:event-handler:order.paid <- src/events/handlers.ts"],
        {"table:products": ["UPDATE"]}, [], [], ["event:order.paid"]),
}


class ShopEntitiesTest(unittest.TestCase):
    def setUp(self):
        self.m = model_of("shop")

    def test_screens(self):
        self.assertEqual(ids(self.m, "screens"), ["screen:/checkout", "screen:/products"])

    def test_endpoints(self):
        self.assertEqual(ids(self.m, "endpoints"), [
            "endpoint:GET /api/health", "endpoint:GET /api/products",
            "endpoint:POST /api/orders", "endpoint:POST /api/webhooks/stripe"])

    def test_webhook_is_marked(self):
        e = by_id(self.m)["endpoint:POST /api/webhooks/stripe"]
        self.assertTrue(e.get("webhook"))
        self.assertIn("entrypoint:webhook:POST /api/webhooks/stripe", ids(self.m, "entrypoints"))

    def test_tables_and_enum(self):
        self.assertEqual(ids(self.m, "tables"), ["table:orders", "table:products"])
        self.assertEqual(ids(self.m, "enums"), ["enum:order_status"])

    def test_jobs(self):
        self.assertEqual(ids(self.m, "jobs"), [
            "job:cron:cleanup-cron", "job:queue-consumer:receipts", "job:worker:stock-sync"])
        cron = by_id(self.m)["job:cron:cleanup-cron"]
        self.assertEqual(cron["schedule"], "0 3 * * *")

    def test_event(self):
        ev = by_id(self.m)["event:order.paid"]
        self.assertEqual([p["file"] for p in ev["publishers"]], ["src/app/api/webhooks/stripe/route.ts"])
        self.assertEqual([s["file"] for s in ev["subscribers"]], ["src/events/handlers.ts"])

    def test_integrations(self):
        stripe = by_id(self.m)["integration:stripe"]
        self.assertEqual(sorted(stripe["directions"]), ["inbound", "outbound"])
        self.assertEqual(stripe["envVars"], ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"])
        host = by_id(self.m)["integration:host:api.example-warehouse.test"]
        self.assertEqual(host["directions"], ["outbound"])

    def test_screen_relations(self):
        idx = by_id(self.m)
        products = idx["screen:/products"]
        self.assertEqual([r["id"] for r in products["calls"]], ["endpoint:GET /api/products"])
        self.assertEqual([r["id"] for r in products["links"]], ["screen:/checkout"])
        checkout = idx["screen:/checkout"]
        self.assertEqual([r["id"] for r in checkout["links"]], ["screen:/products"])
        self.assertEqual(checkout["links"][0]["tags"], ["push"])

    def test_endpoint_relations_follow_imports(self):
        """API → サービス → DB / Stripe / キューを import をたどって結び付ける。"""
        orders = by_id(self.m)["endpoint:POST /api/orders"]
        self.assertEqual([(r["id"], r["operations"]) for r in orders["tables"]], [("table:orders", ["CREATE"])])
        self.assertEqual([r["id"] for r in orders["integrations"]], ["integration:stripe"])
        self.assertEqual([r["id"] for r in orders["enqueues"]], ["job:queue-consumer:receipts"])
        self.assertEqual(orders["tables"][0]["evidence"][0]["file"], "src/services/orders.ts")


class ShopFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.m = model_of("shop")
        self.features = {(f["id"], f["type"]): f for f in self.m["features"]}

    def test_feature_set_is_fixed(self):
        self.assertEqual(sorted(self.features), sorted(EXPECTED_FEATURES))

    def test_each_feature(self):
        for key, (eps, tables, integ, jobs, events) in EXPECTED_FEATURES.items():
            f = self.features[key]
            with self.subTest(feature=key[0]):
                self.assertEqual(f["entrypoints"], eps)
                self.assertEqual({t["id"]: t["operations"] for t in f["tables"]}, tables)
                self.assertEqual(f["integrations"], integ)
                self.assertEqual(f["jobs"], jobs)
                self.assertEqual(f["events"], events)
                self.assertEqual(f["status"], "candidate")
                self.assertEqual(f["nameSource"], "derived")

    def test_features_without_screen_are_first_class(self):
        no_screen = [f for f in self.m["features"] if not f["screens"]]
        self.assertEqual(len(no_screen), 6)
        self.assertEqual({f["type"] for f in no_screen},
                         {"api", "webhook", "cron", "queue-consumer", "worker", "event-handler"})

    def test_implementation_files_are_real(self):
        f = self.features[("feature:screen-checkout", "screen")]
        self.assertIn("src/services/orders.ts", f["implementationFiles"])
        for path in f["implementationFiles"]:
            self.assertTrue(os.path.isfile(os.path.join(fixture("shop"), *path.split("/"))), path)


class ShopEvidenceTest(unittest.TestCase):
    def test_every_entity_has_evidence_in_real_files(self):
        m = model_of("shop")
        for coll in ("screens", "endpoints", "tables", "integrations", "jobs", "events", "entrypoints"):
            for item in m[coll]:
                with self.subTest(entity=item["id"]):
                    self.assertTrue(item["evidence"], "evidence が無い")
                    for ev in item["evidence"]:
                        self.assertTrue(os.path.isfile(os.path.join(fixture("shop"), *ev["file"].split("/"))))

    def test_env_file_is_never_read(self):
        """`.env` の値はモデルのどこにも出ない（`.env.example` の変数名だけを使う）。"""
        text = json.dumps(model_of("shop"), ensure_ascii=False)
        self.assertNotIn("FIXTURE_SECRET_VALUE", text)
        self.assertNotIn('".env"', text)


if __name__ == "__main__":
    unittest.main()
