#!/usr/bin/env python3
"""設計書に載ってはいけない情報が混ざっていないか調べる。

検出対象は 3 種類。
  - 認証情報・鍵（API キー / トークン / パスワード / 秘密鍵 / DB 接続文字列）
  - 個人情報（実データのメールアドレス・電話番号）
  - 取引先名・契約情報（案件ごとの語彙リストが与えられた場合のみ）

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

# (種別, ラベル, 正規表現)
PATTERNS = [
    # --- 認証情報・鍵 ---
    ("credential", "AWS アクセスキー", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("credential", "秘密鍵ブロック", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credential", "Bearer トークン", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}")),
    ("credential", "JWT らしき文字列", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
    ("credential", "DB 接続文字列", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:@/]+:[^\s@/]+@")),
    (
        "credential",
        "キー・シークレットへの値の代入",
        re.compile(
            r"\b(?:api[_-]?key|secret|password|passwd|token|client[_-]?secret|private[_-]?key)"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9\-_/+=]{12,}",
            re.IGNORECASE,
        ),
    ),
    # --- 個人情報 ---
    ("pii", "メールアドレス", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("pii", "電話番号（E.164）", re.compile(r"\+\d{1,3}\d{7,13}\b")),
    ("pii", "電話番号（国内表記）", re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b")),
]

# 明らかな説明用ダミー。除外はここだけに留める。判断を機械に寄せすぎない。
DUMMY_HINTS = (
    "example.com", "example.org", "example.net", "@example",
    "xxx@", "user@example", "test@test",
)


def scan(doc, terms):
    findings = []
    lines = doc.splitlines()

    for lineno, line in enumerate(lines, 1):
        for kind, label, pattern in PATTERNS:
            for m in pattern.finditer(line):
                value = m.group(0)
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
    args = ap.parse_args()

    if not os.path.isfile(args.doc):
        raise SystemExit(f"error: 設計書が見つかりません: {args.doc}")

    with open(args.doc, encoding="utf-8") as f:
        doc = f.read()

    terms = []
    if args.terms:
        if not os.path.isfile(args.terms):
            raise SystemExit(f"error: 語彙ファイルが見つかりません: {args.terms}")
        with open(args.terms, encoding="utf-8") as f:
            terms = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    findings = scan(doc, terms)

    result = {
        "ok": not findings,
        "doc": args.doc,
        "termsFile": args.terms,
        "findings": findings,
        "note": "検出は誤検知を含む。伏せ字にするかどうかは人間が判断する。",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"SECRETS={len(findings)} RESULT={'OK' if result['ok'] else 'REVIEW_REQUIRED'}")
        for f_ in findings:
            dummy = " (ダミーの可能性)" if f_["looksDummy"] else ""
            print(f"  L{f_['line']} [{f_['label']}] {f_['value']}{dummy}")
            print(f"      {f_['context']}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
