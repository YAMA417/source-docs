"""Markdown / HTML / evidence の描画のテスト。"""

import json
import os
import re
import shutil
import tempfile
import unittest

from helpers import ShopDocs, analyze_files, try_symlink, write_project

from sdocs.core.model import model_dir
from sdocs.renderers import evidence_index, find_feature, render_docs, write_evidence
from sdocs.renderers import html as html_mod
from sdocs.renderers import mermaid as mm
from sdocs.core.resolve import resolve


class ShopRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = ShopDocs()
        cls.written = cls.docs.render()

    @classmethod
    def tearDownClass(cls):
        cls.docs.cleanup()

    def test_all_documents_exist(self):
        names = sorted(os.path.relpath(p, self.docs.out).replace(os.sep, "/") for p in self.written)
        self.assertEqual(names, ["api.md", "database.md", "detail/order-confirm.md", "features.md",
                                 "flow/order-confirm.md", "integrations.md", "overview.md", "screens.md"])

    def test_no_heading_deeper_than_h3(self):
        for path in self.written:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    self.assertFalse(re.match(r"^#{4,}\s", line), f"{path}: {line}")

    def test_every_document_ends_with_unverified(self):
        for path in self.written:
            self.assertIn("## 未確認", open(path, encoding="utf-8").read(), path)

    def test_overview_is_an_entrance(self):
        text = self.docs.read("overview.md")
        for section in ("## このシステムは何か", "### 主な利用者", "## 全体構成図", "## アプリケーションと責務",
                        "## 技術スタック", "## 主要コンポーネント", "## 主要機能", "## 資料の案内"):
            self.assertIn(section, text)
        self.assertIn("サンプル EC", text)
        self.assertIn("[機能一覧](features.md)", text)
        self.assertIn("画面を持たないもの", text)

    def test_features_table(self):
        text = self.docs.read("features.md")
        self.assertIn("| 機能 | 種別 | 起点 | Screen | API | DB | 外部連携 | 詳細設計 |", text)
        row = next(ln for ln in text.splitlines() if ln.startswith("| 注文確定 |"))
        self.assertIn("POST /api/orders", row)
        self.assertIn("orders (C)", row)
        self.assertIn("Stripe", row)
        self.assertIn("[詳細設計](detail/order-confirm.md)", row)
        self.assertIn("| cleanup-cron（名前未確定） | 定期実行 |", text)
        self.assertIn("| receipts（名前未確定） | キュー処理 |", text)

    def test_database(self):
        text = self.docs.read("database.md")
        self.assertIn("erDiagram", text)
        # migration が無いので、Drizzle の references()（drizzle-kit が FK として作る）を DB の外部キーとして実線で描く
        self.assertIn('orders }o--|| products : "Physical FK (product_id) / ORM relation (Drizzle)"', text)
        self.assertIn("定義の根拠: Drizzle", text)
        self.assertIn("### `orders` — 注文", text)
        self.assertIn("| `status` | order_status | 不可 | `\"pending\"` |  | 支払いの状態 |".replace("|  |", "| — |"),
                      text)
        self.assertIn("`orders_payment_intent_idx`", text)
        self.assertIn("## Enum", text)

    def test_api(self):
        text = self.docs.read("api.md")
        self.assertIn("## 共通仕様", text)
        self.assertIn("認証なし", text)
        self.assertIn("### `POST /api/orders` — 注文を作る", text)
        self.assertIn("| Body | `productId` | number | はい |", text)
        self.assertIn("（Webhook）", text)
        self.assertIn("ページング", text)  # 未確認として残る

    def test_screens(self):
        text = self.docs.read("screens.md")
        self.assertIn("flowchart TD", text)
        self.assertIn("|移動|", text)
        self.assertIn("### 注文確認 `/checkout`", text)
        self.assertIn("| 注文を確定する |", text)

    def test_integrations_never_show_values(self):
        text = self.docs.read("integrations.md")
        self.assertIn("`STRIPE_SECRET_KEY`", text)
        self.assertNotIn("FIXTURE_SECRET_VALUE", text)
        self.assertIn("| 方向 | 送信 / 受信 |", text)
        self.assertIn("`POST /api/webhooks/stripe`", text)

    def test_detail_sections(self):
        text = self.docs.read("detail/order-confirm.md")
        for section in ("## 概要", "## 入口", "## 前提条件", "## 入力", "## バリデーション", "## シーケンス",
                        "## 分岐とエラー", "## データへの影響", "## トランザクション", "## 同時実行・ロック",
                        "## 冪等性", "## 状態遷移", "## 外部連携", "## 副作用", "## 関連ファイル", "## 根拠",
                        "## 未確認"):
            self.assertIn(section, text)
        self.assertIn("sequenceDiagram", text)
        self.assertIn("alt productId が無い", text)
        self.assertIn("stateDiagram-v2", text)
        self.assertIn("[*] --> pending : 注文確定", text)
        self.assertIn("実装なし", text)                    # transaction: not-implemented
        self.assertIn("Stripe 側の設定は見ていない", text)  # idempotency の調べた範囲

    def test_flow_stays_short(self):
        text = self.docs.read("flow/order-confirm.md")
        self.assertNotIn("sequenceDiagram", text)
        steps = [ln for ln in text.splitlines() if re.match(r"^\d+\. ", ln)]
        self.assertLessEqual(len(steps), 5)
        self.assertIn("[機能一覧]" if False else "flowchart LR", text)

    def test_evidence_index(self):
        path = write_evidence(self.docs.resolved(), self.docs.out)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)["entities"]
        orders = data["endpoint:POST /api/orders"]
        self.assertEqual(orders["sources"][0]["file"], "src/app/api/orders/route.ts")
        self.assertEqual(orders["relations"]["tables"][0]["sources"][0]["file"], "src/services/orders.ts")
        self.assertIn("feature:order-confirm", data)
        self.assertIn("annotation", data["feature:order-confirm"])


class RenderOptionsTest(unittest.TestCase):
    def test_detail_skeleton_without_annotation(self):
        """注釈が無くても、モデルから分かる範囲で詳細設計の骨組みを書き、残りを未確認にする。"""
        docs = ShopDocs(annotations=False)
        try:
            written = docs.render(only=[], feature_keys=["feature:cron-cleanup-cron"], kinds=("detail",))
            self.assertEqual([os.path.basename(p) for p in written], ["cron-cleanup-cron.md"])
            text = docs.read("detail/cron-cleanup-cron.md")
            self.assertIn("`orders` | DELETE", text)
            self.assertIn("| cleanup-cron（名前未確定） | トランザクション | 未解決 | 注釈未作成 |", text)
            self.assertIn("## 認証・権限", text)
            self.assertIn("## 注意点", text)
        finally:
            docs.cleanup()

    def test_unknown_feature_is_an_error(self):
        docs = ShopDocs(annotations=False)
        try:
            with self.assertRaises(SystemExit):
                docs.render(only=[], feature_keys=["存在しない機能"])
        finally:
            docs.cleanup()

    def test_find_feature_by_name_slug_id(self):
        docs = ShopDocs()
        try:
            from sdocs.core.features import apply_annotations
            fs = apply_annotations(docs.model, docs.annotations())
            for key in ("注文確定", "order-confirm", "feature:order-confirm"):
                self.assertEqual(find_feature(fs, key)["id"], "feature:order-confirm")
        finally:
            docs.cleanup()


class ErSplitTest(unittest.TestCase):
    def test_many_tables_are_split(self):
        sql = "\n".join(f"create table t{i} (id int primary key, parent int references t{i - 1} (id));"
                        if i % 5 else f"create table t{i} (id int primary key);" for i in range(30))
        m = analyze_files({"schema.sql": sql})
        out = tempfile.mkdtemp()
        try:
            render_docs(resolve(m, {}), out, only=["database"])
            text = open(os.path.join(out, "database.md"), encoding="utf-8").read()
            self.assertGreaterEqual(text.count("erDiagram"), 3)
            self.assertIn("1 枚の図に描くのは 12 テーブルまで", text)
            for block in re.findall(r"```mermaid\nerDiagram\n(.*?)```", text, re.S):
                self.assertLessEqual(block.count(" {\n"), mm.MAX_NODES)
        finally:
            shutil.rmtree(out)


class StrapiApiSpecTest(unittest.TestCase):
    """Strapi の既定の API は、注釈が無くても入出力が schema.json から埋まり「未確認」にならない。"""

    def test_core_api_has_request_and_response(self):
        from helpers import fixture
        from sdocs.core.analyzer import analyze
        out = tempfile.mkdtemp()
        try:
            m = analyze([fixture("nextjs-strapi")], out)
            render_docs(resolve(m, {"api": {"auth": "不要"}}), out, only=["api"])
            text = open(os.path.join(out, "api.md"), encoding="utf-8").read()
            section = text.split("### `POST /api/books` —")[1].split("\n### ")[0]
            self.assertIn("作成する（Strapi の既定の API）", section)
            self.assertIn("| Body | `data.title` | string | はい |", section)
            # fixture は Strapi 5。v5 は data の直下に項目が並ぶ（v4 なら data.attributes の下）
            self.assertIn("| `data.title` | string |", section)
            self.assertIn("`data.documentId`", section)
            self.assertIn("認証: 共通仕様のとおり", section)
            self.assertNotIn("**未確認**", section)
        finally:
            shutil.rmtree(out)

    def test_check_counts_unannotated_apis(self):
        docs = ShopDocs()
        try:
            from sdocs.validators.annotations import check
            warnings = check(docs.model, docs.annotations(), docs.out).warnings
            self.assertTrue(any("注釈が足りないAPIが 2 件" in w for w in warnings), warnings)
        finally:
            docs.cleanup()


class ErRelationTest(unittest.TestCase):
    def test_strapi_relations_use_cardinality_and_are_not_doubled(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"@strapi/strapi": "4"}}),
            "src/api/a/content-types/a/schema.json": json.dumps({"collectionName": "as", "info": {"singularName": "a", "pluralName": "as"},
                "attributes": {"bs": {"type": "relation", "relation": "oneToMany", "target": "api::b.b"}}}),
            "src/api/b/content-types/b/schema.json": json.dumps({"collectionName": "bs", "info": {"singularName": "b", "pluralName": "bs"},
                "attributes": {"a": {"type": "relation", "relation": "manyToOne", "target": "api::a.a"}}}),
        })
        out = tempfile.mkdtemp()
        try:
            render_docs(resolve(m, {}), out, only=["database"])
            text = open(os.path.join(out, "database.md"), encoding="utf-8").read()
            # 双方向に宣言された 1 つの関係は 1 本だけ、カーディナリティつきで描く。
            # ORM の関係なので点線（DB の外部キーとは限らない）
            self.assertIn("as ||..o{ bs", text)
            self.assertNotIn("bs }o..|| as", text)
            self.assertNotIn("}o..o{", text)
            self.assertIn("**ORM の関係**", text)
        finally:
            shutil.rmtree(out)


class MermaidTest(unittest.TestCase):
    def test_label_escaping(self):
        label = mm.label('a "b" <script>; #x')
        self.assertNotIn('"', label)
        self.assertNotIn("<", label)
        self.assertNotIn(";", label.replace("#quot;", "").replace("#lt;", "").replace("#gt;", "")
                         .replace("#59;", "").replace("#35;", ""))

    def test_ident(self):
        self.assertEqual(mm.ident("screen:/books/:id", "s"), "screen__books__id")
        self.assertEqual(mm.ident("1x"), "n_1x")

    def test_components_and_pack(self):
        groups = mm.components(["a", "b", "c", "d"], [("a", "b")])
        self.assertEqual(groups[0], ["a", "b"])
        self.assertEqual(mm.pack([["a"] * 20], size=12)[0], ["a"] * 12)


class HtmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = ShopDocs()
        cls.docs.render()
        cls.site = os.path.join(cls.docs.out, "html")
        cls.written = html_mod.write_site(cls.docs.out, cls.site)

    @classmethod
    def tearDownClass(cls):
        cls.docs.cleanup()

    def read(self, rel):
        return open(os.path.join(self.site, *rel.split("/")), encoding="utf-8").read()

    def test_pages_including_subdirs(self):
        self.assertTrue(os.path.isfile(os.path.join(self.site, "detail", "order-confirm.html")))
        index = self.read("index.html")
        self.assertIn('href="detail/order-confirm.html"', index)
        self.assertIn("<h2>詳細設計</h2>", index)

    def test_navigation_from_subdir(self):
        page = self.read("detail/order-confirm.html")
        self.assertIn('href="../features.html"', page)
        self.assertIn('href="../style.css"', page)
        self.assertIn('href="../features.html">', page)

    def test_md_links_become_html(self):
        self.assertIn('href="detail/order-confirm.html"', self.read("features.html"))

    def test_mermaid_with_sri(self):
        page = self.read("overview.html")
        self.assertIn(html_mod.MERMAID_SRI, page)
        self.assertIn('securityLevel: "strict"', page)
        self.assertIn('<pre class="mermaid">', page)

    def test_escaped_pipe_in_table(self):
        self.assertEqual(html_mod.split_row("| a \\| b | c |"), ["a | b", "c"])

    def test_no_mermaid_option(self):
        out = tempfile.mkdtemp()
        try:
            html_mod.write_site(self.docs.out, out, mermaid=False)
            self.assertNotIn("<script", open(os.path.join(out, "overview.html"), encoding="utf-8").read())
        finally:
            shutil.rmtree(out)


class HtmlSafetyTest(unittest.TestCase):
    def test_href_allowlist(self):
        for bad in ("javascript:alert(1)", "JaVaScRiPt:x", "data:text/html,x", "vbscript:x", "java\nscript:x"):
            self.assertEqual(html_mod.safe_href(bad), "#", bad)
        self.assertEqual(html_mod.safe_href("db.md"), "db.html")
        self.assertEqual(html_mod.safe_href("https://a.test/x?a=1&b=2"), "https://a.test/x?a=1&amp;b=2")

    def test_inline_html_is_escaped(self):
        out = html_mod.render_inline('<script>alert(1)</script> [x](https://a.test/" onmouseover="y)')
        self.assertNotIn("<script>", out)
        self.assertNotIn('onmouseover="y', out)

    def test_nav_escapes_names(self):
        nav = html_mod._nav_html([('x" onmouseover="alert(1)', "題")], None)
        self.assertNotIn('onmouseover="alert', nav)

    def test_symlinked_md_is_not_read(self):
        src = write_project({"real.md": "# 実物\n", "secret.txt": "PRIVATE_MATERIAL"})
        out = tempfile.mkdtemp()
        try:
            try_symlink(self, os.path.join(src, "secret.txt"), os.path.join(src, "attack.md"))
            html_mod.write_site(src, out)
            self.assertFalse(os.path.exists(os.path.join(out, "attack.html")))
        finally:
            shutil.rmtree(src)
            shutil.rmtree(out)

    def test_dangerous_out_dirs(self):
        src = write_project({"a.md": "# a\n"})
        try:
            for bad in (os.path.join(src, ".git", "x"), os.path.join(src, "node_modules", "x"),
                        os.path.expanduser("~")):
                with self.assertRaises(SystemExit, msg=bad):
                    html_mod.write_site(src, bad)
        finally:
            shutil.rmtree(src)

    def test_unterminated_fence_keeps_rest(self):
        kinds = [b[0] for b in html_mod.parse_blocks("```ts\nconst a = 1;\n\n## 見出し\n")]
        self.assertIn("heading", kinds)


class EvidenceIndexUnitTest(unittest.TestCase):
    def test_contains_every_entity(self):
        docs = ShopDocs(annotations=False)
        try:
            data = evidence_index(resolve(docs.model, {}))
            for coll in ("screens", "endpoints", "tables", "entrypoints"):
                for item in docs.model[coll]:
                    self.assertIn(item["id"], data)
            self.assertTrue(os.path.isdir(model_dir(docs.out)))
        finally:
            docs.cleanup()


if __name__ == "__main__":
    unittest.main()
