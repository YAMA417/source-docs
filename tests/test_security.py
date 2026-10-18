"""開かないファイル・シンボリックリンク・機密検出のテスト。"""

import json
import os
import shutil
import tempfile
import unittest

from helpers import ShopDocs, analyze_files, try_symlink, write_project

from sdocs.core import fs, security
from sdocs.core.analyzer import analyze
from sdocs.validators.secrets import blocking, mask, scan
from sdocs.core.resolve import resolve


class SecretFileTest(unittest.TestCase):
    def test_env_files_are_blocked(self):
        for name in (".env", ".env.local", ".env.production", "config/.env.staging"):
            self.assertTrue(security.is_secret_file(name), name)

    def test_env_templates_are_allowed(self):
        for name in (".env.example", ".env.sample", ".env.template", ".env.dist"):
            self.assertFalse(security.is_secret_file(name), name)

    def test_keys_and_credentials(self):
        for name in ("id_rsa", "server.pem", "cert.key", "store.p12", "release.jks", "credentials.json",
                     "service-account.json", ".npmrc", ".netrc", "AuthKey_X.p8", "prod.tfvars",
                     ".git-credentials", ".pgpass", ".envrc"):
            self.assertTrue(security.is_secret_file(name), name)

    def test_secret_directories(self):
        for path in ("a/.ssh/config", "a/.aws/config", "a/.gnupg/x", "a/.kube/config",
                     "credentials/google.json", "secrets/x.yaml", "app\\.ssh\\known_hosts"):
            self.assertTrue(security.is_secret_path(path), path)
        self.assertFalse(security.is_secret_path("src/credentials.test.ts"))

    def test_env_example_yields_names_only(self):
        names = security.env_var_names("# c\nexport API_KEY=abc123\nDB_URL = postgres://x\n\n9BAD=1\n")
        self.assertEqual(names, ["API_KEY", "DB_URL"])


class RepoWalkTest(unittest.TestCase):
    def test_secret_files_are_not_listed_or_read(self):
        root = write_project({
            ".env": "TOKEN=VALUE_FROM_DOTENV", ".env.example": "TOKEN=",
            "credentials/svc.json": "{}", "src/a.ts": "export const a = 1;",
            "node_modules/x/index.js": "x", "id_rsa": "KEY",
        })
        try:
            repo = fs.Repo("r", root)
            self.assertEqual(repo.files, [".env.example", "src/a.ts"])
            self.assertEqual(repo.read(".env"), "")
        finally:
            shutil.rmtree(root)

    def test_symlinked_file_is_not_read(self):
        root = write_project({"real.txt": "PRIVATE_MATERIAL", "src/a.ts": "x"})
        try:
            try_symlink(self, os.path.join(root, "real.txt"), os.path.join(root, "src", "schema.ts"))
            repo = fs.Repo("r", root)
            self.assertNotIn("src/schema.ts", repo.files)
            self.assertEqual(repo.read("src/schema.ts"), "")
        finally:
            shutil.rmtree(root)

    def test_symlinked_directory_is_not_walked(self):
        outside = write_project({"secret.ts": "export const s = 'PRIVATE_MATERIAL';"})
        root = write_project({"src/a.ts": "x"})
        try:
            try_symlink(self, outside, os.path.join(root, "src", "linked"))
            self.assertEqual(fs.Repo("r", root).files, ["src/a.ts"])
        finally:
            shutil.rmtree(root)
            shutil.rmtree(outside)

    def test_out_dir_is_excluded(self):
        root = write_project({"src/a.ts": "x", "docs/system/api.md": "# x"})
        try:
            model = analyze([root], os.path.join(root, "docs", "system"))
            self.assertEqual(model["repositories"][0]["files"], 1)
        finally:
            shutil.rmtree(root)

    def test_large_file_is_skipped(self):
        root = write_project({"src/big.ts": "a" * (fs.MAX_FILE_BYTES + 10)})
        try:
            repo = fs.Repo("r", root)
            self.assertEqual(repo.read("src/big.ts"), "")
            self.assertEqual(repo.skipped_large, ["src/big.ts"])
        finally:
            shutil.rmtree(root)


class SecretScanTest(unittest.TestCase):
    def labels(self, text):
        return {f["label"] for f in scan(text, [])}

    def test_tokens(self):
        self.assertIn("GitHub トークン", self.labels("token: ghp_" + "a" * 36))
        self.assertIn("AWS アクセスキー", self.labels("AKIA" + "A" * 16))
        self.assertIn("Anthropic API キー", self.labels("sk-ant-api03-" + "x" * 40))
        self.assertIn("Stripe 本番キー", self.labels("sk_live_" + "a" * 24))
        self.assertIn("秘密鍵ブロック", self.labels("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertIn("DB 接続文字列", self.labels("postgres://user:pass@db.example.com/x"))

    def test_infra_and_pii(self):
        self.assertIn("私有 IP アドレス", self.labels("host 192.168.1.10"))
        self.assertIn("内部ホスト名", self.labels("api.internal"))
        self.assertIn("メールアドレス", self.labels("contact taro@example.co.jp"))

    def test_variable_names_are_not_flagged(self):
        self.assertEqual(scan("環境変数 `STRIPE_SECRET_KEY` で渡す。client_secret: CLIENT_SECRET", []), [])

    def test_mask_hides_value(self):
        masked = mask("ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        self.assertTrue(masked.startswith("ghp_"))
        self.assertNotIn("abcdefghij", masked)

    def test_long_line_is_bounded(self):
        """1 行が極端に長くても、照合が終わる。"""
        scan("a" * 200000 + "@", [])

    def test_blocking_ignores_dummy(self):
        found = scan("key sk_live_" + "a" * 24 + " and user@example.com", [])
        self.assertEqual([f["label"] for f in blocking(found)], ["Stripe 本番キー"])


class RenderSecretGateTest(unittest.TestCase):
    def test_credential_in_annotations_blocks_render(self):
        """注釈に認証情報があれば、資料を書き出さずに止める。"""
        import sys
        from io import StringIO
        from helpers import CLI  # noqa: F401
        sys.path.insert(0, os.path.dirname(CLI))
        import source_docs
        docs = ShopDocs()
        try:
            data = docs.annotations()
            data["entities"]["integration:stripe"]["auth"] = "sk_live_" + "b" * 24
            docs.write_annotations(data)
            old = sys.stdout
            sys.stdout = StringIO()
            try:
                code = source_docs.main(["render", "--out", docs.out])
            finally:
                sys.stdout = old
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(os.path.join(docs.out, "integrations.md")))
        finally:
            docs.cleanup()

    def test_generated_docs_have_no_dotenv_value(self):
        docs = ShopDocs()
        try:
            for path in docs.render():
                with open(path, encoding="utf-8") as f:
                    self.assertNotIn("FIXTURE_SECRET_VALUE", f.read())
        finally:
            docs.cleanup()


class ModelHasNoSecretsTest(unittest.TestCase):
    def test_env_names_but_no_values(self):
        m = analyze_files({
            "package.json": json.dumps({"dependencies": {"next": "15", "stripe": "17"}}),
            ".env": "STRIPE_SECRET_KEY=VALUE_SHOULD_NOT_APPEAR",
            ".env.example": "STRIPE_SECRET_KEY=EXAMPLE_VALUE_SHOULD_NOT_APPEAR",
            "app/api/pay/route.ts": "import Stripe from 'stripe';\n"
                                    "const s = new Stripe(process.env.STRIPE_SECRET_KEY);\n"
                                    "export async function POST() { await s.charges.create({}); }\n",
        })
        text = json.dumps(m)
        self.assertIn("STRIPE_SECRET_KEY", text)
        self.assertNotIn("VALUE_SHOULD_NOT_APPEAR", text)


if __name__ == "__main__":
    unittest.main()


class SpecSecretListTest(unittest.TestCase):
    """社内公開前の依頼で挙がった、開いてはいけないファイルの一覧。

    それぞれに固有の値（カナリア）を置き、解析・描画（Markdown / JSON / HTML）のどこにも出ないことを確かめる。
    """

    FILES = (".env", ".env.local", ".env.production", "server.pem", "tls.key", "store.p12", "release.jks",
             "id_rsa", "credentials.json", "service-account.json", ".npmrc", ".netrc", ".git-credentials",
             ".pgpass", "prod.tfvars", ".ssh/config", ".aws/credentials", ".gnupg/pubring.kbx",
             ".kube/config", "credentials/google.json", "secrets/app.ts", "config/secrets/db.ts")

    def test_nothing_from_secret_files_reaches_any_output(self):
        from sdocs.renderers import render_docs, write_evidence
        from sdocs.renderers.html import write_site
        files = {"package.json": json.dumps({"dependencies": {"next": "15"}}),
                 "app/page.tsx": "export default function P() { return null }\n",
                 ".env.example": "EXAMPLE_ONLY_KEY=CANARY_EXAMPLE_VALUE\n"}
        for i, name in enumerate(self.FILES):
            files[name] = f'export const leaked = "CANARY_{i}";\nexport async function GET() {{}}\n'
        root = write_project(files)
        out = tempfile.mkdtemp()
        try:
            repo = fs.Repo("x", root)
            listed = set(repo.files)
            for name in self.FILES:
                self.assertNotIn(name, listed, name)
            model = analyze([root], out)
            written = render_docs(resolve(model, {}), out) + [write_evidence(resolve(model, {}), out)]
            written += write_site(out, os.path.join(out, "html"))
            texts = [json.dumps(model)]
            for p in written:
                with open(p, encoding="utf-8") as f:
                    texts.append(f.read())
            blob = "\n".join(texts)
            self.assertNotIn("CANARY_", blob)
        finally:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(out, ignore_errors=True)

    def test_scanner_covers_markdown_json_and_html(self):
        from sdocs.validators import secrets as sec
        d = tempfile.mkdtemp()
        try:
            for name in ("a.md", "b.json", "c.html"):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write("token: ghp_" + "a1B2" * 9 + "\n")
            found = sec.scan_paths([d])
            self.assertEqual(sorted(os.path.basename(p) for p in found), ["a.md", "b.json", "c.html"])
        finally:
            shutil.rmtree(d)
