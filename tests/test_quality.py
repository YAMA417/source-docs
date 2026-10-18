"""品質ゲートの回帰テスト。実際の生成結果で起きた誤りを、そのまま再現して確かめる。

- GitHub OAuth に EmailProvider の SMTP_FROM が載った
- DB の UNIQUE / FK で、本文と未確認欄が食い違った
- API の共通仕様で「未ログインなら 403」と断定し、個別の API の未確認欄では分からないと書いた
"""

import json
import os
import shutil
import tempfile
import unittest

from helpers import analyze_files, by_id

from sdocs.core import facts as facts_mod
from sdocs.core.quality import _check_facts, validate_resolved_model
from sdocs.core.resolve import resolve
from sdocs.renderers import QualityGateError, render_docs

NEXT_AUTH = {
    "package.json": json.dumps({"dependencies": {"next": "14", "next-auth": "4", "@prisma/client": "5"}}),
    "lib/auth.ts": 'import GitHubProvider from "next-auth/providers/github";\n'
                   'import EmailProvider from "next-auth/providers/email";\n\n'
                   "export const authOptions = {\n  providers: [\n    GitHubProvider({\n"
                   "      clientId: process.env.GITHUB_CLIENT_ID,\n      clientSecret: process.env.GITHUB_CLIENT_SECRET,\n"
                   "    }),\n    EmailProvider({\n      from: process.env.SMTP_FROM,\n    }),\n  ],\n};\n",
    "app/api/auth/[...nextauth]/route.ts": 'import NextAuth from "next-auth";\n'
                                           'import { authOptions } from "@/lib/auth";\n'
                                           "const handler = NextAuth(authOptions);\n"
                                           "export { handler as GET, handler as POST };\n",
    "lib/session.ts": 'import { getServerSession } from "next-auth";\nimport { authOptions } from "@/lib/auth";\n'
                      "export async function session() {\n  return getServerSession(authOptions);\n}\n",
    # 明示的な未ログインの分岐がある API
    "app/api/posts/route.ts": 'import { getServerSession } from "next-auth";\nimport { authOptions } from "@/lib/auth";\n'
                              "export async function GET() {\n  const session = await getServerSession(authOptions);\n"
                              "  if (!session) {\n    return new Response(\"Unauthorized\", { status: 403 });\n  }\n"
                              "  return Response.json([]);\n}\n",
    # 分岐が無く、セッションの値を条件に渡しているだけの API（taxonomy の PATCH /api/posts/:postId と同じ形）
    "app/api/posts/[postId]/route.ts": 'import { getServerSession } from "next-auth";\n'
                                       'import { authOptions } from "@/lib/auth";\n'
                                       "export async function PATCH(req: Request) {\n"
                                       "  const session = await getServerSession(authOptions);\n"
                                       "  const count = await db.post.count({ where: { authorId: session?.user.id } });\n"
                                       "  if (count === 0) {\n    return new Response(null, { status: 403 });\n  }\n"
                                       "  return new Response(null, { status: 200 });\n}\n",
    "prisma/schema.prisma": "model User {\n  id String @id\n  email String @unique\n  posts Post[]\n"
                            '  @@map("users")\n}\nmodel Post {\n  id String @id\n  authorId String\n'
                            '  author User @relation(fields: [authorId], references: [id])\n  @@map("posts")\n}\n',
    "prisma/migrations/1_init/migration.sql":
        'CREATE TABLE "users" ("id" TEXT NOT NULL, "email" TEXT NOT NULL, PRIMARY KEY ("id"));\n'
        'CREATE UNIQUE INDEX "users_email_key" ON "users"("email");\n'
        'CREATE TABLE "posts" ("id" TEXT NOT NULL, "authorId" TEXT NOT NULL, PRIMARY KEY ("id"));\n'
        'ALTER TABLE "posts" ADD CONSTRAINT "posts_authorId_fkey" FOREIGN KEY ("authorId") REFERENCES "users"("id");\n',
    "prisma/migrations/2_drop_fk/migration.sql": 'ALTER TABLE "posts" DROP CONSTRAINT "posts_authorId_fkey";\n',
    "tsconfig.json": json.dumps({"compilerOptions": {"paths": {"@/*": ["./*"]}}}),
}


class GateCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = analyze_files(NEXT_AUTH)

    def render(self, annotations, only):
        out = tempfile.mkdtemp()
        try:
            render_docs(resolve(self.model, annotations), out, only=only)
            docs = {}
            for name in os.listdir(out):
                if name.endswith(".md"):
                    with open(os.path.join(out, name), encoding="utf-8") as f:
                        docs[name] = f.read()
            return docs
        finally:
            shutil.rmtree(out)


class IntegrationEnvTest(GateCase):
    def test_smtp_from_is_not_github_env(self):
        view = resolve(self.model, {})
        github = by_id(view)["integration:github-oauth"]
        self.assertEqual(github["envVars"], ["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"])
        self.assertEqual(by_id(view)["integration:email-login"]["envVars"], ["SMTP_FROM"])
        doc = self.render({}, ["integrations"])["integrations.md"]
        row = next(ln for ln in doc.splitlines() if ln.startswith("| GitHub（OAuth） |"))
        self.assertNotIn("SMTP_FROM", row)

    def test_disputed_env_leaves_body_and_is_conflict(self):
        """注釈で異議を出した環境変数は、本文から外れて「未確定（矛盾）」に出る。"""
        ann = {"disputes": [{"entity": "integration:email-login", "field": "envVars", "value": "SMTP_FROM",
                             "reason": "テスト用の異議", "evidence": ["lib/auth.ts:11"]}]}
        view = resolve(self.model, ann)
        email = by_id(view)["integration:email-login"]
        self.assertNotIn("SMTP_FROM", email["envVars"])
        self.assertEqual([(p["value"], p["status"]) for p in email["pending"]], [("SMTP_FROM", "conflict")])
        self.assertEqual(view["documentationStatus"], "FAILED")
        doc = self.render(ann, ["integrations"])["integrations.md"]
        body, _, pending = doc.partition("## 未確定")
        self.assertNotIn("SMTP_FROM", body)
        self.assertIn("`SMTP_FROM` | 矛盾", pending)


class DbTest(GateCase):
    def test_unique_from_prisma_and_migration(self):
        users = by_id(resolve(self.model, {}))["table:users"]
        email = {c["name"]: c for c in users["columns"]}["email"]
        self.assertTrue(email["unique"])
        doc = self.render({}, ["database"])["database.md"]
        self.assertIn("| `email` | TEXT | 不可 | — | UK |", doc)

    def test_dropped_fk_is_physical_false_and_orm_true(self):
        view = resolve(self.model, {})
        posts = by_id(view)["table:posts"]
        self.assertEqual(posts["physicalForeignKeys"], [])
        self.assertEqual([(r["name"], r["status"], r["physicalForeignKey"]) for r in posts["ormRelations"]],
                         [("author", "confirmed", False)])
        found = {(f["subject"], f["predicate"]): f for f in view["facts"]}
        self.assertIs(found[("column:posts.authorId", "physical_foreign_key")]["object"], False)
        self.assertEqual(found[("table:posts", "orm_relation")]["status"], "confirmed")
        doc = self.render({}, ["database"])["database.md"]
        self.assertIn("| 無い（migration で posts_authorId_fkey を削除した）", doc)
        self.assertNotIn("FK → users", doc)

    def test_body_and_unverified_contradiction_blocks_render(self):
        """本文で UNIQUE と確定しているのに、未確認で「一意か分からない」と書いたら書き出さない。"""
        ann = {"entities": {"table:users": {"summary": "利用者", "unverified": [
            {"item": "email に一意制約があるか", "searched": "schema.prisma"}]}}}
        view = resolve(self.model, ann)
        codes = [d["code"] for d in validate_resolved_model(view)]
        self.assertIn("gate.body-vs-unverified", codes)
        with self.assertRaises(QualityGateError):
            self.render(ann, ["database"])


class ApiAuthTest(GateCase):
    def test_unauthenticated_status_from_code_branch_only(self):
        view = resolve(self.model, {})
        idx = by_id(view)
        self.assertEqual((idx["endpoint:GET /api/posts"]["authCheck"]["unauthenticatedStatus"],
                          idx["endpoint:GET /api/posts"]["authCheck"]["status"]), (403, "confirmed"))
        patch = idx["endpoint:PATCH /api/posts/:postId"]
        self.assertEqual(patch["authCheck"]["status"], "unresolved")
        self.assertIn(("unauthenticated_status", "unresolved"),
                      [(p["field"], p["status"]) for p in patch["pending"]])

    def test_common_spec_must_not_overclaim(self):
        """共通仕様で「未ログインなら 403」と断定しても、確定できない API があれば書き出さない。"""
        ann = {"api": {"auth": "セッションで認証する。未ログインなら 403 を返す"}}
        view = resolve(self.model, ann)
        errors = validate_resolved_model(view)
        self.assertEqual([d["code"] for d in errors], ["gate.common-overclaim"])
        self.assertIn("PATCH /api/posts/:postId", errors[0]["message"])
        with self.assertRaises(QualityGateError):
            self.render(ann, ["api"])

    def test_common_spec_is_aggregated_from_endpoints(self):
        doc = self.render({"api": {"auth": "セッションで認証する"}}, ["api"])["api.md"]
        row = next(ln for ln in doc.splitlines() if ln.startswith("| 未ログイン時の応答 |"))
        self.assertIn("未ログイン時に 403 を返すことをコードの分岐で確認できた API（1 件）: `GET /api/posts`", row)
        self.assertIn("確定できない API（1 件）: `PATCH /api/posts/:postId`", row)

    def test_endpoint_error_row_without_code_branch_leaves_body(self):
        ann = {"entities": {"endpoint:PATCH /api/posts/:postId": {"summary": "更新", "errors": [
            {"status": 403, "condition": "ログインしていない"},
            {"status": 403, "condition": "本人の投稿でない"}]}}}
        view = resolve(self.model, ann)
        patch = by_id(view)["endpoint:PATCH /api/posts/:postId"]
        self.assertEqual([r["condition"] for r in patch["notes"]["errors"]], ["本人の投稿でない"])
        self.assertIn((403, "unresolved", "annotation"),
                      [(p["value"], p["status"], p.get("source")) for p in patch["pending"]])
        doc = self.render(ann, ["api"])["api.md"]
        body = doc.partition("## 未確定")[0]
        self.assertNotIn("ログインしていない", body)


class ClaimReadingTest(unittest.TestCase):
    """注釈の文から「未ログインなら 〇〇」を読み取る範囲。誤検知で書き出しを止めないため。"""

    def test_claims(self):
        self.assertEqual(facts_mod.unauth_claims("セッションで認証する。未ログインなら 403 を返す"), {403})
        self.assertEqual(facts_mod.unauth_claims("未ログインのときは、403 を返す"), {403})
        # 前の生成物（taxonomy）で品質ゲートが見逃した言い回し
        self.assertEqual(facts_mod.unauth_claims(
            "NextAuth のログインセッション（jwt 方式）。ログインが必要な API は、それぞれの API の中で"
            "セッションを確認し、無ければ 403 を返す"), {403})
        self.assertEqual(facts_mod.unauth_claims("セッションが無い場合は 401"), {401})

    def test_not_claims(self):
        # taxonomy の実際の注釈。422 は入力の検証エラー、Unauthorized は応答の本文の文字列
        self.assertEqual(facts_mod.unauth_claims(
            "共通の形式は無い。入力の検証エラー（422）は zod の issues 配列を JSON で返し、"
            "それ以外のエラーは本文なしか短い文字列（Unauthorized など）を返す"), set())
        self.assertEqual(facts_mod.unauth_claims("ログインしていない（本文は Unauthorized）"), set())


class ContradictoryFactTest(unittest.TestCase):
    def test_two_confirmed_values_are_an_error(self):
        f = [facts_mod.fact("column:users.email", "unique", True, "confirmed", "body"),
             facts_mod.fact("column:users.email", "unique", False, "confirmed", "body")]
        self.assertEqual([d["code"] for d in _check_facts(f, [])], ["gate.multiple-values"])

    def test_pending_fact_in_body_is_an_error(self):
        f = [facts_mod.fact("integration:github-oauth", "uses_env", "SMTP_FROM", "conflict", "body")]
        self.assertEqual([d["code"] for d in _check_facts(f, [])], ["gate.contaminated"])

    def test_same_precedence_disagreement_becomes_conflict_and_leaves_body(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"drizzle-orm": "0.30"}}),
            "src/schema.ts": "export const users = pgTable('users', {\n  id: serial('id').primaryKey(),\n"
                             "  email: text('email').notNull(),\n});\n",
            "requirements.txt": "sqlalchemy\nfastapi\n",
            "models.py": "class User(Base):\n    __tablename__ = 'users'\n"
                         "    id = Column(Integer, primary_key=True)\n    email = Column(String, nullable=True)\n",
        })
        view = resolve(m, {})
        users = by_id(view)["table:users"]
        self.assertNotIn("email", [c["name"] for c in users["columns"]])
        self.assertIn(("columns", "email", "conflict"), [(p["field"], p["value"], p["status"]) for p in users["pending"]])
        self.assertEqual(validate_resolved_model(view), [])  # 本文には混ざっていないので、書き出しは止めない
        self.assertEqual(view["documentationStatus"], "FAILED")


if __name__ == "__main__":
    unittest.main()
