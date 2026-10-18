"""資料に載せてはいけない情報が混ざっていないかを調べる。

検出対象は 4 種類。
  - 認証情報・鍵（クラウドサービスのトークン / API キー / 秘密鍵 / DB 接続文字列）
  - 個人情報（実データのメールアドレス・電話番号）
  - 内部の構成情報（私有 IP・内部ホスト名）
  - 取引先名・契約情報（案件ごとの語彙リストが与えられた場合のみ）

検出するのは**値そのものの形**だけ。環境変数の名前を書くのは正しい書き方なので、
名前には反応しない。

**検出しても自動で伏せ字にしない。** 誤検知が出るので、判断は人間に回す。
"""

import os
import re

# (種別, ラベル, 正規表現, 必須文字列)
#
# 追加するときは「値そのものの形」を書く。変数名だけを検出しないこと。
# 資料には環境変数の名前を書くのが正しい書き方なので、名前で反応すると
# 正しい資料が毎回引っかかる。
#
# 必須文字列は、その文字列を含まない行を照合前に弾くためのもの。
# `[A-Za-z0-9._%+-]+@` のようなパターンは `@` の無い長い行に対して
# 全位置からバックトラックし、二乗時間になる。先に弾く。
PATTERNS = [
    # --- クラウド・サービスのトークン。形が決まっているので確度が高い ---
    ("credential", "AWS アクセスキー",
     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "IA"),
    ("credential", "GitHub トークン", re.compile(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"
        r"|\bgithub_pat_[A-Za-z0-9_]{50,}\b"
    ), "_"),
    ("credential", "Slack トークン",
     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "xox"),
    ("credential", "Stripe 本番キー",
     re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"), "_live_"),
    ("credential", "Anthropic API キー",
     re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "sk-ant-"),
    ("credential", "OpenAI API キー",
     re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}"), "sk-"),
    ("credential", "Google API キー",
     re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "AIza"),
    ("credential", "SendGrid API キー",
     re.compile(r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"), "SG."),
    ("credential", "Twilio SID",
     re.compile(r"\bAC[0-9a-f]{32}\b"), "AC"),
    ("credential", "npm トークン",
     re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), "npm_"),

    # --- 鍵・トークンの一般形 ---
    ("credential", "秘密鍵ブロック", re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?|PGP PRIVATE KEY BLOCK)-----"
    ), "-----BEGIN"),
    ("credential", "Basic 認証ヘッダー",
     re.compile(r"\bBasic\s+[A-Za-z0-9+/]{16,}={0,2}"), "Basic"),
    ("credential", "Bearer トークン",
     re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}"), "Bearer"),
    ("credential", "JWT らしき文字列",
     re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."), "eyJ"),
    ("credential", "DB 接続文字列",
     re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:@/]+:[^\s@/]+@"), "://"),
    (
        "credential",
        "キー・シークレットへの値の代入",
        re.compile(
            r"\b(?:api[_-]?key|secret|password|passwd|token|client[_-]?secret|private[_-]?key)"
            r"\s*[:=]\s*[\"\']?[A-Za-z0-9\-_/+=]{12,}",
            re.IGNORECASE,
        ),
        "",
    ),

    # --- 個人情報 ---
    ("pii", "メールアドレス",
     re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "@"),
    ("pii", "電話番号（E.164）",
     re.compile(r"\+\d{1,3}\d{7,13}\b"), "+"),
    ("pii", "電話番号（国内表記）",
     re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b"), "-"),

    # --- 内部の構成情報。外へ出す資料には載せない ---
    ("infra", "私有 IP アドレス", re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    ), "."),
    # ネストした量指定子を避ける。ラベル 1 つ分だけを見る
    ("infra", "内部ホスト名", re.compile(
        r"\b[A-Za-z0-9][A-Za-z0-9\-]{0,62}\.(?:internal|local|corp|intranet|lan)\b"
    ), "."),
]

# 明らかな説明用ダミー。除外はここだけに留める。判断を機械に寄せすぎない。
DUMMY_HINTS = (
    "example.com", "example.org", "example.net", "@example",
    "xxx@", "user@example", "test@test",
)

# 代入の右辺がこれに当てはまるならプレースホルダとみなす。
# `client_secret: CLIENT_SECRET` `token = YOUR_ACCESS_TOKEN` のような
# 説明用の記述は資料に頻出するので、毎回引っかかると検出が信用されなくなる
RE_PLACEHOLDER_VALUE = re.compile(r"^[A-Z][A-Z0-9_]*$")

RE_ASSIGN_VALUE = re.compile(r"[:=]\s*[\"\'<]?([^\"\'<>\s]+)")

# 1 度に読む文書の上限。巨大なファイルを渡されてメモリを食い潰さないため
MAX_DOC_BYTES = 5_000_000

# 1 行あたりの照合上限
MAX_LINE_CHARS = 4_000


def read_doc(path):
    """検査対象を読む。上限を超えたら切り詰め、切り詰めたことを返す。"""
    with open(path, "rb") as f:
        raw = f.read(MAX_DOC_BYTES + 1)
    truncated = len(raw) > MAX_DOC_BYTES
    return raw[:MAX_DOC_BYTES].decode("utf-8", errors="ignore"), truncated


def _is_placeholder(value):
    """検出した文字列が説明用のプレースホルダかどうか。"""
    m = RE_ASSIGN_VALUE.search(value)
    if not m:
        return False
    rhs = m.group(1).strip("<>\"'")
    return bool(RE_PLACEHOLDER_VALUE.match(rhs))


def mask(value):
    """検出した値を、判断に必要な最小限だけ見せる形にする。

    既定でこれを使う。検出結果をターミナルやログにそのまま流すと、
    資料から機密を消しても別の場所に残る。
    """
    if not value:
        return value
    if len(value) <= 8:
        return value[0] + "*" * (len(value) - 1)
    return value[:4] + "*" * 6 + value[-2:]


def scan(doc, terms):
    findings = []
    lines = doc.splitlines()

    for lineno, line in enumerate(lines, 1):
        # 極端に長い 1 行は途中までしか照合しない。資料の 1 行がこの長さを
        # 超えることはなく、超える入力はバックトラックを狙ったものとみなす
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS]
        for kind, label, pattern, required in PATTERNS:
            if required and required not in line:
                continue
            for m in pattern.finditer(line):
                value = m.group(0)
                if label == "キー・シークレットへの値の代入" and _is_placeholder(value):
                    continue
                checked = value
                if label == "内部ホスト名":
                    # Strapi のハンドラ名（`session-auth.local`）はホスト名ではない
                    if re.search(r"[\"']?(?:operation|handler)[\"']?\s*:", line):
                        continue
                    # `strapi.internal.example.com` のように、続きまで見ると例示用のドメインのもの
                    checked = value + re.match(r"[A-Za-z0-9.\-]*", line[m.end():]).group(0)
                dummy = any(h in checked.lower() for h in DUMMY_HINTS)
                findings.append({
                    "kind": kind,
                    "label": label,
                    "line": lineno,
                    "value": value,
                    "looksDummy": dummy,
                    "context": line.strip()[:160],
                })

        for term in terms:
            if term and term in line:
                findings.append({
                    "kind": "business",
                    "label": "取引先名・契約情報の語彙",
                    "line": lineno,
                    "value": term,
                    "looksDummy": False,
                    "context": line.strip()[:160],
                })

    return findings


def scan_file(path, terms=()):
    doc, truncated = read_doc(path)
    return scan(doc, terms), truncated


def scan_paths(paths, terms=()):
    """ファイルかディレクトリの列を検査する。{パス: 検出の列}。"""
    out = {}
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(dirs)
                for name in sorted(files):
                    if name.endswith((".md", ".json", ".html")):
                        full = os.path.join(root, name)
                        found, _ = scan_file(full, terms)
                        if found:
                            out[full] = found
        elif os.path.isfile(p):
            found, _ = scan_file(p, terms)
            if found:
                out[p] = found
    return out


def blocking(findings):
    """書き出しを止めるべき検出。認証情報で、ダミーに見えないもの。"""
    return [f for f in findings if f["kind"] == "credential" and not f["looksDummy"]]


def masked(findings):
    return [{**f, "value": mask(f["value"]), "context": "（--reveal で表示）"} for f in findings]
