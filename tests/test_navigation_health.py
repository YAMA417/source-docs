"""目次から詳細設計への導線・資料の状態（Overall Status）と品質ゲートの診断の分離・機能一覧の説明文の回帰テスト。

taxonomy の生成物で、利用者が読み違えた・辿れなかった箇所をそのまま検査する。
"""

import os
import re
import shutil
import tempfile
import unittest

from helpers import ShopDocs, analyze_files, write_project
from sdocs.core.analyzer import analyze

from sdocs.core.quality import STATUS_RULES, status_of, status_reasons
from sdocs.core.resolve import resolve
from sdocs.renderers import html as html_mod, render_docs
from sdocs.validators.health import result_of
from test_generated_docs import TAXONOMY_LIKE, generate, html_section, md_section, read

LEGEND = "「名前未確定」はコードの識別子をそのまま名前にしている候補"


def detail_links(index_html):
    """index.html の「詳細設計」の節にあるリンク先。"""
    m = re.search(r"<h2>詳細設計</h2>(.*?)(?=<h2>|</div>)", index_html, re.S)
    return re.findall(r'href="([^"]+)"', m.group(1)) if m else None


class IndexNavigationTest(unittest.TestCase):
    def test_every_generated_detail_is_listed(self):
        """生成済みの detail をすべて目次に載せる。ファイル名は生成物から取り、テストに書かない。"""
        docs = ShopDocs()
        try:
            docs.render()
            # 2 件目の詳細設計を置き、ファイル名を決め打ちしていないことを確かめる
            detail_dir = os.path.join(docs.out, "detail")
            first = sorted(os.listdir(detail_dir))[0]
            shutil.copy(os.path.join(detail_dir, first), os.path.join(detail_dir, "another-feature.md"))
            generated = sorted(f"detail/{n[:-3]}.html" for n in os.listdir(detail_dir) if n.endswith(".md"))
            self.assertGreaterEqual(len(generated), 2)
            site = os.path.join(docs.out, "html")
            html_mod.write_site(docs.out, site)
            index = read(os.path.join(site, "index.html"))
            self.assertIn("<h2>全体</h2>", index)
            self.assertEqual(sorted(detail_links(index)), generated)
            for link in generated:
                self.assertTrue(os.path.isfile(os.path.join(site, *link.split("/"))), link)
        finally:
            docs.cleanup()

    def test_no_detail_yet(self):
        repo, out, p = generate({})
        try:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            index = read(os.path.join(out, "html", "index.html"))
            self.assertEqual(detail_links(index), [])
            self.assertIn("詳細設計はまだ生成されていません。", index)
        finally:
            shutil.rmtree(repo, ignore_errors=True)


class OverallStatusRuleTest(unittest.TestCase):
    def view(self, **counts):
        q = {"confirmed": 1, "inferred": 0, "unresolved": 0, "conflict": 0, "unsupported": 0, "errors": 0}
        q.update(counts)
        return {"quality": q}

    def test_rule(self):
        cases = [({}, "SAFE"), ({"unresolved": 5}, "WARNING"), ({"unsupported": 1}, "WARNING"),
                 ({"conflict": 1}, "FAILED"), ({"errors": 1}, "FAILED"),
                 ({"errors": 1, "unresolved": 5}, "FAILED"), ({"conflict": 1, "unsupported": 2}, "FAILED")]
        for counts, expected in cases:
            self.assertEqual(status_of(self.view(**counts)), expected, counts)

    def test_reasons_list_every_condition_in_rule_order(self):
        status, reasons = status_reasons(self.view(unresolved=5))
        self.assertEqual(status, "WARNING")
        self.assertEqual([label for label, _, _ in reasons],
                         [label for _, conds in STATUS_RULES for _, label in conds])
        self.assertIn(("未解決の事実（unresolved）", 5, "WARNING"), reasons)

    def test_deterministic(self):
        model = analyze_files(TAXONOMY_LIKE)
        results = {(resolve(model, {})["documentationStatus"],
                    tuple(status_reasons(resolve(model, {}))[1])) for _ in range(3)}
        self.assertEqual(len(results), 1)

    def test_check_does_not_soften_failed(self):
        """check の結果は、資料の状態が FAILED なら診断の内容によらず FAILED。"""
        self.assertEqual(result_of([], {"documentationStatus": "FAILED"}), "FAILED")
        self.assertEqual(result_of([], {"documentationStatus": "WARNING"}), "WARNING")
        self.assertEqual(result_of([{"severity": "WARNING"}], {"documentationStatus": "SAFE"}), "WARNING")
        self.assertEqual(result_of([], {"documentationStatus": "SAFE"}), "SAFE")


class HealthSeparationTest(unittest.TestCase):
    """未解決の事実はあるが、品質ゲートの WARNING は 0 件。この 2 つを別のものとして表示する。"""

    @classmethod
    def setUpClass(cls):
        view = resolve(analyze_files(TAXONOMY_LIKE), {})
        # 品質ゲートの WARNING が 0 件の状態を作る（未解決の事実は残す）
        view["diagnostics"] = [d for d in view["diagnostics"] if d["severity"] != "WARNING"]
        cls.unresolved = view["quality"]["unresolved"]
        assert cls.unresolved > 0 and view["documentationStatus"] == "WARNING"
        cls.out = tempfile.mkdtemp(prefix="sd-health-")
        render_docs(view, cls.out, only=["overview"])
        html_mod.write_site(cls.out, os.path.join(cls.out, "html"))
        cls.md = read(os.path.join(cls.out, "overview.md"))
        cls.html = read(os.path.join(cls.out, "html", "overview.html"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def sections(self):
        return (("md", md_section, self.md), ("html", html_section, self.html))

    def test_overall_status_with_reasons(self):
        for name, section, doc in self.sections():
            overall = section(doc, "資料の状態（Overall Status）")
            self.assertIsNotNone(overall, name)
            self.assertIn("WARNING", overall.split("理由")[0], name)
            self.assertIn(f"未解決の事実（unresolved）: {self.unresolved} 件（1 件以上なら WARNING）", overall, name)
            self.assertIn("根拠どうしの矛盾（conflict）: 0 件", overall, name)
            self.assertIn("品質ゲートの ERROR: 0 件", overall, name)

    def test_fact_status_table(self):
        fact = md_section(self.md, "事実の状態（Fact Status）")
        self.assertIn(f"| Unresolved | 未解決 | {self.unresolved} |", fact)
        self.assertIn("| Conflict | 矛盾 | 0 |", fact)

    def test_quality_gate_diagnostics_are_separate(self):
        diag_md = md_section(self.md, "品質ゲートの診断（Quality Gate Diagnostics）")
        for sev in ("ERROR", "WARNING", "INFO"):
            self.assertIn(f"| {sev} | 0 |", diag_md)
        self.assertIn("資料の状態（Overall Status）とは別のもの", diag_md)
        diag_html = re.search(r"品質ゲートの診断（Quality Gate Diagnostics）</h3>(.*?)<h3", self.html, re.S).group(1)
        cells = [re.sub(r"<[^>]+>", " ", r).split() for r in re.findall(r"<tr>(.*?)</tr>", diag_html, re.S)]
        self.assertEqual(cells, [["Severity", "件数"], ["ERROR", "0"], ["WARNING", "0"], ["INFO", "0"]])
        # 資料の状態の節には、診断の件数を混ぜない
        self.assertNotIn("Severity", md_section(self.md, "資料の状態（Overall Status）"))


class UnresolvedEntityTest(unittest.TestCase):
    """HTTP メソッドを決められない API（foodadvisor の pages/api/preview.js と同じ形）。

    資料の状態が数える未解決の件数と、資料末尾の「未確定」に載る行が一致する。
    """

    @classmethod
    def setUpClass(cls):
        model = analyze_files({
            "package.json": '{"dependencies": {"next": "14"}}',
            "pages/index.js": "export default function Home() { return null }\n",
            "pages/api/preview.js": "export default function handler(req, res) {\n"
                                    "  res.setPreviewData({});\n  res.redirect('/');\n}\n",
        })
        cls.view = resolve(model, {})
        cls.out = tempfile.mkdtemp(prefix="sd-unresolved-")
        render_docs(cls.view, cls.out, only=["overview", "api"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_api_and_its_entrypoint_count_once(self):
        self.assertEqual(self.view["quality"]["unresolved"], 1)

    def test_listed_in_pending(self):
        api = read(os.path.join(self.out, "api.md"))
        pending = md_section(api, "未確定")
        self.assertIn("| `ANY /api/preview` | API | `ANY /api/preview` | 未解決 | 受け付ける HTTP メソッドをコードから特定できない |",
                      pending)
        self.assertNotIn("受け付ける HTTP メソッド", md_section(api, "未確認") or "")
        overview = read(os.path.join(self.out, "overview.md"))
        self.assertIn("未解決の事実（unresolved）: 1 件", overview)


FASTAPI_SQLMODEL = {
    # full-stack-fastapi-template と同じ形: SQLModel の table=True、親クラスから項目を継ぐ、画面は TanStack Router
    "backend/pyproject.toml": '[project]\nname = "app"\ndependencies = ["fastapi>=0.1", "sqlmodel>=0.0.1"]\n',
    "backend/app/models.py": (
        "from sqlmodel import Field, Relationship, SQLModel\n\n"
        "class UserBase(SQLModel):\n"
        "    email: str = Field(unique=True, index=True, max_length=255)\n"
        "    full_name: str | None = Field(default=None, max_length=255)\n\n"
        "class User(UserBase, table=True):\n"
        "    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)\n"
        "    hashed_password: str\n"
        "    items: list[Item] = Relationship(back_populates=\"owner\", cascade_delete=True)\n\n"
        "class Item(SQLModel, table=True):\n"
        "    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)\n"
        "    owner_id: uuid.UUID = Field(\n        foreign_key=\"user.id\", nullable=False, ondelete=\"CASCADE\"\n    )\n"),
    "backend/app/api/routes/items.py": (
        "from fastapi import APIRouter\nrouter = APIRouter(prefix=\"/items\")\n\n"
        "@router.get(\"/\")\ndef read_items():\n    return []\n"),
    "frontend/package.json": '{"dependencies": {"react": "19", "@tanstack/react-router": "1"}}',
    "frontend/src/routes/login.tsx": "export const Route = createFileRoute('/login')({})\n",
}


class SqlModelAndUnreadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = analyze_files(FASTAPI_SQLMODEL)

    def test_sqlmodel_tables(self):
        tables = {t["key"]: t for t in self.model["tables"]}
        self.assertEqual(sorted(tables), ["item", "user"])
        cols = {c["name"]: c for c in tables["user"]["columns"]}
        # 親クラス（UserBase）の項目も持つ。Relationship はカラムにしない
        self.assertEqual(sorted(cols), ["email", "full_name", "hashed_password", "id"])
        self.assertTrue(cols["id"]["primaryKey"])
        self.assertTrue(cols["email"]["unique"])
        self.assertTrue(cols["full_name"]["nullable"])
        fks = tables["item"]["physicalForeignKeys"]
        self.assertEqual([(f["columns"], f["references"]["table"]) for f in fks], [(["owner_id"], "user")])

    def test_unread_screens_are_not_zero(self):
        """画面を作る依存があるのに読めなかったら、「画面 0 件」と確定せず未確認に出し、SAFE にしない。"""
        self.assertIn("screens", [u["kind"] for u in self.model["unsupported"]])
        view = resolve(self.model, {})
        self.assertNotEqual(view["documentationStatus"], "SAFE")
        out = tempfile.mkdtemp(prefix="sd-unread-")
        try:
            render_docs(view, out, only=["screens"])
            doc = read(os.path.join(out, "screens.md"))
            self.assertNotIn("画面 0 件", doc)
            self.assertIn("画面のルーティングを読み取れなかった", md_section(doc, "未確認"))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_unread_tables_are_not_zero(self):
        files = dict(FASTAPI_SQLMODEL, **{"backend/app/models.py": "from sqlmodel import SQLModel\nTABLES = load()\n"})
        model = analyze_files(files)
        out = tempfile.mkdtemp(prefix="sd-unread-")
        try:
            render_docs(resolve(model, {}), out, only=["database"])
            doc = read(os.path.join(out, "database.md"))
            self.assertNotIn("テーブル 0 件", doc)
            self.assertIn("テーブルの定義を読み取れなかった", md_section(doc, "未確認"))
        finally:
            shutil.rmtree(out, ignore_errors=True)


class SchemaQualifiedReferenceTest(unittest.TestCase):
    def test_auth_users_is_not_public_users(self):
        """`REFERENCES auth.users` を、同じ名前の public.users と取り違えない（nextjs-subscription-payments）。"""
        model = analyze_files({
            "package.json": '{"dependencies": {"@supabase/supabase-js": "2"}}',
            "supabase/migrations/0001_init.sql": (
                "create table users (id uuid references auth.users not null primary key, full_name text);\n"
                "create table customers (id uuid references auth.users not null primary key);\n"
                "create table subscriptions (id text primary key, user_id uuid references auth.users not null,"
                " owner uuid references public.users(id));\n"),
        })
        tables = {t["key"]: t for t in model["tables"]}
        refs = {(t, ",".join(f["columns"])): f["references"]["table"]
                for t in ("users", "customers", "subscriptions") for f in tables[t]["physicalForeignKeys"]}
        self.assertEqual(refs[("users", "id")], "auth.users")
        self.assertEqual(refs[("customers", "id")], "auth.users")
        self.assertEqual(refs[("subscriptions", "user_id")], "auth.users")
        self.assertEqual(refs[("subscriptions", "owner")], "users")
        view = resolve(model, {})
        self.assertEqual([d for d in view["diagnostics"] if d["severity"] == "ERROR"], [])

    def test_same_fk_in_schema_and_migration_is_drawn_once(self):
        sql = "create table users (id uuid references auth.users not null primary key);\n"
        model = analyze_files({"package.json": '{"dependencies": {"@supabase/supabase-js": "2"}}',
                               "schema.sql": sql, "supabase/migrations/0001_init.sql": sql})
        users = [t for t in model["tables"] if t["key"] == "users"][0]
        self.assertEqual(len(users["physicalForeignKeys"]), 1)
        out = tempfile.mkdtemp(prefix="sd-fk-")
        try:
            render_docs(resolve(model, {}), out, only=["database"])
            doc = read(os.path.join(out, "database.md"))
            self.assertIn("FK → auth.users（主キー）", doc)
        finally:
            shutil.rmtree(out, ignore_errors=True)


class AdditionsTest(unittest.TestCase):
    """解析器が読めない書き方でも、Claude が読んで根拠つきで足せば資料になる（使っているサービスに依らない）。"""

    def setUp(self):
        self.root = write_project(FASTAPI_SQLMODEL)
        self.out = os.path.join(self.root, "docs", "system")
        self.model = analyze([self.root], self.out)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def check(self, additions):
        from sdocs.validators.annotations import check
        return check(self.model, {"schema": "source-docs/annotations", "version": 1, "additions": additions}, self.out)

    def test_screen_added_from_code(self):
        ann = {"additions": [{"kind": "screen", "route": "/login", "name": "ログイン",
                              "evidence": ["frontend/src/routes/login.tsx:1"]}]}
        self.assertEqual(self.check(ann["additions"]).errors, [])
        view = resolve(self.model, ann)
        screens = {s["route"]: s for s in view["screens"]}
        self.assertEqual(screens["/login"]["status"], "inferred")
        # 足したので「画面を読み取れなかった」は消え、資料に画面が載る
        self.assertNotIn("screens", [u["kind"] for u in view["unsupported"]])
        render_docs(view, self.out, only=["screens"])
        doc = read(os.path.join(self.out, "screens.md"))
        self.assertIn("`/login`", doc)
        self.assertIn("画面 1 件", doc)

    def test_evidence_must_contain_the_name(self):
        result = self.check([{"kind": "screen", "route": "/signup",
                              "evidence": ["frontend/src/routes/login.tsx:1"]}])
        self.assertTrue(any("書かれていない" in e for e in result.errors), result.errors)

    def test_evidence_is_required(self):
        result = self.check([{"kind": "endpoint", "method": "POST", "path": "/api/x"}])
        self.assertTrue(any("根拠" in e for e in result.errors), result.errors)


KIDS_PLAY_LIKE = {
    # kids-play-monorepo と同じ形: Next.js が `${API_URL}/snaps/...`（API_URL は .../api）で Strapi の独自ルートを呼ぶ
    "frontend/package.json": '{"name": "web", "dependencies": {"next": "14", "react": "18"}}',
    "frontend/app/page.tsx": "export default function Home() { return null }\n",
    "frontend/app/api/snaps/route.ts": (
        "const API_URL = process.env.STRAPI_API_URL\n"
        "export async function POST(req: Request) {\n"
        "  const res = await fetch(`${API_URL}/snaps/upload-image`, { method: 'POST' })\n"
        "  return Response.json(await res.json())\n}\n"),
    "frontend/lib/__tests__/strapi.test.ts": "fetch('https://strapi.internal.example.com/api/x')\n",
    "backend/strapi-app/package.json": '{"name": "cms", "dependencies": {"@strapi/strapi": "5", "react": "18"}}',
    "backend/strapi-app/src/api/snap/routes/custom-snap.ts": (
        "export default {\n  routes: [\n    {\n      method: 'POST',\n      path: '/snaps/upload-image',\n"
        "      handler: 'snap.uploadImage',\n      config: {\n        policies: [],\n      },\n    },\n  ],\n}\n"),
}


class KidsPlayShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = analyze_files(KIDS_PLAY_LIKE)

    def test_strapi_route_with_nested_config(self):
        """`config: { policies: [] }` を持つ独自ルートも読む。"""
        self.assertIn("POST /api/snaps/upload-image", [e["key"].split("@")[0] for e in self.model["endpoints"]])

    def test_call_with_api_base_url_reaches_strapi(self):
        """`${API_URL}/snaps/upload-image` を Strapi の POST /api/snaps/upload-image への呼び出しと推定する。"""
        ep = [e for e in self.model["endpoints"] if e["key"].startswith("POST /api/snaps@")
              or e["key"] == "POST /api/snaps"][0]
        calls = [(r["id"], r["status"]) for r in ep.get("calls", [])]
        self.assertTrue(any("upload-image" in i and st == "inferred" for i, st in calls), calls)

    def test_test_code_hosts_are_not_integrations(self):
        self.assertNotIn("strapi.internal.example.com", [i["name"] for i in self.model["integrations"]])

    def test_backend_admin_react_is_not_unread_screens(self):
        self.assertNotIn("screens", [u["kind"] for u in self.model["unsupported"]])

    def test_request_header_is_not_a_conflict(self):
        """入力検証のスキーマ（本文）に無いヘッダーを、注釈に書いても矛盾にしない。"""
        files = dict(KIDS_PLAY_LIKE, **{"frontend/app/api/hook/route.ts": (
            "import { z } from 'zod'\nconst Body = z.object({ id: z.string() })\n"
            "export async function POST(req: Request) {\n  const body = Body.parse(await req.json())\n"
            "  return Response.json(body)\n}\n")})
        model = analyze_files(files)
        eid = [e["id"] for e in model["endpoints"] if "/api/hook" in e["id"]][0]
        ann = {"entities": {eid: {"summary": "hook", "request": [
            {"in": "header", "name": "x-webhook-secret", "type": "string"},
            {"in": "body", "name": "id", "type": "string"}]}}}
        view = resolve(model, ann)
        self.assertEqual(view["quality"]["conflict"], 0)
        ep = [e for e in view["endpoints"] if e["id"] == eid][0]
        self.assertIn("x-webhook-secret", [f["name"] for f in ep["resolvedRequest"]["fields"]])


class VerifyApiHeadingTest(unittest.TestCase):
    def test_server_action_heading_is_not_a_table(self):
        """API 仕様の見出し `### \\`server.updatePassword\\` — …` をテーブルとして照合しない（nextjs-subscription-payments）。"""
        from sdocs.validators.verify import verify
        model = {"endpoints": [{"key": "server.updatePassword", "path": "server.updatePassword", "method": "ACTION",
                                "protocol": "rpc"}], "tables": [], "repositories": []}
        doc = "### `server.updatePassword` — パスワードを変える\n"
        result = verify(model, tempfile.gettempdir(), {"api.md": doc})
        self.assertEqual([f for f in result["findings"] if f["kind"] == "table"], [])


class PendingAndUnverifiedTest(unittest.TestCase):
    """未確定に載せた事実を、注釈でも「分からない」と書いたとき、未確認に同じ事実を重ねて載せない。"""

    def test_annotation_explains_the_pending_row(self):
        model = analyze_files(TAXONOMY_LIKE)
        eid = "endpoint:PATCH /api/posts/:postId"
        ann = {"entities": {eid: {"summary": "投稿を更新する", "unverified": [
            {"item": "未ログインのときの応答", "kind": "library-owned", "searched": "route.ts に未ログインの分岐が無い",
             "about": {"predicate": "unauthenticated_status"}}]}}}
        out = tempfile.mkdtemp(prefix="sd-merge-")
        try:
            render_docs(resolve(model, ann), out, only=["api"])
            api = read(os.path.join(out, "api.md"))
            pending = md_section(api, "未確定")
            row = [r for r in pending.splitlines() if r.startswith("| `PATCH /api/posts/:postId` | 未ログイン時の応答")]
            self.assertEqual(len(row), 1, pending)
            self.assertIn("注釈: route.ts に未ログインの分岐が無い", row[0])
            self.assertNotIn("未ログインのときの応答", md_section(api, "未確認") or "")
        finally:
            shutil.rmtree(out, ignore_errors=True)


class LibraryOwnedEndpointTest(unittest.TestCase):
    """入出力をライブラリが決める API（taxonomy の NextAuth の受け口）を、注釈が足りないと警告しない。"""

    FILES = {
        "package.json": '{"dependencies": {"next": "14", "next-auth": "4"}}',
        "pages/api/auth/[...nextauth].ts": 'import NextAuth from "next-auth";\n'
                                           "export default NextAuth({ providers: [] });\n",
    }

    def warnings(self, note):
        from sdocs.validators.annotations import check
        model = analyze_files(self.FILES)
        eid = [e["id"] for e in model["endpoints"] if "nextauth" in e["id"]][0]
        ann = {"schema": "source-docs/annotations", "version": 1, "entities": {eid: note}}
        out = tempfile.mkdtemp(prefix="sd-lib-")
        try:
            return [w for w in check(model, ann, out).warnings if "注釈が足りない" in w]
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_library_owned_is_enough(self):
        self.assertEqual(self.warnings({
            "summary": "NextAuth.js の認証用の入口",
            "unverified": [{"item": "受け付けるサブパス・メソッド・入出力", "kind": "library-owned",
                            "searched": "NextAuth(authOptions) を返すだけ"}]}), [])

    def test_api_doc_does_not_ask_for_request_and_response(self):
        """API 仕様の未確認に「リクエストの形 — 注釈未作成」を出さない（すぐ下の library-owned と食い違う）。"""
        model = analyze_files(self.FILES)
        eid = [e["id"] for e in model["endpoints"] if "nextauth" in e["id"]][0]
        ann = {"entities": {eid: {"summary": "NextAuth.js の認証用の入口", "unverified": [
            {"item": "受け付けるサブパス・メソッド・入出力", "kind": "library-owned", "searched": "NextAuth を返すだけ"}]}}}
        out = tempfile.mkdtemp(prefix="sd-lib-")
        try:
            render_docs(resolve(model, ann), out, only=["api"])
            unverified = md_section(read(os.path.join(out, "api.md")), "未確認")
            self.assertIn("受け付けるサブパス・メソッド・入出力", unverified)
            self.assertNotIn("リクエストの形", unverified)
            self.assertNotIn("レスポンスの形", unverified)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_missing_fields_are_named(self):
        warnings = self.warnings({"summary": "NextAuth.js の認証用の入口"})
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("（request・responses）", warnings[0])


class FeatureLegendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = analyze_files(TAXONOMY_LIKE)

    def features_md(self, annotations):
        out = tempfile.mkdtemp(prefix="sd-features-")
        try:
            render_docs(resolve(self.model, annotations), out, only=["features"])
            return read(os.path.join(out, "features.md"))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_legend_only_when_some_feature_is_unnamed(self):
        doc = self.features_md({})
        self.assertIn("（名前未確定）", doc)
        self.assertIn(LEGEND, doc)

    def test_no_legend_when_every_feature_is_named(self):
        ids = [f["id"] for f in resolve(self.model, {})["features"]]
        named = {"features": {fid: {"name": f"機能 {i}"} for i, fid in enumerate(ids)}}
        doc = self.features_md(named)
        self.assertNotIn("（名前未確定）", doc)
        self.assertNotIn(LEGEND, doc)
        self.assertIn("DB 欄の括弧は操作", doc)


if __name__ == "__main__":
    unittest.main()
