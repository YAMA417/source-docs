#!/usr/bin/env python3
"""設計書に載ってはいけない情報が混ざっていないか調べる。

検出対象は 4 種類。
  - 認証情報・鍵（クラウドサービスのトークン / API キー / 秘密鍵 / DB 接続文字列）
  - 個人情報（実データのメールアドレス・電話番号）
  - 内部の構成情報（私有 IP・内部ホスト名）
  - 取引先名・契約情報（案件ごとの語彙リストが与えられた場合のみ）

検出するのは**値そのものの形**だけ。環境変数の名前を書くのは正しい書き方なので、
名前には反応しない。

**検出しても自動で伏せ字にしない。** 仕様説明中の「パスワードリセット」や
テスト用ダミーメールを拾うため、誤検知かどうかの判断は人間に回す。

使い方:
    python3 secrets.py --doc <md> [--terms <語彙ファイル>] [--json]

終了コード:
    0  検出なし
    1  検出あり（人間の判断が必要）
    2  引数・入出力の誤り
"""

import argparse
import json
import os
import re
import sys

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
                dummy = any(h in value.lower() for h in DUMMY_HINTS)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True, help="検査する設計書 md")
    ap.add_argument(
        "--terms",
        help="取引先名・契約情報の語彙ファイル（1 行 1 語。案件ごとに用意する。無ければこの検査は行わない）",
    )
    ap.add_argument("--json", action="store_true", help="JSON で出力する")
    ap.add_argument(
        "--reveal",
        action="store_true",
        help="検出した値と前後の文脈をそのまま表示する。既定は伏せる（ログに残さないため）",
    )
    args = ap.parse_args()

    if not os.path.isfile(args.doc):
        raise SystemExit(f"error: 設計書が見つかりません: {args.doc}")

    doc, truncated = read_doc(args.doc)
    if truncated:
        print(
            f"warning: {MAX_DOC_BYTES // 1_000_000}MB を超えたため切り詰めて検査した",
            file=sys.stderr,
        )

    terms = []
    if args.terms:
        if not os.path.isfile(args.terms):
            raise SystemExit(f"error: 語彙ファイルが見つかりません: {args.terms}")
        with open(args.terms, encoding="utf-8") as f:
            terms = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    findings = scan(doc, terms)

    shown = findings if args.reveal else [
        {**f, "value": mask(f["value"]), "context": "（--reveal で表示）"}
        for f in findings
    ]

    result = {
        "ok": not findings,
        "doc": args.doc,
        "termsFile": args.terms,
        "revealed": args.reveal,
        "findings": shown,
        "note": "検出は誤検知を含む。伏せ字にするかどうかは人間が判断する。",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"SECRETS={len(findings)} RESULT={'OK' if result['ok'] else 'REVIEW_REQUIRED'}")
        for f_ in shown:
            dummy = " (ダミーの可能性)" if f_["looksDummy"] else ""
            print(f"  L{f_['line']} [{f_['label']}] {f_['value']}{dummy}")
            if args.reveal:
                print(f"      {f_['context']}")
        if findings and not args.reveal:
            print("  値と文脈は伏せた。中身を見るなら --reveal を付けるか、"
                  "資料の該当行を直接開く")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
