"""検出器ごとの単体テスト。小さなプロジェクトをその場で作って解析する。"""

import json
import unittest

from helpers import analyze_files, by_id, ids

NEXT = json.dumps({"dependencies": {"next": "15.0.0", "react": "19.0.0"}})


class SqlTest(unittest.TestCase):
    def setUp(self):
        self.m = analyze_files({"db/migrations/001.sql": """
CREATE TYPE order_status AS ENUM ('pending', 'paid');
CREATE SEQUENCE invoice_no;
CREATE TABLE IF NOT EXISTS "orders" (
  id serial PRIMARY KEY,
  code varchar(20) NOT NULL UNIQUE,
  total numeric(10, 2) DEFAULT 0 CHECK (total >= 0),
  memo text,
  CONSTRAINT orders_code_total UNIQUE (code, total)
);
CREATE TABLE items (order_id int, sku text, PRIMARY KEY (order_id, sku),
  FOREIGN KEY (order_id) REFERENCES orders (id));
ALTER TABLE orders ADD COLUMN note text NOT NULL DEFAULT '';
CREATE UNIQUE INDEX items_sku_idx ON items (sku);
CREATE MATERIALIZED VIEW order_totals AS SELECT 1;
CREATE TABLE tmp (id int);
DROP TABLE tmp;
""", "db/migrations/002.sql": "-- ; はコメントの中\nALTER TABLE orders ADD CONSTRAINT memo_len CHECK (length(memo) < 100);\n"})
        self.idx = by_id(self.m)

    def test_tables_views_drop(self):
        self.assertEqual(ids(self.m, "tables"), ["table:items", "table:order_totals", "table:orders"])
        self.assertEqual(self.idx["table:order_totals"]["kind"], "materialized-view")

    def test_columns_and_constraints(self):
        orders = self.idx["table:orders"]
        cols = {c["name"]: c for c in orders["columns"]}
        self.assertTrue(cols["id"]["primaryKey"])
        self.assertFalse(cols["code"]["nullable"])
        self.assertTrue(cols["code"]["unique"])
        self.assertEqual(cols["total"]["default"], "0")
        self.assertTrue(cols["memo"]["nullable"])
        self.assertEqual(cols["note"]["default"], "''")
        self.assertIn(["code", "total"], orders["uniques"])
        self.assertEqual({c["expression"] for c in orders["checks"]}, {"total >= 0", "length(memo) < 100"})

    def test_composite_pk_fk_index(self):
        items = self.idx["table:items"]
        self.assertEqual(items["primaryKey"], ["order_id", "sku"])
        self.assertEqual(items["foreignKeys"][0]["references"], {"table": "orders", "columns": ["id"]})
        self.assertEqual(items["indexes"], [{"name": "items_sku_idx", "columns": ["sku"], "unique": True}])

    def test_enum_and_sequence(self):
        self.assertEqual(self.idx["enum:order_status"]["values"], ["pending", "paid"])
        self.assertIn("sequence:invoice_no", ids(self.m, "sequences"))


class PrismaTest(unittest.TestCase):
    def test_map_relation_index_enum(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@prisma/client": "6.0.0"}}),
            "prisma/schema.prisma": """
model User {
  id     Int     @id @default(autoincrement())
  email  String  @unique @db.VarChar(255)
  posts  Post[]
  role   Role    @default(MEMBER)
  @@map("users")
}
model Post {
  id       Int    @id
  authorId Int    @map("author_id")
  author   User   @relation(fields: [authorId], references: [id])
  title    String?
  @@index([authorId], map: "posts_author_idx")
}
enum Role {
  ADMIN
  MEMBER
}
""",
            "src/repo.ts": "import { prisma } from './db';\nexport const f = () => prisma.user.findMany();\n",
        })
        idx = by_id(m)
        self.assertEqual(ids(m, "tables"), ["table:Post", "table:users"])
        users = idx["table:users"]
        cols = {c["name"]: c for c in users["columns"]}
        self.assertEqual(cols["email"]["type"], "String (VarChar(255))")
        self.assertEqual(cols["role"]["default"], "MEMBER")
        post = idx["table:Post"]
        self.assertEqual(post["foreignKeys"], [{"columns": ["author_id"],
                                                "references": {"table": "users", "columns": ["id"]}}])
        self.assertTrue({c["name"]: c for c in post["columns"]}["title"]["nullable"])
        self.assertEqual(post["indexes"][0]["name"], "posts_author_idx")
        self.assertEqual(idx["enum:Role"]["values"], ["ADMIN", "MEMBER"])


class NextJsTest(unittest.TestCase):
    def test_route_groups_parallel_and_methods(self):
        m = analyze_files({
            "package.json": NEXT,
            "app/(shop)/cart/page.tsx": "export default function Cart() { return null }",
            "app/@modal/login/page.tsx": "export default function L() { return null }",
            "app/blog/[...slug]/page.tsx": "export default function B() { return null }",
            "app/api/items/[id]/route.ts": "async function h() {}\nexport { h as GET, h as DELETE };\n",
            "app/api/broken/route.ts": "export const dynamic = 'force-dynamic';\n",
        })
        self.assertEqual(ids(m, "screens"), ["screen:/blog/*slug", "screen:/cart", "screen:/login"])
        self.assertEqual(ids(m, "endpoints"), ["endpoint:DELETE /api/items/:id", "endpoint:GET /api/items/:id"])
        self.assertEqual(m["unsupported"][0]["file"], "app/api/broken/route.ts")

    def test_pages_router_api_without_method_is_unresolved(self):
        m = analyze_files({
            "package.json": NEXT,
            "pages/index.tsx": "export default function Home() { return null }",
            "pages/_app.tsx": "export default function App() { return null }",
            "pages/api/a.ts": "export default function h(req, res) { if (req.method === 'POST') {} }",
            "pages/api/b.ts": "export default function h(req, res) { res.json({}) }",
        })
        self.assertEqual(ids(m, "screens"), ["screen:/"])
        idx = by_id(m)
        self.assertEqual(idx["endpoint:POST /api/a"]["status"], "detected")
        self.assertEqual(idx["endpoint:ANY /api/b"]["status"], "unresolved")

    def test_server_actions_are_rpc(self):
        m = analyze_files({
            "package.json": NEXT,
            "app/actions.ts": "'use server';\nexport async function createTodo() {}\n",
        })
        e = m["endpoints"][0]
        self.assertEqual((e["protocol"], e["path"]), ("rpc", "actions.createTodo"))
        self.assertEqual(m["entrypoints"][0]["type"], "rpc")


class JsHttpTest(unittest.TestCase):
    def test_express_mount_and_dynamic_path(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"express": "4.0.0"}}),
            "src/app.ts": "import express from 'express';\nimport users from './users';\n"
                          "const app = express();\napp.use('/api/users', users);\n"
                          "app.get(PATH, h);\n",
            "src/users.ts": "import { Router } from 'express';\nconst router = Router();\n"
                            "router.get('/', list);\nrouter.route('/:id').get(show).put(update);\n"
                            "export default router;\n",
            "src/client.ts": "import axios from 'axios';\naxios.get('/api/users');\n",
        })
        self.assertEqual(ids(m, "endpoints"), ["endpoint:GET /api/users", "endpoint:GET /api/users/:id",
                                               "endpoint:PUT /api/users/:id"])
        self.assertTrue(any("変数" in u["reason"] for u in m["unsupported"]))

    def test_fastify_register_prefix(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"fastify": "5.0.0"}}),
            "src/server.ts": "import Fastify from 'fastify';\nimport routes from './routes';\n"
                             "const app = Fastify();\napp.register(routes, { prefix: '/v2' });\n",
            "src/routes.ts": "import type { FastifyInstance } from 'fastify';\n"
                             "export default async function routes(fastify: FastifyInstance) {\n"
                             "  fastify.post('/jobs', async () => ({}));\n}\n",
        })
        self.assertEqual(ids(m, "endpoints"), ["endpoint:POST /v2/jobs"])


class BackgroundTest(unittest.TestCase):
    def test_entrypoint_kinds(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@nestjs/schedule": "4.0.0", "amqplib": "0.10.0",
                                                         "commander": "12.0.0", "socket.io": "4.0.0"},
                                        "bin": {"shopctl": "bin/cli.js"}}),
            "src/tasks.ts": "import { Cron, Interval } from '@nestjs/schedule';\nclass T {\n"
                            "  @Cron('0 0 * * *')\n  nightly() {}\n  @Interval(5000)\n  poll() {}\n}\n",
            "src/consumer.ts": "import amqp from 'amqplib';\nch.consume('mail', async (msg) => {});\n",
            "src/producer.ts": "import amqp from 'amqplib';\nexport function f() { ch.sendToQueue('mail', b); }\n",
            "src/listener.ts": "button.on('click', h);\nbus.on('user:signup', h);\n",
            "bin/cli.js": "import { program } from 'commander';\nprogram.command('import-csv').action(() => {});\n",
            "src/ws.ts": "const io = new Server();\nio.on('connection', (socket) => {\n"
                         "  socket.on('chat:send', h);\n  socket.on('disconnect', h);\n});\n",
            "schema.graphql": "type Query {\n  books: [Book]\n}\ntype Mutation {\n  addBook(title: String): Book\n}\n",
            "vercel.json": json.dumps({"crons": [{"path": "/api/cron/sync", "schedule": "*/5 * * * *"}]}),
        })
        types = sorted({e["type"] for e in m["entrypoints"]})
        self.assertEqual(types, ["cli", "cron", "event-handler", "graphql", "queue-consumer", "scheduler",
                                 "websocket"])
        jobs = {j["id"]: j for j in m["jobs"]}
        self.assertEqual(jobs["job:cron:nightly"]["schedule"], "0 0 * * *")
        self.assertEqual(jobs["job:scheduler:poll"]["schedule"], "interval 5000ms")
        self.assertEqual(jobs["job:cron:/api/cron/sync"]["schedule"], "*/5 * * * *")
        self.assertIn("event:user:signup", ids(m, "events"))
        self.assertNotIn("event:click", ids(m, "events"))
        self.assertEqual(ids(m, "commands"), ["command:import-csv", "command:shopctl"])
        self.assertIn("endpoint:websocket chat:send", ids(m, "endpoints"))
        self.assertNotIn("endpoint:websocket disconnect", ids(m, "endpoints"))
        self.assertIn("endpoint:graphql Mutation.addBook", ids(m, "endpoints"))

    def test_trpc_procedures(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@trpc/server": "11.0.0"}}),
            "src/router.ts": "import { initTRPC } from '@trpc/server';\nconst t = initTRPC.create();\n"
                             "export const appRouter = t.router({\n"
                             "  listTodos: publicProcedure.query(() => []),\n"
                             "  addTodo: protectedProcedure.input(z.string()).mutation(({ input }) => input),\n"
                             "});\n",
        })
        self.assertEqual(ids(m, "endpoints"), ["endpoint:rpc trpc.addTodo", "endpoint:rpc trpc.listTodos"])
        self.assertEqual({e["method"] for e in m["endpoints"]}, {"QUERY", "MUTATION"})

    def test_vercel_cron_calls_route(self):
        m = analyze_files({
            "package.json": NEXT,
            "vercel.json": json.dumps({"crons": [{"path": "/api/cron/sync", "schedule": "0 * * * *"}]}),
            "app/api/cron/sync/route.ts": "export async function GET() {}\n",
        })
        job = by_id(m)["job:cron:/api/cron/sync"]
        self.assertEqual([r["id"] for r in job["calls"]], ["endpoint:GET /api/cron/sync"])
        # 定期実行から呼ばれる API は、定期実行の機能に含まれ、単独の機能にならない
        self.assertEqual([f["type"] for f in m["features"]], ["cron"])

    def test_cron_constant_schedule_is_unresolved(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@nestjs/schedule": "4.0.0"}}),
            "src/t.ts": "class T {\n  @Cron(CronExpression.EVERY_HOUR)\n  hourly() {}\n}\n",
        })
        job = m["jobs"][0]
        self.assertEqual(job["status"], "unresolved")

    def test_worker_without_loop_is_unsupported(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"react": "19.0.0"}}),
            "src/workers/idle.ts": "export const x = 1;\n",
        })
        self.assertEqual(m["jobs"], [])
        self.assertEqual(m["unsupported"][0]["kind"], "worker")


class PythonTest(unittest.TestCase):
    def test_celery_task_and_enqueue(self):
        m = analyze_files({
            "requirements.txt": "fastapi\ncelery\n",
            "app/tasks.py": "from celery import shared_task\n\n@shared_task\ndef send_mail(x):\n    pass\n",
            "app/api.py": "from fastapi import APIRouter\nfrom .tasks import send_mail\n"
                          "router = APIRouter()\n\n@router.post('/mail')\ndef post():\n    send_mail.delay(1)\n",
        })
        self.assertEqual(ids(m, "jobs"), ["job:queue-consumer:send_mail"])
        post = by_id(m)["endpoint:POST /mail"]
        self.assertEqual([r["id"] for r in post["enqueues"]], ["job:queue-consumer:send_mail"])


class IntegrationTest(unittest.TestCase):
    def test_internal_host_is_not_recorded(self):
        m = analyze_files({
            "package.json": NEXT,
            "app/api/x/route.ts": "export async function GET() {\n"
                                  "  await fetch('http://10.0.0.5/api');\n"
                                  "  await fetch('https://billing.corp/api');\n"
                                  "  await fetch('https://api.github.com/repos');\n}\n",
        })
        self.assertEqual(ids(m, "integrations"), ["integration:host:api.github.com"])
        self.assertNotIn("10.0.0.5", json.dumps(m))
        self.assertNotIn("billing.corp", json.dumps(m))
        self.assertTrue(any(u["kind"] == "integration" for u in m["unsupported"]))

    def test_unused_import_is_not_a_relation(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"next": "15", "stripe": "17"}}),
            "app/api/x/route.ts": "import Stripe from 'stripe';\nexport async function GET() { return 1 }\n",
        })
        self.assertIn("integration:stripe", ids(m, "integrations"))
        self.assertNotIn("integrations", by_id(m)["endpoint:GET /api/x"])


if __name__ == "__main__":
    unittest.main()
