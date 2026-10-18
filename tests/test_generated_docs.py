"""生成物（Markdown と HTML）そのものを検査する回帰テスト。

taxonomy の実際の生成物で起きた誤りを、同じ形の小さなプロジェクトで CLI から再現する。
内部のデータではなく、利用者が読む `integrations.md / .html`・`api.md / .html`・`database.md / .html` を読む。

- GitHub OAuth の環境変数に EmailProvider の SMTP_FROM が載った
- API の共通仕様で「ログインが必要な API は、セッションを確認し、無ければ 403 を返す」と全 API に断定した
- migration で削除した外部キーを、ER 図のカラムに `FK` と出した
"""

import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from helpers import CLI, write_project

TAXONOMY_LIKE = {
    "package.json": json.dumps({"dependencies": {"next": "14", "next-auth": "4", "@prisma/client": "5",
                                                 "postmark": "3"}}),
    "tsconfig.json": json.dumps({"compilerOptions": {"paths": {"@/*": ["./*"]}}}),
    # GitHub と EmailProvider が同じファイルにあり、メールの送信は Postmark で行う（taxonomy の lib/auth.ts と同じ形）
    "lib/auth.ts": 'import GitHubProvider from "next-auth/providers/github";\n'
                   'import EmailProvider from "next-auth/providers/email";\n'
                   'import { Client } from "postmark";\n\n'
                   "const postmarkClient = new Client(process.env.POSTMARK_API_TOKEN);\n\n"
                   "export const authOptions = {\n  providers: [\n    GitHubProvider({\n"
                   "      clientId: process.env.GITHUB_CLIENT_ID,\n"
                   "      clientSecret: process.env.GITHUB_CLIENT_SECRET,\n    }),\n"
                   "    EmailProvider({\n      from: process.env.SMTP_FROM,\n"
                   "      sendVerificationRequest: async ({ identifier }) => {\n"
                   "        await postmarkClient.sendEmailWithTemplate({ To: identifier, From: process.env.SMTP_FROM });\n"
                   "      },\n    }),\n  ],\n};\n",
    "app/api/auth/[...nextauth]/route.ts": 'import NextAuth from "next-auth";\n'
                                           'import { authOptions } from "@/lib/auth";\n'
                                           "const handler = NextAuth(authOptions);\n"
                                           "export { handler as GET, handler as POST };\n",
    # 未ログインの分岐（403）がある API
    "app/api/posts/route.ts": 'import { getServerSession } from "next-auth";\nimport { authOptions } from "@/lib/auth";\n'
                              "export async function POST(req: Request) {\n"
                              "  const session = await getServerSession(authOptions);\n"
                              "  if (!session) {\n    return new Response(\"Unauthorized\", { status: 403 });\n  }\n"
                              "  return Response.json({});\n}\n",
    "app/api/users/[userId]/route.ts": 'import { getServerSession } from "next-auth";\n'
                                       'import { authOptions } from "@/lib/auth";\n'
                                       "export async function PATCH(req: Request) {\n"
                                       "  const session = await getServerSession(authOptions);\n"
                                       "  if (!session?.user) {\n    return new Response(null, { status: 403 });\n  }\n"
                                       "  return new Response(null, { status: 200 });\n}\n",
    # 分岐が無く、セッションの値を条件に渡しているだけの API（未ログイン時の挙動はリポジトリ内で確定できない）
    "app/api/posts/[postId]/route.ts": 'import { getServerSession } from "next-auth";\n'
                                       'import { authOptions } from "@/lib/auth";\n'
                                       "export async function PATCH(req: Request) {\n"
                                       "  const session = await getServerSession(authOptions);\n"
                                       "  const count = await db.post.count({ where: { authorId: session?.user.id } });\n"
                                       "  if (count === 0) {\n    return new Response(null, { status: 403 });\n  }\n"
                                       "  return new Response(null, { status: 200 });\n}\n"
                                       "export async function DELETE(req: Request) {\n"
                                       "  const session = await getServerSession(authOptions);\n"
                                       "  const count = await db.post.count({ where: { authorId: session?.user.id } });\n"
                                       "  if (count === 0) {\n    return new Response(null, { status: 403 });\n  }\n"
                                       "  return new Response(null, { status: 204 });\n}\n",
    "prisma/schema.prisma": "model Account {\n  id String @id\n  userId String\n"
                            '  user User @relation(fields: [userId], references: [id])\n  @@map("accounts")\n}\n'
                            "model Session {\n  id String @id\n  userId String\n"
                            '  user User @relation(fields: [userId], references: [id])\n  @@map("sessions")\n}\n'
                            "model User {\n  id String @id\n  email String @unique\n  accounts Account[]\n"
                            '  sessions Session[]\n  posts Post[]\n  @@map("users")\n}\n'
                            "model Post {\n  id String @id\n  authorId String\n"
                            '  author User @relation(fields: [authorId], references: [id])\n  @@map("posts")\n}\n',
    "prisma/migrations/1_init/migration.sql":
        "CREATE TABLE `users` (`id` VARCHAR(191) NOT NULL, `email` VARCHAR(191) NOT NULL, PRIMARY KEY (`id`));\n"
        "CREATE UNIQUE INDEX `users_email_key` ON `users`(`email`);\n"
        "CREATE TABLE `accounts` (`id` VARCHAR(191) NOT NULL, `userId` VARCHAR(191) NOT NULL, PRIMARY KEY (`id`));\n"
        "CREATE TABLE `sessions` (`id` VARCHAR(191) NOT NULL, `userId` VARCHAR(191) NOT NULL, PRIMARY KEY (`id`));\n"
        "CREATE TABLE `posts` (`id` VARCHAR(191) NOT NULL, `authorId` VARCHAR(191) NOT NULL, PRIMARY KEY (`id`));\n"
        "ALTER TABLE `accounts` ADD CONSTRAINT `accounts_userId_fkey` FOREIGN KEY (`userId`) REFERENCES `users`(`id`);\n"
        "ALTER TABLE `sessions` ADD CONSTRAINT `sessions_userId_fkey` FOREIGN KEY (`userId`) REFERENCES `users`(`id`);\n"
        "ALTER TABLE `posts` ADD CONSTRAINT `posts_authorId_fkey` FOREIGN KEY (`authorId`) REFERENCES `users`(`id`);\n",
    # 後の migration で accounts と posts の外部キーを削除する（sessions は残す）
    "prisma/migrations/2_drop_fk/migration.sql":
        "ALTER TABLE `accounts` DROP FOREIGN KEY `accounts_userId_fkey`;\n"
        "ALTER TABLE `posts` DROP FOREIGN KEY `posts_authorId_fkey`;\n",
}

# 前の生成物（taxonomy）の注釈にあった、全 API への断定
OVERCLAIM = "ログインが必要な API は、それぞれの API の中でセッションを確認し、無ければ 403 を返す"


def run(*args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, encoding="utf-8", env=env)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def generate(annotations):
    """解析 → 注釈 → render（Markdown と HTML）。(出力先, render の結果) を返す。呼び出し側で消す。"""
    repo = write_project(TAXONOMY_LIKE)
    out = os.path.join(repo, "docs", "system")
    p = run("analyze", "--repo", repo, "--out", out)
    assert p.returncode == 0, p.stderr
    with open(os.path.join(out, ".source-docs", "annotations.json"), "w", encoding="utf-8") as f:
        json.dump(annotations, f, ensure_ascii=False)
    return repo, out, run("render", "--out", out, "--format", "both")


def html_text(fragment):
    """HTML の断片から、利用者が読む文字だけを取り出す。"""
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", fragment))


def md_section(doc, heading):
    """`### <heading>` から次の見出しまで。"""
    m = re.search(r"^#{2,3} " + re.escape(heading) + r".*?$(.*?)(?=^#{2,3} |\Z)", doc, re.M | re.S)
    return m.group(1) if m else None


def html_section_raw(doc, heading):
    """見出し `<h2>/<h3>` が heading で始まる節（次の h2/h3 まで）の HTML。"""
    for m in re.finditer(r"<h([23])[^>]*>(.*?)</h\1>(.*?)(?=<h[23][^>]*>|\Z)", doc, re.S):
        if html_text(m.group(2)).strip().startswith(heading):
            return m.group(3)
    return None


def html_section(doc, heading):
    raw = html_section_raw(doc, heading)
    return None if raw is None else html_text(raw)


def md_row(doc, first_cell):
    """連携先一覧の、1 列目が first_cell の行。"""
    return [ln for ln in (md_section(doc, "連携先一覧") or "").splitlines() if ln.startswith(f"| {first_cell} |")]


def html_row(doc, first_cell):
    rows = re.findall(r"<tr>(.*?)</tr>", html_section_raw(doc, "連携先一覧") or "", re.S)
    return [html_text(r) for r in rows
            if html_text(re.search(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S).group(1)).strip() == first_cell]


def er_diagrams_md(doc):
    return re.findall(r"```mermaid\n(erDiagram.*?)```", doc, re.S)


def er_diagrams_html(doc):
    return [html_mod.unescape(b) for b in re.findall(r"(erDiagram.*?)</(?:pre|div|code)>", doc, re.S)]


class GeneratedDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo, cls.out, cls.render = generate({"api": {"auth": "認証には NextAuth のセッションを利用する"}})
        assert cls.render.returncode == 0, cls.render.stdout + cls.render.stderr
        cls.md = {n: read(os.path.join(cls.out, f"{n}.md")) for n in ("integrations", "api", "database")}
        cls.html = {n: read(os.path.join(cls.out, "html", f"{n}.html")) for n in ("integrations", "api", "database")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    # --- 必須条件 1: GitHub OAuth に SMTP_FROM が載らない -------------------------------------------

    def views(self):
        """(名前, 連携先一覧の行を取る関数, 節を取る関数, 文書)。Markdown と HTML の両方を同じ検査に掛ける。"""
        return (("md", md_row, md_section, self.md["integrations"]),
                ("html", html_row, html_section, self.html["integrations"]))

    def test_github_oauth_env_vars(self):
        for name, row, section, doc in self.views():
            rows = row(doc, "GitHub（OAuth）")
            detail = section(doc, "GitHub（OAuth）")
            self.assertEqual(len(rows), 1, name)
            self.assertIsNotNone(detail, name)
            for github_oauth_section in (rows[0], detail):
                self.assertIn("GITHUB_CLIENT_ID", github_oauth_section, name)
                self.assertIn("GITHUB_CLIENT_SECRET", github_oauth_section, name)
                self.assertNotIn("SMTP_FROM", github_oauth_section, name)

    def test_smtp_from_belongs_to_email_side_only(self):
        for name, row, section, doc in self.views():
            for owner in ("メールでのログイン（NextAuth）", "Postmark"):
                self.assertIn("SMTP_FROM", row(doc, owner)[0], name)
                self.assertIn("SMTP_FROM", section(doc, owner), name)
            # 連携先一覧で SMTP_FROM を持つ行は、メールでのログインと Postmark だけ
            listing = md_section(doc, "連携先一覧") if name == "md" else html_section(doc, "連携先一覧")
            holders = {owner for owner in ("メールでのログイン（NextAuth）", "GitHub（OAuth）", "Postmark")
                       if "SMTP_FROM" in row(doc, owner)[0]}
            self.assertEqual(holders, {"メールでのログイン（NextAuth）", "Postmark"}, name)
            self.assertEqual(listing.count("SMTP_FROM"), 2, name)

    # --- 必須条件 2: API の共通仕様が、確定できない API まで 403 と断定しない ----------------------------

    def test_api_common_spec_md_and_html(self):
        for name, common in (("md", md_section(self.md["api"], "共通仕様")),
                             ("html", html_section(self.html["api"], "共通仕様"))):
            self.assertIsNotNone(common, name)
            self.assertNotIn(OVERCLAIM, common, name)
            self.assertIn("認証には NextAuth のセッションを利用する", common, name)
            self.assertIn("未ログイン時に 403 を返すことをコードの分岐で確認できた API（2 件）", common, name)
            self.assertIn("POST /api/posts", common, name)
            self.assertIn("PATCH /api/users/:userId", common, name)
            unknown = common.split("リポジトリ内だけでは確定できない API", 1)[1]
            self.assertIn("PATCH /api/posts/:postId", unknown, name)
            self.assertIn("DELETE /api/posts/:postId", unknown, name)

    def test_overclaiming_annotation_is_not_rendered(self):
        """前の生成物と同じ断定を注釈に書くと、品質ゲートが止め、api.md / api.html を書き出さない。"""
        repo, out, p = generate({"api": {"auth": "NextAuth のログインセッション（jwt 方式）。" + OVERCLAIM}})
        try:
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertIn("gate.common-overclaim", p.stdout + p.stderr)
            for path in (os.path.join(out, "api.md"), os.path.join(out, "html", "api.html")):
                self.assertFalse(os.path.exists(path) and OVERCLAIM in read(path), path)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_no_python_none_in_generated_docs(self):
        """値の決まらない未確定（未ログイン時の応答）を、`None` と出さない（taxonomy の features.md で起きた）。"""
        features = read(os.path.join(self.out, "features.md"))
        # 未解決の事実なので、未確認ではなく未確定に載せる
        self.assertIn("（`PATCH /api/posts/:postId`） | 未ログイン時の応答 | （値を決められない） | 未解決 |",
                      md_section(features, "未確定"))
        self.assertNotIn("未ログイン時の応答", md_section(features, "未確認"))
        for root, _, files in os.walk(self.out):
            for name in files:
                if name.endswith((".md", ".html")):
                    self.assertNotRegex(read(os.path.join(root, name)), r"\bNone\b", name)

    # --- 必須条件 3: ORM の関係と DB の外部キーを分ける ------------------------------------------------

    def test_er_diagram_marks_fk_only_for_physical_foreign_keys(self):
        for name, blocks in (("md", er_diagrams_md(self.md["database"])),
                             ("html", er_diagrams_html(self.html["database"]))):
            self.assertTrue(blocks, name)
            er = "\n".join(blocks)
            # migration で削除した外部キーのカラムには FK を付けない
            self.assertNotIn("VARCHAR userId FK", er.split("sessions {")[0], name)
            self.assertNotRegex(er, r"accounts \{[^}]*userId FK", name)
            self.assertNotRegex(er, r"posts \{[^}]*authorId FK", name)
            self.assertNotIn("VARCHAR authorId FK", er, name)
            # 残っている外部キー（sessions.userId）には付ける
            self.assertRegex(er, r"sessions \{[^}]*VARCHAR userId FK", name)
            # 線のラベル: 外部キーが無いものは ORM relation（点線）、あるものは Physical FK（実線）
            self.assertRegex(er, r'accounts \}o\.\.\|\| users : "ORM relation \(Prisma\)"', name)
            self.assertRegex(er, r'posts \}o\.\.\|\| users : "ORM relation \(Prisma\)"', name)
            self.assertRegex(er, r'sessions \}o--\|\| users : "Physical FK \(userId\) / ORM relation \(Prisma\)"', name)
            self.assertNotRegex(er, r'(accounts|posts) \}o--\|\|', name)

    def test_table_definition_separates_orm_relation_and_physical_fk(self):
        for name, doc, section in (("md", self.md["database"], md_section),
                                   ("html", self.html["database"], html_section)):
            posts = section(doc, "`posts`") if name == "md" else section(doc, "posts")
            self.assertIsNotNone(posts, name)
            self.assertNotIn("FK →", posts, name)
            self.assertIn("無い（migration で posts_authorId_fkey を削除した）", posts, name)
            self.assertIn("Prisma", posts, name)
            sessions = section(doc, "`sessions`") if name == "md" else section(doc, "sessions")
            self.assertIn("FK → users.id", sessions, name)


if __name__ == "__main__":
    unittest.main()
