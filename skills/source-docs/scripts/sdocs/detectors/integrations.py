"""外部連携の検出。SDK の import・外部ホストへの HTTP 呼び出し・環境変数名を拾う。

対応表はデータとして持つ。新しい SDK は `SDKS` に 1 行足せばよい。
環境変数は**名前だけ**を拾う。値は読まない（`.env` 自体を開かない）。

環境変数と連携先の結び付けは、文字列の近さでは決めない。次の順に追う（precedence の `integration.env`）。

    環境変数 → クライアント・プロバイダの初期化の引数 → SDK → 連携先     （client-init: confirmed）
    環境変数 → 作ったクライアントの呼び出しの引数                          （call-site:   confirmed）
    環境変数 → 変数に入れてから初期化の引数に渡す                          （init-variable: inferred）
    同じファイルで使っているだけ                                            （same-file:   unresolved）

例: `GitHubProvider({ clientId: env.GITHUB_CLIENT_ID })` → GitHub（OAuth）に GITHUB_CLIENT_ID（confirmed）。
同じファイルの `EmailProvider({ from: env.SMTP_FROM })` は EmailProvider の引数なので GitHub には付かない。
"""

import ipaddress
import re

from .base import Adapter, line_of, match_brace, strip_comments
from .javascript import JsLanguage, is_js, package_name, parse_imports

# パッケージ名（接頭辞一致）→ (キー, 表示名)
SDKS = [
    # 下位パスまで見る対応。認証ライブラリの OAuth プロバイダなど、パッケージ名だけでは連携先が決まらないもの
    ("next-auth/providers/github", "github-oauth", "GitHub（OAuth）"),
    ("next-auth/providers/google", "google-oauth", "Google（OAuth）"),
    ("next-auth/providers/discord", "discord-oauth", "Discord（OAuth）"),
    ("next-auth/providers/twitter", "twitter-oauth", "X（OAuth）"),
    ("next-auth/providers/email", "email-login", "メールでのログイン（NextAuth）"),
    ("@auth/core/providers/github", "github-oauth", "GitHub（OAuth）"),
    ("@auth/core/providers/google", "google-oauth", "Google（OAuth）"),
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
    ("postmark", "postmark", "Postmark"),
    ("@clerk/", "clerk", "Clerk"),
    ("@auth0/", "auth0", "Auth0"),
    ("mailgun.js", "mailgun", "Mailgun"),
    ("@mailchimp/", "mailchimp", "Mailchimp"),
    ("@paypal/", "paypal", "PayPal"),
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
    # t3-env / zod で検証した env オブジェクト（`env.STRIPE_API_KEY`）
    re.compile(r"(?<![\w.])env\.([A-Z][A-Z0-9_]*)\b"),
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
            if not pkg:
                continue
            hit = sdk_for(spec) or sdk_for(pkg)
            if hit:
                found.append((hit[0], hit[1], spec if sdk_for(spec) and spec != pkg else pkg, names))
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


# 例示用のドメイン（RFC 2606）。実在の連携先ではない（.test は再現用の連携先に使うので外さない）
EXAMPLE_HOST_SUFFIX = (".example", ".invalid", ".localhost")
EXAMPLE_HOSTS = ("example.com", "example.net", "example.org")
RE_TEST_PATH = re.compile(r"(^|/)(__tests__|__mocks__|tests?|e2e|fixtures|mocks?)/|\.(test|spec|stories)\.[cm]?[jt]sx?$|(^|/)test_[^/]*\.py$")


def is_example_host(host):
    host = host.lower()
    return host.endswith(EXAMPLE_HOST_SUFFIX) or any(host == h or host.endswith("." + h) for h in EXAMPLE_HOSTS)


def is_test_file(rel):
    return bool(RE_TEST_PATH.search(rel))


def host_key(host):
    return "host:" + host.lower()


class Integrations(Adapter):
    name = "integrations"

    def applies(self, app):
        return True

    def collect(self, app, b):
        js = JsLanguage()
        names_by_file, hosts_by_file = {}, {}
        # 1. ファイルごとに、SDK の import と、そこから作ったクライアントの変数を集める
        for rel in app.files:
            text = app.read(rel)
            if not text:
                continue
            names = {}
            for key, name, pkg, local in imported_sdks(rel, text):
                b.add_integration(key, name, app.ev(rel, rule="integration.sdk"), sdk=pkg)
                for n in local:
                    names[n] = (key, "client-init")
                for v in _client_vars(text, local):
                    names[v] = (key, "call-site")
            names_by_file[rel] = names
            # どの入口からも辿れない呼び出しも、連携先の一覧には載せる
            code = strip_comments(text) if is_js(rel) else text
            for u in self.usages(app, rel, text):
                if u["kind"] == "integration" and u.get("host"):
                    b.add_integration(u["key"], u["name"],
                                      app.ev(rel, text.count("\n", 0, u["offset"]) + 1, "integration.http"),
                                      protocol=u.get("protocol"), host=u["host"])
                    span = _http_call_span(code, u["offset"])
                    if span:
                        hosts_by_file.setdefault(rel, []).append(span + (u["key"], "call-site"))
        # 2. 別のファイルで作ったクライアントを import して使っている場合（`import { stripe } from "@/lib/stripe"`）
        for rel, names in names_by_file.items():
            if not is_js(rel):
                continue
            text = app.read(rel)
            aliases = js.aliases(app, rel, text)
            for local, target in js.imports(app, rel, text).items():
                found = names_by_file.get(target, {}).get(aliases.get(local, local))
                if found and found[1] == "call-site":
                    names.setdefault(local, found)
        # 3. 初期化・呼び出しの引数に出てくる環境変数を結び付ける
        for rel in sorted(set(names_by_file) | set(hosts_by_file)):
            if names_by_file.get(rel) or hosts_by_file.get(rel):
                self._trace_env(app, b, rel, names_by_file.get(rel, {}), hosts_by_file.get(rel, []))

    def _trace_env(self, app, b, rel, names, host_spans):
        """環境変数を、それが置かれた**いちばん内側の**初期化・呼び出しの引数の連携先に結び付ける。

        `EmailProvider({ send: () => postmark.sendEmail({ TemplateId: env.X }) })` の env.X は、
        外側の EmailProvider ではなく内側の Postmark の呼び出しのもの。
        変数に入れてから渡す（`const id = env.X; postmark.send({ id })`）場合は、変数を使った呼び出しのもの（inferred）。
        """
        code = strip_comments(app.read(rel)) if is_js(rel) else app.read(rel)
        spans = list(host_spans)
        for local in sorted(names):
            key, source = names[local]
            rx = re.compile(r"(?<![\w$.])(?:new\s+)?" + re.escape(local) + r"(?:\s*\.\s*[\w$]+)*\s*\(")
            for m in rx.finditer(code):
                close = match_brace(code, m.end() - 1, "(", ")")
                spans.append((m.start(), close if close != -1 else min(len(code), m.end() + 2000), key, source))
        # 変数への代入（クライアントそのものを入れる変数は除く。それは上の call-site で扱う）
        assigns = []
        for m in RE_ASSIGN.finditer(code):
            if m.group(1) in names:
                continue
            refs = [(n, m.start(2) + pos) for n, pos in _env_occurrences(m.group(2))]
            if refs:
                assigns.append((m.group(1), m.start(), m.end(), refs))

        def innermost(pos):
            inside = [sp for sp in spans if sp[0] <= pos < sp[1]]
            return min(inside, key=lambda sp: sp[1] - sp[0]) if inside else None

        def var_targets(var, after):
            """変数を引数に使っている呼び出しのうち、いちばん内側のもの。"""
            out = []
            for m in re.finditer(r"(?<![\w$.])" + re.escape(var) + r"\b", code[after:]):
                span = innermost(after + m.start())
                if span and span not in out:
                    out.append(span)
            return out

        used = set()
        for env, pos in _env_occurrences(code):
            outer = innermost(pos)
            assign = next((a for a in assigns if a[1] <= pos < a[2]), None)
            targets = var_targets(assign[0], assign[2]) if assign else []
            deeper = [t for t in targets
                      if outer is None or (outer[0] <= t[0] and t[1] <= outer[1] and t != outer)]
            if deeper:
                for start, _, key, _ in deeper:
                    b.add_env(b.get(f"integrations||{key}"), env, "init-variable", "inferred",
                              app.ev(rel, line_of(code, start), "env.init-variable"))
                used.add(env)
            elif outer:
                start, _, key, source = outer
                b.add_env(b.get(f"integrations||{key}"), env, source, "confirmed",
                          app.ev(rel, line_of(code, start), f"env.{source}"))
                used.add(env)
            elif targets:
                for start, _, key, _ in targets:
                    b.add_env(b.get(f"integrations||{key}"), env, "init-variable", "inferred",
                              app.ev(rel, line_of(code, start), "env.init-variable"))
                used.add(env)
        # 引数で使っていない環境変数は確定させない。同じ関数の中にあるもの（same-scope）は、その関数で呼ぶ
        # 連携先の候補に、それ以外（same-file）はファイル内の全連携先の候補にする。どちらも unresolved
        from ..core.relations import definitions
        scopes = sorted(definitions(code).values())
        keys = sorted({k for k, _ in names.values()} | {sp[2] for sp in host_spans})
        for env, offset in _env_refs(code):
            if env in used:
                continue
            scope = next(((st, en) for st, en in scopes if st <= offset < en), None)
            near = sorted({sp[2] for sp in spans if scope and scope[0] <= sp[0] < scope[1]})
            if near:
                for key in near:
                    b.add_env(b.get(f"integrations||{key}"), env, "same-scope", "unresolved",
                              app.ev(rel, line_of(code, offset), "env.same-scope"),
                              reason="連携先を呼ぶ関数の中で使われているが、初期化・呼び出しの引数ではない")
                continue
            for key in keys:
                b.add_env(b.get(f"integrations||{key}"), env, "same-file", "unresolved",
                          app.ev(rel, line_of(code, offset), "env.same-file"),
                          reason="同じファイルで使われているが、初期化・呼び出しの引数ではない")

    def usages(self, app, rel, text):
        out = []
        code = strip_comments(text) if is_js(rel) else text
        full = app.read(rel) if app is not None else text
        sdks = imported_sdks(rel, full or text)
        for key, name, pkg, names in sdks:
            # import しただけでなく、その名前（かそこから作ったクライアント）を使っているか
            names = names + _client_vars(full or text, names)
            used = not names or any(re.search(r"\b" + re.escape(n) + r"\b", _without_imports(code))
                                    for n in names)
            if used:
                out.append({"kind": "integration", "key": key, "name": name, "sdk": pkg,
                            "offset": 0})
        # テストのコードに書いた URL は、システムの連携先ではない
        for m in ([] if is_test_file(rel) else RE_URL.finditer(code)):
            host = m.group(2)
            if is_example_host(host):
                continue
            near = code[max(0, m.start() - 80):m.start()]
            if not RE_HTTP_CALL_NEAR.search(near):
                continue
            if is_internal_host(host):
                out.append({"kind": "internal-host", "offset": m.start()})
                continue
            out.append({"kind": "integration", "key": host_key(host), "name": host,
                        "host": host, "protocol": "https" if m.group(1).startswith("https") else "http",
                        "offset": m.start()})
        return out


# `const x = ...` の右辺。次の行が `?` `:` `.` `&&` `||` `+` で始まる間は式が続くとみなす
RE_ASSIGN = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*((?:[^;\n]|\n\s*(?:[?:.+]|&&|\|\|))+)")


def _http_call_span(code, url_offset):
    """URL を引数に持つ HTTP 呼び出し（`fetch("https://…", {…})`）の範囲。"""
    base = max(0, url_offset - 80)
    calls = list(re.finditer(r"\b(?:fetch|axios(?:\.\w+)?|got|ky|request|requests\.\w+|httpx\.\w+)\s*\(",
                             code[base:url_offset]))
    if not calls:
        return None
    open_paren = base + calls[-1].end() - 1
    close = match_brace(code, open_paren, "(", ")")
    return (open_paren, close) if close != -1 else None


def _env_occurrences(text):
    """[(環境変数名, 位置)]。同じ名前も、出てくるたびに返す。"""
    out = set()
    for rx in RE_ENV:
        out |= {(m.group(1), m.start()) for m in rx.finditer(text)}
    return sorted(out, key=lambda x: x[1])


def _env_refs(text):
    """[(環境変数名, 位置)]。同じ名前は最初の 1 つだけ。"""
    out, seen = [], set()
    for rx in RE_ENV:
        for m in rx.finditer(text):
            if m.group(1) not in seen:
                seen.add(m.group(1))
                out.append((m.group(1), m.start()))
    return sorted(out, key=lambda x: x[1])


def _client_vars(text, names):
    """import した名前から作った変数（`const stripe = new Stripe(...)`）の名前。"""
    if not names:
        return []
    alt = "|".join(re.escape(n) for n in names)
    return re.findall(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:new\s+)?(?:" + alt + r")\b", text)


def _without_imports(text):
    return re.sub(r"^\s*(import|from)\b[^\n]*$", "", text, flags=re.M)


def env_example_names(app):
    """`.env.example` などに並ぶ変数名。値は取り出さない。"""
    from ..core.security import ENV_SAFE, env_var_names
    names = {}
    for rel in app.files:
        if rel.rsplit("/", 1)[-1].lower() in ENV_SAFE:
            for n in env_var_names(app.read(rel)):
                names.setdefault(n, rel)
    return names
