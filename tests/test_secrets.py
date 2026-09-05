"""機密検出のテスト。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "source-docs", "scripts"))
import secrets as secrets_mod  # noqa: E402


def kinds(doc):
    return {f["label"] for f in secrets_mod.scan(doc, [])}


class TokenPatternTest(unittest.TestCase):
    def test_github_pat(self):
        self.assertIn("GitHub トークン", kinds("token: ghp_" + "a" * 36))

    def test_github_fine_grained(self):
        self.assertIn("GitHub トークン", kinds("github_pat_" + "A" * 22 + "_" + "b" * 59))

    def test_slack_token(self):
        # 秘密走査に引っかからないよう、テスト値は実行時に組み立てる。
        # リテラルで書くと push protection が本物のトークンとみなす
        value = "xoxb" + "-" + "1" * 12 + "-" + "2" * 12 + "-" + "a" * 24
        self.assertIn("Slack トークン", kinds(value))

    def test_stripe_live_key(self):
        self.assertIn("Stripe 本番キー", kinds("sk_live_" + "a" * 24))

    def test_anthropic_key(self):
        self.assertIn("Anthropic API キー", kinds("sk-ant-api03-" + "x" * 40))

    def test_openai_key(self):
        self.assertIn("OpenAI API キー", kinds("sk-proj-" + "x" * 40))

    def test_google_api_key(self):
        self.assertIn("Google API キー", kinds("AIza" + "B" * 35))

    def test_ssh_private_key_body(self):
        self.assertIn("秘密鍵ブロック", kinds("-----BEGIN OPENSSH PRIVATE KEY-----"))

    def test_private_ip(self):
        self.assertIn("私有 IP アドレス", kinds("接続先は 10.0.13.42 です"))

    def test_internal_hostname(self):
        self.assertIn("内部ホスト名", kinds("db.internal.example.corp に接続"))


class NoFalsePositiveTest(unittest.TestCase):
    def test_plain_prose_is_clean(self):
        doc = """
# API 仕様

パスワードリセットのエンドポイント。認証は Bearer トークンで行う。
実装ファイルは `src/routes/auth.ts`。
"""
        self.assertEqual(secrets_mod.scan(doc, []), [])

    def test_env_var_name_only_is_clean(self):
        """環境変数の名前だけを書くのは正しい書き方。検出しない。"""
        doc = "| 決済サービス | `PAYMENT_API_KEY` | `src/config/env.ts` |"
        self.assertEqual(secrets_mod.scan(doc, []), [])

    def test_version_like_number_is_not_an_ip(self):
        self.assertNotIn("私有 IP アドレス", kinds("バージョン 10.0.1 を使う"))


class MaskTest(unittest.TestCase):
    def test_long_value_is_masked(self):
        value = "ghp_" + "abcdefghijklmnopqrstuvwxyz" + "0123456789"
        masked = secrets_mod.mask(value)
        self.assertNotIn("abcdefghijkl", masked)
        self.assertTrue(masked.startswith("ghp_"))

    def test_short_value_is_masked(self):
        self.assertNotIn("abc", secrets_mod.mask("abcdef"))

    def test_empty_is_safe(self):
        self.assertEqual(secrets_mod.mask(""), "")


class RedosTest(unittest.TestCase):
    def test_hostname_pattern_is_linear_enough(self):
        """病的な入力でも実用的な時間で終わる。"""
        import time
        evil = "a." * 6000 + "invalid"
        start = time.time()
        secrets_mod.scan(evil, [])
        self.assertLess(time.time() - start, 0.15)


class PlaceholderTest(unittest.TestCase):
    def test_uppercase_placeholder_is_not_a_secret(self):
        for doc in ("client_secret: CLIENT_SECRET",
                    "token = YOUR_ACCESS_TOKEN",
                    "api_key: <YOUR_API_KEY>",
                    "password = CHANGE_ME_PLEASE"):
            self.assertEqual(secrets_mod.scan(doc, []), [], doc)

    def test_real_looking_value_is_still_detected(self):
        doc = "api_key = " + "a8Kd93jfLp0Qz" + "xY7v"
        self.assertNotEqual(secrets_mod.scan(doc, []), [])


class AdditionalPatternTest(unittest.TestCase):
    def test_basic_auth_header(self):
        doc = "Authorization: Basic " + "dXNlcjpwYXNzd29yZDEyMzQ1Ng=="
        self.assertIn("Basic 認証ヘッダー", {f["label"] for f in secrets_mod.scan(doc, [])})

    def test_pgp_private_key(self):
        doc = "-----BEGIN PGP PRIVATE KEY BLOCK-----"
        self.assertIn("秘密鍵ブロック", {f["label"] for f in secrets_mod.scan(doc, [])})


class InputLimitTest(unittest.TestCase):
    def test_reports_truncation_for_huge_input(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "big.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("x" * (secrets_mod.MAX_DOC_BYTES + 10))
            text, truncated = secrets_mod.read_doc(path)
            self.assertTrue(truncated)
            self.assertLessEqual(len(text.encode("utf-8")), secrets_mod.MAX_DOC_BYTES)
