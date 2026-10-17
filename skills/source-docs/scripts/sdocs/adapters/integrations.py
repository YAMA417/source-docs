"""外部連携の検出。SDK の import・外部ホストへの HTTP 呼び出し・環境変数名を拾う。

対応表はデータとして持つ。新しい SDK は `SDKS` に 1 行足せばよい。
環境変数は**名前だけ**を拾う。値は読まない（`.env` 自体を開かない）。
"""

import ipaddress
import re

from .base import Adapter, strip_comments
from .lang_js import is_js, package_name, parse_imports

# パッケージ名（接頭辞一致）→ (キー, 表示名)
SDKS = [
    ("stripe", "stripe", "Stripe"),
    ("@stripe/", "stripe", "Stripe"),
    ("stripe-go", "stripe", "Stripe"),
    ("@sendgrid/", "sendgrid", "SendGrid"),
    ("sendgrid", "sendgrid", "SendGrid"),
    ("twilio", "twilio", "Twilio"),
    ("openai", "openai", "OpenAI"),
    ("@anthropic-ai/sdk", "anthropic", "Anthropic"),
    ("anthropic", "anthropic", "Anthropic"),
    ("@aws-sdk/client-s3", "aws-s3", "AWS S3"),
    ("@aws-sdk/client-sqs", "aws-sqs", "AWS SQS"),
    ("@aws-sdk/client-ses", "aws-ses", "AWS SES"),
    ("@aws-sdk/client-sns", "aws-sns", "AWS SNS"),
    ("@aws-sdk/", "aws", "AWS"),
    ("aws-sdk", "aws", "AWS"),
    ("boto3", "aws", "AWS"),
    ("@google-cloud/storage", "gcs", "Google Cloud Storage"),
    ("@google-cloud/pubsub", "gcp-pubsub", "Google Cloud Pub/Sub"),
    ("googleapis", "google-apis", "Google APIs"),
    ("firebase-admin", "firebase", "Firebase"),
    ("@slack/", "slack", "Slack"),
    ("slack_sdk", "slack", "Slack"),
    ("resend", "resend", "Resend"),
    ("nodemailer", "smtp", "SMTP（メール送信）"),
    ("@sentry/", "sentry", "Sentry"),
    ("sentry_sdk", "sentry", "Sentry"),
    ("@shopify/", "shopify", "Shopify"),
    ("shopify-api-node", "shopify", "Shopify"),
    ("algoliasearch", "algolia", "Algolia"),
    ("@line/bot-sdk", "line", "LINE"),
    ("@octokit/", "github", "GitHub"),
    ("octokit", "github", "GitHub"),
    ("@notionhq/client", "notion", "Notion"),
    ("@vercel/blob", "vercel-blob", "Vercel Blob"),
    ("@upstash/redis", "upstash", "Upstash"),
    ("pusher", "pusher", "Pusher"),
    ("@onesignal/", "onesignal", "OneSignal"),
    ("expo-server-sdk", "expo-push", "Expo Push"),
]

RE_URL = re.compile(r"""['"`](https?://([A-Za-z0-9.\-]+)(?::\d+)?(/[^'"`\s]*)?)['"`]""")
RE_HTTP_CALL_NEAR = re.compile(r"\b(fetch|axios|got|ky|request|http|requests|httpx|Get|Post)\b")

RE_ENV = [
    re.compile(r"\bprocess\.env\.([A-Z][A-Z0-9_]*)"),
    re.compile(r"""\bprocess\.env\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]"""),
    re.compile(r"""\bDeno\.env\.get\(\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\)"""),
    re.compile(r"""\bimport\.meta\.env\.([A-Z][A-Z0-9_]*)"""),
    re.compile(r"""\bos\.environ(?:\.get\(|\[)\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
    re.compile(r"""\bos\.getenv\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
    re.compile(r"""\bos\.Getenv\(\s*"([A-Z][A-Z0-9_]*)"\s*\)"""),
    re.compile(r"""\bENV\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]"""),
    re.compile(r"""\benv\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
]

RE_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
RE_GO_IMPORT = re.compile(r'"((?:github\.com|gopkg\.in|cloud\.google\.com)/[^"]+)"')

# 資料に載せてはいけないホスト。ここで落とし、存在だけを unsupported に残す
INTERNAL_HOST_SUFFIX = (".internal", ".local", ".corp", ".intranet", ".lan", "localhost")


def sdk_for(package):
    for prefix, key, name in SDKS:
        if package == prefix or (prefix.endswith("/") and package.startswith(prefix)) \
                or package.startswith(prefix + "/") or package.startswith(prefix + "."):
            return key, name
    return None


def imported_sdks(rel, text):
    """[(キー, 表示名, パッケージ, ローカル名の列)]。"""
    found = []
    if is_js(rel):
        for names, spec in parse_imports(text):
            pkg = package_name(spec)
            hit = sdk_for(pkg) if pkg else None
            if hit:
                found.append((hit[0], hit[1], pkg, names))
    elif rel.endswith(".py"):
        for m in RE_PY_IMPORT.finditer(text):
            pkg = (m.group(1) or m.group(2)).split(".")[0]
            hit = sdk_for(pkg)
            if hit:
                found.append((hit[0], hit[1], pkg, [pkg]))
    elif rel.endswith(".go"):
        for m in RE_GO_IMPORT.finditer(text):
            pkg = m.group(1).split("/")[2] if m.group(1).count("/") >= 2 else m.group(1)
            hit = sdk_for(pkg)
            if hit:
                found.append((hit[0], hit[1], m.group(1), []))
    return found


def is_internal_host(host):
    host = host.lower()
    if host.endswith(INTERNAL_HOST_SUFFIX) or host in ("localhost", "0.0.0.0"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def host_key(host):
    return "host:" + host.lower()


class Integrations(Adapter):
    name = "integrations"

    def applies(self, app):
        return True

    def collect(self, app, b):
        for rel in app.files:
            text = app.read(rel)
            if not text:
                continue
            for key, name, pkg, _ in imported_sdks(rel, text):
                b.add_integration(key, name, app.ev(rel, rule="integration.sdk"), sdk=pkg)
            # どの入口からも辿れない呼び出しも、連携先の一覧には載せる
            for u in self.usages(app, rel, text):
                if u["kind"] == "integration" and u.get("host"):
                    b.add_integration(u["key"], u["name"],
                                      app.ev(rel, text.count("\n", 0, u["offset"]) + 1,
                                             "integration.http"),
                                      protocol=u.get("protocol"), host=u["host"])

    def usages(self, app, rel, text):
        out = []
        code = strip_comments(text) if is_js(rel) else text
        sdks = imported_sdks(rel, text)
        for key, name, pkg, names in sdks:
            # import しただけでなく、その名前（かそこから作ったクライアント）を使っているか
            used = not names or any(re.search(r"\b" + re.escape(n) + r"\b", _without_imports(code))
                                    for n in names)
            if used:
                out.append({"kind": "integration", "key": key, "name": name, "sdk": pkg,
                            "offset": 0})
        for m in RE_URL.finditer(code):
            host = m.group(2)
            near = code[max(0, m.start() - 80):m.start()]
            if not RE_HTTP_CALL_NEAR.search(near):
                continue
            if is_internal_host(host):
                out.append({"kind": "internal-host", "offset": m.start()})
                continue
            out.append({"kind": "integration", "key": host_key(host), "name": host,
                        "host": host, "protocol": "https" if m.group(1).startswith("https") else "http",
                        "offset": m.start()})
        for rx in RE_ENV:
            for m in rx.finditer(code):
                out.append({"kind": "env", "name": m.group(1), "offset": m.start()})
        return out


def _without_imports(text):
    return re.sub(r"^\s*(import|from)\b[^\n]*$", "", text, flags=re.M)


def env_example_names(app):
    """`.env.example` などに並ぶ変数名。値は取り出さない。"""
    from ..security import ENV_SAFE, env_var_names
    names = {}
    for rel in app.files:
        if rel.rsplit("/", 1)[-1].lower() in ENV_SAFE:
            for n in env_var_names(app.read(rel)):
                names.setdefault(n, rel)
    return names
