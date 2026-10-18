"""検出器ごとの単体テスト。小さなプロジェクトをその場で作って解析する。"""

import json
import unittest

from helpers import analyze_files, by_id, env_vars, ids

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
        self.assertIn(["code", "total"], [u["columns"] for u in orders["uniques"]])
        self.assertEqual({c["expression"] for c in orders["checks"]}, {"total >= 0", "length(memo) < 100"})

    def test_composite_pk_fk_index(self):
        items = self.idx["table:items"]
        self.assertEqual(items["primaryKey"], ["order_id", "sku"])
        self.assertEqual(items["physicalForeignKeys"][0]["references"], {"table": "orders", "columns": ["id"]})
        self.assertEqual(items["physicalForeignKeys"][0]["status"], "confirmed")
        self.assertEqual(items["indexes"], [{"name": "items_sku_idx", "columns": ["sku"], "unique": True,
                                             "source": "migration"}])

    def test_enum_and_sequence(self):
        self.assertEqual(self.idx["enum:order_status"]["values"], ["pending", "paid"])
        self.assertIn("sequence:invoice_no", ids(self.m, "sequences"))


class MysqlMigrationTest(unittest.TestCase):
    """Prisma が MySQL 向けに出すマイグレーションの書き方（shadcn-ui/taxonomy で見つけたもの）。"""

    def setUp(self):
        self.m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@prisma/client": "5"}}),
            "prisma/schema.prisma": 'model Post {\n  id String @id\n  authorId String\n'
                                    '  author User @relation(fields: [authorId], references: [id])\n  @@map(name: "posts")\n}\n'
                                    'model User {\n  id String @id\n  email String? @unique\n  posts Post[]\n'
                                    '  @@map(name: "users")\n}\n',
            "prisma/migrations/1_init/migration.sql":
                "CREATE TABLE `users` (\n  `id` VARCHAR(191) NOT NULL,\n  `email` VARCHAR(191) NULL,\n"
                "  UNIQUE INDEX `users_email_key`(`email`),\n  PRIMARY KEY (`id`)\n);\n"
                "CREATE TABLE `posts` (\n  `id` VARCHAR(191) NOT NULL,\n  `authorId` VARCHAR(191) NOT NULL,\n"
                "  INDEX `posts_authorId_idx`(`authorId`),\n  PRIMARY KEY (`id`)\n);\n"
                "ALTER TABLE `posts` ADD CONSTRAINT `posts_authorId_fkey` FOREIGN KEY (`authorId`) "
                "REFERENCES `users`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;\n",
            "prisma/migrations/2_stripe/migration.sql":
                "ALTER TABLE `posts` DROP FOREIGN KEY `posts_authorId_fkey`;\n"
                "ALTER TABLE `users` ADD COLUMN `stripe_customer_id` VARCHAR(191) NULL,\n"
                "    ADD COLUMN `stripe_price_id` VARCHAR(191) NULL;\n",
        })
        self.idx = by_id(self.m)

    def test_inline_unique_and_plain_index(self):
        users = self.idx["table:users"]
        self.assertIn({"name": "users_email_key", "columns": ["email"], "unique": True, "source": "migration"},
                      users["indexes"])
        posts = self.idx["table:posts"]
        self.assertIn({"name": "posts_authorId_idx", "columns": ["authorId"], "unique": False,
                       "source": "migration"}, posts["indexes"])

    def test_alter_with_several_columns(self):
        cols = {c["name"]: c["type"] for c in self.idx["table:users"]["columns"]}
        self.assertEqual(cols["stripe_customer_id"], "VARCHAR(191)")
        self.assertEqual(cols["stripe_price_id"], "VARCHAR(191)")

    def test_dropped_fk_is_not_a_db_fk_but_orm_relation_remains(self):
        posts = self.idx["table:posts"]
        self.assertEqual(posts["droppedForeignKeys"], ["posts_authorId_fkey"])
        # DB の外部キーは migration で消えた。Prisma の relation は ORM の関係として別に残る
        self.assertEqual(posts["physicalForeignKeys"], [])
        self.assertEqual([(r["name"], r["source"], r["physicalForeignKey"]) for r in posts["ormRelations"]],
                         [("author", "prisma", False)])
        self.assertIn("posts_authorId_fkey", posts["ormRelations"][0]["physicalForeignKeyReason"])
        self.assertEqual(posts["physicalSource"], "migration")


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
        # Prisma の relation は物理 FK の根拠にしない
        self.assertEqual(post["physicalForeignKeys"], [])
        self.assertEqual(post["ormRelations"], [{"name": "author", "columns": ["author_id"],
                                                 "references": {"table": "users", "columns": ["id"]},
                                                 "onDelete": None, "source": "prisma", "status": "confirmed",
                                                 "physicalForeignKey": None,
                                                 "physicalForeignKeyReason": "migration が無く、DB に外部キーがあるかを確かめられない"}])
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
            "app/api/og/route.tsx": "export function GET() { return null }\n",
        })
        self.assertEqual(ids(m, "screens"), ["screen:/blog/*slug", "screen:/cart", "screen:/login"])
        self.assertEqual(ids(m, "endpoints"), ["endpoint:DELETE /api/items/:id", "endpoint:GET /api/items/:id",
                                               "endpoint:GET /api/og"])
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
        self.assertEqual(idx["endpoint:POST /api/a"]["status"], "confirmed")
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


class RealWorldLessonsTest(unittest.TestCase):
    """実在の OSS（shadcn-ui/taxonomy・strapi/foodadvisor）で見つけた不具合の再発防止。"""

    def test_prisma_map_name_and_migration_are_one_table(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@prisma/client": "5"}}),
            "prisma/schema.prisma": 'datasource db {\n  provider = "mysql"\n  url = env("DATABASE_URL")\n}\n'
                                    'model User {\n  id String @id\n  name String?\n  @@map(name: "users")\n}\n',
            "prisma/migrations/1_init/migration.sql":
                "CREATE TABLE `users` (`id` VARCHAR(191) NOT NULL, `name` VARCHAR(191) NULL, PRIMARY KEY (`id`));\n",
        })
        self.assertEqual(ids(m, "tables"), ["table:users"])
        users = by_id(m)["table:users"]
        self.assertEqual([(c["name"], c["type"]) for c in users["columns"]],
                         [("id", "VARCHAR(191)"), ("name", "VARCHAR(191)")])  # マイグレーションを正とする
        self.assertEqual(users["primaryKey"], ["id"])
        self.assertEqual(m["applications"][0]["stack"]["db"], ["MySQL"])

    def test_url_helper_prefix(self):
        m = analyze_files({
            "client/package.json": json.dumps({"dependencies": {"next": "13"}}),
            "client/utils/index.js": "export function getStrapiURL(path) {\n  return `${\n"
                                     "    process.env.NEXT_PUBLIC_API_URL || 'http://localhost:1337'\n  }/api${path}`;\n}\n",
            "client/pages/blog.js": "import { getStrapiURL } from '../utils';\n"
                                    "export default function Blog() { return null }\n"
                                    "export async function getServerSideProps() {\n"
                                    "  await fetch(getStrapiURL(`/articles?populate=*`));\n  return { props: {} };\n}\n",
            "client/pages/api/articles.js": "export default function h(req, res) { if (req.method === 'GET') {} }\n",
        })
        self.assertEqual([r["id"] for r in by_id(m)["screen:/blog"]["calls"]], ["endpoint:GET /api/articles"])

    def test_only_the_imported_function_is_followed(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"next": "15", "drizzle-orm": "0.36"}}),
            "src/schema.ts": "export const a = pgTable('a', { id: serial('id') });\n"
                             "export const b = pgTable('b', { id: serial('id') });\n",
            "src/repo.ts": "import { a, b } from './schema';\n"
                           "export async function listA() {\n  return db.select().from(a);\n}\n"
                           "export async function removeB() {\n  return db.delete(b);\n}\n",
            "app/api/a/route.ts": "import { listA } from '@/repo';\nexport async function GET() { return listA(); }\n",
        })
        get = by_id(m)["endpoint:GET /api/a"]
        self.assertEqual([(r["id"], r["operations"]) for r in get["tables"]], [("table:a", ["READ"])])

    def test_config_passed_inside_function_is_not_followed(self):
        """`getServerSession(authOptions)` から認証設定の連携先を拾わない。入口を作る `NextAuth(authOptions)` は拾う。"""
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"next": "13", "next-auth": "4", "postmark": "3"}}),
            "lib/auth.ts": "import { Client } from 'postmark';\nimport GitHubProvider from 'next-auth/providers/github';\n"
                           "import { env } from '@/env.mjs';\n"
                           "const postmarkClient = new Client(env.POSTMARK_API_TOKEN);\n"
                           "export const authOptions = {\n  providers: [GitHubProvider({ clientId: env.GITHUB_CLIENT_ID }),\n"
                           "    { send: () => postmarkClient.sendEmail({}) }],\n};\n",
            "lib/session.ts": "import { getServerSession } from 'next-auth';\nimport { authOptions } from './auth';\n"
                              "export async function getCurrentUser() {\n  const s = await getServerSession(authOptions);\n"
                              "  return s?.user;\n}\n",
            "app/dashboard/page.tsx": "import { getCurrentUser } from '@/lib/session';\n"
                                      "export default async function D() { await getCurrentUser(); return null }\n",
            "pages/api/auth/[...nextauth].ts": "import NextAuth from 'next-auth';\nimport { authOptions } from '@/lib/auth';\n"
                                               "export default NextAuth(authOptions);\n",
        })
        idx = by_id(m)
        self.assertNotIn("integrations", idx["screen:/dashboard"])
        auth = idx["endpoint:ANY /api/auth/*nextauth"]
        self.assertEqual(sorted(r["id"] for r in auth["integrations"]),
                         ["integration:github-oauth", "integration:postmark"])
        self.assertEqual(env_vars(idx["integration:postmark"]), {"POSTMARK_API_TOKEN": ("confirmed", "client-init")})
        self.assertEqual(env_vars(idx["integration:github-oauth"]), {"GITHUB_CLIENT_ID": ("confirmed", "client-init")})


class RealRunFeedbackTest(unittest.TestCase):
    """実際の Claude Code でスキルを動かしたときに報告された問題の再発防止。"""

    def test_link_to_api_route_is_a_call(self):
        m = analyze_files({
            "package.json": NEXT,
            "pages/index.js": "export default function H() { return <a href=\"/api/exit-preview\">x</a> }\n",
            "pages/api/exit-preview.js": "export default function h(req, res) { if (req.method === 'GET') {} }\n",
        })
        home = by_id(m)["screen:/"]
        self.assertEqual([r["id"] for r in home["calls"]], ["endpoint:GET /api/exit-preview"])
        self.assertNotIn("unmatched", home)

    def test_strapi_cron_enabled_without_tasks_is_reported(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@strapi/strapi": "4"}}),
            "config/server.js": "module.exports = ({ env }) => ({\n  cron: { enabled: true, tasks: plugin },\n});\n",
        })
        self.assertTrue(any("cron が有効" in u["reason"] for u in m["unsupported"]))


if __name__ == "__main__":
    unittest.main()


class FactStatusTest(unittest.TestCase):
    """事実の状態（confirmed / inferred / unresolved / conflict）が根拠どおりに付く。"""

    def test_env_used_only_in_same_file_is_unresolved(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"stripe": "14"}}),
            "lib/pay.ts": "import Stripe from 'stripe';\nconst s = new Stripe(process.env.STRIPE_KEY!);\n"
                          "const base = process.env.APP_BASE_URL;\nexport const pay = () => s.charges.create({});\n",
        })
        self.assertEqual(env_vars(by_id(m)["integration:stripe"]), {
            "STRIPE_KEY": ("confirmed", "client-init"), "APP_BASE_URL": ("unresolved", "same-file")})

    def test_env_passed_through_variable_is_inferred(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"openai": "4"}}),
            "lib/ai.ts": "import OpenAI from 'openai';\nconst key = process.env.OPENAI_KEY;\n"
                         "export const ai = new OpenAI({ apiKey: key });\n",
        })
        self.assertEqual(env_vars(by_id(m)["integration:openai"]), {"OPENAI_KEY": ("inferred", "init-variable")})

    def test_same_precedence_sources_disagreeing_is_conflict(self):
        """migration が無く、同じ優先度の 2 つの定義（Drizzle と SQLAlchemy）が NULL 可否で食い違う。"""
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"drizzle-orm": "0.30"}}),
            "src/schema.ts": "export const users = pgTable('users', {\n  id: serial('id').primaryKey(),\n"
                             "  email: text('email').notNull(),\n});\n",
            "requirements.txt": "sqlalchemy\nfastapi\n",
            "models.py": "class User(Base):\n    __tablename__ = 'users'\n"
                         "    id = Column(Integer, primary_key=True)\n    email = Column(String, nullable=True)\n",
        })
        users = by_id(m)["table:users"]
        self.assertEqual([d["severity"] for d in m["diagnostics"] if d["code"] == "db.conflict"], ["ERROR"])
        # 矛盾したカラムは本文用の columns に残さず、未確定へ送る
        self.assertNotIn("email", [c["name"] for c in users["columns"]])
        self.assertIn(("columns", "email", "conflict"),
                      [(p["field"], p["value"], p["status"]) for p in users["pending"]])

    def test_depth_decides_relation_status(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"next": "15", "drizzle-orm": "0.30"}}),
            "src/schema.ts": "export const a = pgTable('a', { id: serial('id') });\n",
            "src/repo.ts": "import { a } from './schema';\nexport async function listA() {\n"
                           "  return db.select().from(a);\n}\n",
            "app/api/a/route.ts": "import { listA } from '@/repo';\nimport { a } from '@/schema';\n"
                                  "export async function GET() { await db.delete(a); return listA(); }\n",
        })
        rel = by_id(m)["endpoint:GET /api/a"]["tables"][0]
        # 入口のコード片で直接 DELETE、import 先で READ。同じテーブルなら強い方（confirmed）になる
        self.assertEqual((rel["id"], rel["operations"], rel["status"]), ("table:a", ["READ", "DELETE"], "confirmed"))


class EnvScopeTest(unittest.TestCase):
    def test_same_scope_is_unresolved_and_only_for_that_integration(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"stripe": "14", "openai": "4"}}),
            "lib/both.ts": "import Stripe from 'stripe';\nimport OpenAI from 'openai';\n"
                           "const s = new Stripe(process.env.STRIPE_KEY!);\nconst ai = new OpenAI({ apiKey: process.env.OPENAI_KEY });\n"
                           "export async function pay() {\n  const currency = process.env.PAY_CURRENCY;\n"
                           "  return s.charges.create({});\n}\n",
        })
        idx = by_id(m)
        self.assertEqual(env_vars(idx["integration:stripe"])["PAY_CURRENCY"], ("unresolved", "same-scope"))
        self.assertNotIn("PAY_CURRENCY", env_vars(idx["integration:openai"]))
