"""検証済みスタックの回帰テスト。README の「対応スタック」と対応させる。"""

import unittest

from helpers import by_id, ids, model_of


def feature_map(m):
    return {f["name"]: f for f in m["features"]}


class NextJsStrapiTest(unittest.TestCase):
    """Next.js（フロント）+ Strapi（バック）のモノレポ。"""

    def setUp(self):
        self.m = model_of("nextjs-strapi")

    def test_two_applications(self):
        apps = {a["path"]: a for a in self.m["applications"]}
        self.assertEqual(apps["frontend"]["stack"]["frontend"], ["Next.js", "React"])
        self.assertEqual(apps["backend"]["stack"]["backend"], ["Strapi"])
        self.assertEqual(apps["backend"]["kind"], "backend")

    def test_screens_with_dynamic_route(self):
        self.assertEqual(ids(self.m, "screens"), ["screen:/", "screen:/books", "screen:/books/:id"])

    def test_core_router_respects_only(self):
        keys = {e["key"] for e in self.m["endpoints"] if e["app"].endswith("backend")}
        self.assertEqual(keys, {"GET /api/books", "GET /api/books/:id", "POST /api/books",
                                "POST /api/books/:id/archive"})

    def test_same_path_in_two_apps_gets_distinct_ids(self):
        self.assertIn("endpoint:POST /api/books@frontend", ids(self.m, "endpoints"))
        self.assertIn("endpoint:POST /api/books@backend", ids(self.m, "endpoints"))

    def test_proxy_route_calls_backend(self):
        front = by_id(self.m)["endpoint:POST /api/books@frontend"]
        self.assertEqual([r["id"] for r in front["calls"]], ["endpoint:POST /api/books@backend"])

    def test_screen_calls_strapi_with_base_url(self):
        books = by_id(self.m)["screen:/books"]
        self.assertEqual([r["id"] for r in books["calls"]], ["endpoint:GET /api/books"])
        self.assertEqual([r["id"] for r in books["links"]], ["screen:/books/:id"])

    def test_convention_columns_are_labeled(self):
        books = by_id(self.m)["table:books"]
        cols = {c["name"]: c for c in books["columns"]}
        self.assertIn("note", cols["id"])
        self.assertIn("published_at", cols)  # draftAndPublish: true
        self.assertNotIn("note", cols["title"])
        # Strapi の relation は ORM の関係。規約で作るカラムは inferred
        self.assertEqual(books["ormRelations"][0]["references"]["table"], "authors")
        self.assertEqual(books["ormRelations"][0]["relation"], "manyToOne")
        self.assertEqual(cols["created_at"]["status"], "inferred")
        self.assertEqual(cols["title"]["status"], "confirmed")

    def test_core_crud_does_not_absorb_custom_action(self):
        """コアの GET に、独自アクション（archive の UPDATE）を混ぜない。"""
        get = by_id(self.m)["endpoint:GET /api/books"]
        self.assertEqual([(r["id"], r["operations"]) for r in get["tables"]], [("table:books", ["READ"])])
        archive = by_id(self.m)["endpoint:POST /api/books/:id/archive"]
        self.assertEqual(archive["tables"][0]["operations"], ["UPDATE"])

    def test_lifecycle_and_cron(self):
        self.assertIn("entrypoint:lifecycle-hook:book.afterCreate", ids(self.m, "entrypoints"))
        cron = by_id(self.m)["job:cron:publishScheduled"]
        self.assertEqual(cron["schedule"], "0 * * * *")
        self.assertEqual(cron["tables"][0]["operations"], ["UPDATE"])

    def test_proxy_feature_is_not_duplicated(self):
        names = [f["name"] for f in self.m["features"]]
        self.assertIn("POST /api/books@frontend", names)
        self.assertNotIn("POST /api/books@backend", names)


class NextHonoDrizzleTest(unittest.TestCase):
    def setUp(self):
        self.m = model_of("hono-drizzle")

    def test_mounted_routes_get_prefix(self):
        self.assertEqual(ids(self.m, "endpoints"), [
            "endpoint:GET /api/books", "endpoint:GET /api/health", "endpoint:POST /api/books",
            "endpoint:POST /api/books/:id/loans"])

    def test_aliased_table_import(self):
        """`import { books as booksTable }` でもテーブルに結び付く。"""
        get = by_id(self.m)["endpoint:GET /api/books"]
        self.assertEqual([(r["id"], r["operations"]) for r in get["tables"]], [("table:books", ["READ"])])

    def test_route_segments_are_separated(self):
        loans = by_id(self.m)["endpoint:POST /api/books/:id/loans"]
        self.assertEqual({r["id"]: r["operations"] for r in loans["tables"]},
                         {"table:books": ["UPDATE"], "table:loans": ["CREATE"]})

    def test_drizzle_constraints(self):
        loans = by_id(self.m)["table:loans"]
        # migration が無いので `.references()` を物理 FK の根拠にする（inferred）
        self.assertEqual(loans["physicalForeignKeys"], [{"name": None, "columns": ["book_id"],
                                                         "references": {"table": "books", "columns": ["id"]},
                                                         "source": "drizzle", "status": "inferred"}])
        self.assertEqual(loans["physicalSource"], "drizzle")
        self.assertEqual(loans["checks"][0]["name"], "loans_days_positive")
        cols = {c["name"]: c for c in by_id(self.m)["table:books"]["columns"]}
        self.assertEqual(cols["available"]["default"], "true")
        self.assertFalse(cols["title"]["nullable"])

    def test_screen_feature_includes_api_and_table(self):
        f = feature_map(self.m)["/books"]
        self.assertIn("entrypoint:http:POST /api/books", f["entrypoints"])
        self.assertEqual(f["tables"], [{"id": "table:books", "operations": ["CREATE"]}])


class ExpoSupabaseTest(unittest.TestCase):
    def setUp(self):
        self.m = model_of("expo-app")

    def test_expo_screens(self):
        self.assertEqual(ids(self.m, "screens"), ["screen:/", "screen:/home", "screen:/settings"])

    def test_layout_is_not_a_screen(self):
        self.assertNotIn("screen:/_layout", ids(self.m, "screens"))

    def test_edge_function_and_shared_dir(self):
        self.assertEqual(ids(self.m, "endpoints"), ["endpoint:POST /functions/v1/notify"])

    def test_supabase_client_usage(self):
        idx = by_id(self.m)
        home = idx["screen:/home"]
        self.assertEqual([(r["id"], r["operations"]) for r in home["tables"]], [("table:profiles", ["READ"])])
        self.assertEqual([r["id"] for r in home["calls"]], ["endpoint:POST /functions/v1/notify"])
        self.assertEqual([r["id"] for r in home["links"]], ["screen:/settings"])
        settings = idx["screen:/settings"]
        self.assertEqual([r["id"] for r in settings["integrations"]], ["integration:supabase-auth"])

    def test_sql_migration(self):
        idx = by_id(self.m)
        logs = idx["table:audit_logs"]
        self.assertEqual(logs["indexes"][0]["name"], "audit_logs_profile_idx")
        self.assertEqual(logs["physicalForeignKeys"][0]["references"]["table"], "profiles")
        self.assertEqual(logs["checks"][0]["expression"], "action <> ''")
        profiles = idx["table:profiles"]
        self.assertEqual(profiles["triggers"][0]["name"], "profiles_audit")

    def test_db_trigger_and_pg_cron(self):
        self.assertIn("entrypoint:db-trigger:profiles_audit", ids(self.m, "entrypoints"))
        cron = by_id(self.m)["job:cron:purge-audit"]
        self.assertEqual(cron["tables"][0]["id"], "table:audit_logs")
        self.assertEqual(cron["tables"][0]["operations"], ["DELETE"])


class OtherStacksTest(unittest.TestCase):
    def test_next_prisma(self):
        m = model_of("next-prisma")
        self.assertEqual(ids(m, "tables"), ["table:Book", "table:Order"])
        self.assertEqual(ids(m, "screens"), ["screen:/", "screen:/books"])
        self.assertEqual(m["applications"][0]["stack"]["orm"], ["Prisma"])
        self.assertIn("integration:host:api.example-pay.test", ids(m, "integrations"))

    def test_go_gin_groups(self):
        m = model_of("go-gin")
        self.assertEqual(ids(m, "endpoints"), ["endpoint:GET /v1/books", "endpoint:POST /v1/books"])
        self.assertEqual(m["applications"][0]["stack"]["backend"], ["Gin"])

    def test_fastapi_sqlalchemy(self):
        m = model_of("py-fastapi")
        self.assertEqual(ids(m, "endpoints"), ["endpoint:GET /books", "endpoint:POST /books"])
        post = by_id(m)["endpoint:POST /books"]
        self.assertEqual([(r["id"], r["operations"], r["status"]) for r in post["tables"]],
                         [("table:books", ["CREATE"], "confirmed")])

    def test_flutter_stack_only(self):
        m = model_of("flutter-app")
        self.assertEqual(m["applications"][0]["stack"]["frontend"], ["Flutter"])
        self.assertEqual(m["applications"][0]["stack"]["orm"], ["Drift"])

    def test_strapi_without_routes_has_no_invented_endpoints(self):
        """routes が無ければ API を作らない。規約だけで存在を断定しない。"""
        m = model_of("strapi")
        self.assertEqual(ids(m, "tables"), ["table:books"])
        self.assertEqual(m["endpoints"], [])
        self.assertTrue(any("createCoreRouter" in u["reason"] for u in m["unsupported"]))

    def test_content_type_without_router_is_reported(self):
        m = model_of("nextjs-strapi")
        reasons = [u["reason"] for u in m["unsupported"]]
        self.assertEqual(len(reasons), 1)
        self.assertIn("api::author.author", reasons[0])


if __name__ == "__main__":
    unittest.main()
