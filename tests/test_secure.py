"""読み込み禁止ファイルの判定テスト。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "source-docs", "scripts"))
import _secure  # noqa: E402


class IsSecretFileTest(unittest.TestCase):
    def test_env_files_are_blocked(self):
        for name in (".env", ".env.local", ".env.production", ".env.neon"):
            self.assertTrue(_secure.is_secret_file(name), name)

    def test_env_example_is_allowed(self):
        """変数名を知るために必要。値は読まない（SKILL.md の規則）。"""
        for name in (".env.example", ".env.sample", ".env.template"):
            self.assertFalse(_secure.is_secret_file(name), name)

    def test_key_material_is_blocked(self):
        for name in ("server.pem", "private.key", "cert.p12", "store.jks", "id_rsa"):
            self.assertTrue(_secure.is_secret_file(name), name)

    def test_named_credential_files_are_blocked(self):
        for name in ("credentials.json", "service-account.json", "secrets.yaml",
                     ".npmrc", ".netrc", "terraform.tfvars", "master.key"):
            self.assertTrue(_secure.is_secret_file(name), name)

    def test_ordinary_source_is_allowed(self):
        for name in ("schema.ts", "orders.py", "package.json", "secrets.py",
                     "config.ts", "keyboard.tsx"):
            self.assertFalse(_secure.is_secret_file(name), name)

    def test_path_form_is_accepted(self):
        """パスを渡してもベース名で判定する。"""
        self.assertTrue(_secure.is_secret_file("backend/config/.env.production"))
        self.assertFalse(_secure.is_secret_file("backend/src/schema.ts"))

    def test_credential_directory_is_blocked(self):
        """credentials/ 配下は丸ごと読まない。"""
        self.assertTrue(_secure.is_secret_path("credentials/google.json"))
        self.assertTrue(_secure.is_secret_path("app/.ssh/known_hosts"))
        self.assertFalse(_secure.is_secret_path("src/credentials.test.ts"))


class SymlinkTest(unittest.TestCase):
    def test_symlinked_file_is_treated_as_secret(self):
        """リンク先が何であれ、シンボリックリンクは開かない。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "real.txt")
            with open(target, "w", encoding="utf-8") as f:
                f.write("x")
            link = os.path.join(d, "schema.ts")
            os.symlink(target, link)
            self.assertTrue(_secure.is_unsafe_to_open(link))
            self.assertFalse(_secure.is_unsafe_to_open(target))
