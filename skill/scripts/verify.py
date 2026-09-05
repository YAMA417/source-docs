#!/usr/bin/env python3
"""設計書に書いた識別子がソースコードに実在するかを照合する。

対象はエンドポイント / errorType / テーブル名の 3 つだけ。
定数の「値」は設計書「30 分」・コード `1800` のような表記揺れで誤検知が出るため対象外にしている。

拾えるのは「実在しないものを書いた」誤りだけで、「分岐条件の説明が実装と違う」
「書かれていない分岐がある」は検出できない。そこは人間が読むしかない。

使い方:
    python3 verify.py --doc <md> --repo <path> [--repo <path>...] [--json]

終了コード:
    0  不一致なし
    1  不一致あり
    2  引数・入出力の誤り
"""

import argparse
import json
import os
import re
import sys

# 探索から外すディレクトリ。生成物とベンダーコードを読んでも実在確認の役に立たない。
SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", ".turbo", "coverage",
    ".dart_tool", "vendor", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    "ios", "android", ".gradle", "Pods",
}

# 読み込む拡張子。バイナリと巨大なロックファイルを避ける。
SOURCE_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".dart", ".go", ".rb", ".py",
    ".php", ".java", ".kt", ".swift", ".rs", ".prisma", ".sql", ".graphql",
    ".yaml", ".yml", ".json", ".arb", ".env.example",
}

SKIP_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Podfile.lock"}

MAX_FILE_BYTES = 1_000_000

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# md 内の `POST /me/prize-orders` 形式。表・本文・コードブロックのどこにあっても拾う。
RE_ENDPOINT = re.compile(
    r"\b(" + "|".join(HTTP_METHODS) + r")\s+(/[A-Za-z0-9_\-/{}:\.\$\*]*)"
)

# インラインコードの中の大文字スネーク。errorType の候補。
RE_ERROR_TYPE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")

# パスパラメータ。`:id` `{id}` `<id>` `$userId` の表記揺れを吸収する。
RE_PATH_PARAM = re.compile(r"^(\{.*\}|:.+|<.+>|\$.+|\[.+\])$")


def load_sources(repos):
    """リポジトリ内のソースを 1 本のテキストに連結して返す。

    候補ごとにファイルを開き直すと I/O が候補数に比例するので、一度だけ読む。
    """
    chunks = []
    file_count = 0
    for repo in repos:
        if not os.path.isdir(repo):
            raise SystemExit(f"error: リポジトリが見つかりません: {repo}")
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name in SKIP_FILES:
                    continue
                if os.path.splitext(name)[1] not in SOURCE_EXT:
                    continue
                path = os.path.join(root, name)
                try:
                    if os.path.getsize(path) > MAX_FILE_BYTES:
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        chunks.append(f.read())
                    file_count += 1
                except OSError:
                    continue
    return "\n".join(chunks), file_count


def extract_endpoints(doc):
    found = []
    for method, path in RE_ENDPOINT.findall(doc):
        path = path.rstrip(".,)）」`")
        found.append((method, path))
    return sorted(set(found))


def extract_error_types(doc):
    return sorted(set(RE_ERROR_TYPE.findall(doc)))


def extract_tables(doc):
    """CRUD 表の 2 列目からテーブル名を拾う。

    テンプレートで列順を `DB | テーブル | C | R | U | D | 備考` に固定してあるので、
    ヘッダー行に「テーブル」を含む表だけを対象にする。
    """
    tables = []
    lines = doc.splitlines()
    in_table = False
    col = None
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            col = None
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not in_table:
            if "テーブル" in cells:
                in_table = True
                col = cells.index("テーブル")
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # 区切り行
        if col is None or col >= len(cells):
            continue
        tables.extend(cell_identifiers(cells[col]))
    return sorted(set(tables))


def cell_identifiers(cell):
    """表のセルからテーブル名だけを取り出す。

    セルには `User`（Global DB） や `Device` / `UserDevice` のように、
    補足や複数名が同居することがある。バッククォート内を優先して拾い、
    無ければ補足を落としたテキストを使う。
    """
    names = re.findall(r"`([^`]+)`", cell)
    if not names:
        # 全角・半角の括弧内の補足を落とす
        text = re.sub(r"[（(][^）)]*[）)]", "", cell)
        names = re.split(r"[/、,]", text)
    out = []
    for name in names:
        name = name.strip().strip("`").strip()
        if name and name not in {"—", "-", ""}:
            out.append(name)
    return out


def static_segments(path):
    """パスから静的セグメント（パラメータでない部分）だけを取り出す。"""
    return [s for s in path.split("/") if s and not RE_PATH_PARAM.match(s)]


def endpoint_exists(method, path, sources):
    """静的セグメントの最長連続列がソースに現れるかで判定する。

    ルート定義は親でマウントされて分割されていることが多く、フルパスは
    ソース中に現れない。そのため連続する静的セグメントの結合で照合する。
    誤検知を減らす方向に倒し、取りこぼしは許容する。
    """
    segs = static_segments(path)
    if not segs:
        return True  # ルートパスなど、照合対象が無いものは通す

    # 1. 連続する 2 セグメント以上がそのまま現れるか。最も確度が高い
    for size in range(len(segs), 1, -1):
        for start in range(0, len(segs) - size + 1):
            if "/".join(segs[start:start + size]) in sources:
                return True

    # 2. 連続では現れない場合（親でマウントして分割定義しているケース）は、
    #    全セグメントが個別にパス片として現れることを要求する。
    #    単独セグメントの部分一致で通すと、存在しないパスまで通ってしまう。
    return all("/" + seg in sources for seg in segs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True, help="検証する設計書 md")
    ap.add_argument("--repo", action="append", required=True, help="対象リポジトリ（複数可）")
    ap.add_argument("--json", action="store_true", help="JSON で出力する")
    args = ap.parse_args()

    if not os.path.isfile(args.doc):
        raise SystemExit(f"error: 設計書が見つかりません: {args.doc}")

    with open(args.doc, encoding="utf-8") as f:
        doc = f.read()

    sources, file_count = load_sources(args.repo)

    findings = []
    checked = {"endpoint": 0, "errorType": 0, "table": 0}

    for method, path in extract_endpoints(doc):
        checked["endpoint"] += 1
        if not endpoint_exists(method, path, sources):
            findings.append({
                "kind": "endpoint",
                "value": f"{method} {path}",
                "reason": "ルート定義に該当するパスが見つからない",
            })

    for et in extract_error_types(doc):
        checked["errorType"] += 1
        if et not in sources:
            findings.append({
                "kind": "errorType",
                "value": et,
                "reason": "ソースにこの識別子が存在しない",
            })

    for table in extract_tables(doc):
        checked["table"] += 1
        # スキーマ定義は snake_case と PascalCase の両方がありうる
        camel = "".join(p.capitalize() for p in re.split(r"[_\s]+", table) if p)
        if table not in sources and camel not in sources:
            findings.append({
                "kind": "table",
                "value": table,
                "reason": "スキーマ定義にこのテーブル / モデルが存在しない",
            })

    result = {
        "ok": not findings,
        "doc": args.doc,
        "repos": args.repo,
        "scannedFiles": file_count,
        "checked": checked,
        "findings": findings,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"CHECKED endpoint={checked['endpoint']} errorType={checked['errorType']} "
            f"table={checked['table']} FILES={file_count} "
            f"RESULT={'OK' if result['ok'] else 'MISMATCH'}"
        )
        for f_ in findings:
            print(f"  [{f_['kind']}] {f_['value']} — {f_['reason']}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
