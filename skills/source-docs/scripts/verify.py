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

import _secure

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

# 連結したソース全体の上限。大きなリポジトリでメモリを食い尽くさないため。
# 超えたら打ち切り、打ち切ったことを呼び出し元に伝える
MAX_TOTAL_BYTES = 50_000_000

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# md 内の `POST /me/prize-orders` 形式。表・本文・コードブロックのどこにあっても拾う。
RE_ENDPOINT = re.compile(
    r"\b(" + "|".join(HTTP_METHODS) + r")`?\s*\|?\s*`?(/[A-Za-z0-9_\-/{}:\.\$\*]*)"
)

# インラインコードの中の大文字スネーク。errorType の候補。
RE_ERROR_TYPE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")

# パスパラメータ。`:id` `{id}` `<id>` `$userId` の表記揺れを吸収する。
RE_PATH_PARAM = re.compile(r"^(\{.*\}|:.+|<.+>|\$.+|\[.+\])$")

# `### `public.orders` — 注文` / `### public.orders — 注文` の両方を拾う。
# 説明のダッシュが続くものだけをテーブル定義の見出しとみなす。
# 「### 3. テーブル一覧」のような章見出しは識別子の形に合わないので拾わない。
RE_TABLE_HEADING = re.compile(
    r"^#{2,4}\s+`?([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)`?\s*[\u2014\u2013-]\s+\S"
)


def strip_schema(name):
    """`public.orders` のようなスキーマ修飾を落とす。"""
    return name.split(".")[-1] if "." in name else name


def load_sources(repos):
    """リポジトリ内のソースを 1 本のテキストに連結して返す。

    候補ごとにファイルを開き直すと I/O が候補数に比例するので、一度だけ読む。

    認証情報が入りうるファイル（`.env` / 鍵 / credentials 配下）は
    `_secure` の判定で**開かない**。照合に使わないうえ、読む必要もない。
    """
    chunks = []
    file_count = 0
    total = 0
    truncated = False

    for repo in repos:
        if not os.path.isdir(repo):
            print(f"error: リポジトリが見つかりません: {repo}", file=sys.stderr)
            raise SystemExit(2)
        for root, dirs, files in os.walk(repo):
            dirs[:] = [
                d for d in dirs
                if d not in SKIP_DIRS and d.lower() not in _secure.SECRET_DIRS
            ]
            for name in files:
                if name in SKIP_FILES:
                    continue
                if _secure.is_secret_file(name):
                    continue
                if os.path.splitext(name)[1] not in SOURCE_EXT:
                    continue
                path = os.path.join(root, name)
                if _secure.is_unsafe_to_open(path):
                    continue
                try:
                    size = os.path.getsize(path)
                    if size > MAX_FILE_BYTES:
                        continue
                    if total + size > MAX_TOTAL_BYTES:
                        truncated = True
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        chunks.append(f.read())
                    total += size
                    file_count += 1
                except OSError:
                    continue

    if truncated:
        print(
            f"warning: 読み込みが {MAX_TOTAL_BYTES // 1_000_000}MB を超えたため打ち切った。"
            "照合の取りこぼしが出る可能性がある",
            file=sys.stderr,
        )
    return "\n".join(chunks), file_count


def extract_endpoints(doc):
    found = []
    for method, path in RE_ENDPOINT.findall(doc):
        path = path.rstrip(".,)）」`")
        found.append((method, path))
    return sorted(set(found))


def extract_error_types(doc):
    return sorted(set(RE_ERROR_TYPE.findall(doc)))


def _is_divider(cells):
    """Markdown の表の区切り行（| --- | --- |）かどうか。

    ハイフンを 1 つ以上含むことを条件にする。`|  |  |` のような
    空セル行を区切りと誤認しないため。
    """
    joined = "".join(cells)
    return bool(cells) and "-" in joined and set(joined) <= set("-: ")


def extract_tables(doc):
    """テーブル名を 2 つの経路で拾う。

    1. ヘッダーに「テーブル」を含む列を持つ表の、その列
    2. `### public.orders — 注文` 形式の見出し

    ヘッダーは部分一致で見る（「テーブル（CRUD）」のような列名があるため）が、
    **直後に区切り行が続くことを条件にする。** そうしないと、本文セルに
    「テーブル」の語が入っているだけの行をヘッダーと誤認し、
    以降の全行をテーブル名として拾ってしまう。
    """
    tables = []
    lines = [ln.strip() for ln in doc.splitlines()]
    in_table = False
    col = None

    for i, stripped in enumerate(lines):
        m = RE_TABLE_HEADING.match(stripped)
        if m:
            tables.append(m.group(1))
            in_table = False
            col = None
            continue

        if not stripped.startswith("|"):
            in_table = False
            col = None
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]

        if not in_table:
            # 次の行が区切り行のときだけヘッダーとみなす
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not nxt.startswith("|"):
                continue
            next_cells = [c.strip() for c in nxt.strip("|").split("|")]
            if not _is_divider(next_cells):
                continue
            for j, cell in enumerate(cells):
                if "テーブル" in cell:
                    in_table = True
                    col = j
                    break
            continue

        if _is_divider(cells):
            continue
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
        # `orders (C)` のような CRUD 注記を落とす
        name = re.sub(r"[（(][^）)]*[）)]", "", name)
        name = name.strip().strip("`").strip()
        if name and name not in {"—", "-", ""}:
            out.append(name)
    return out


def static_segments(path):
    """パスから静的セグメント（パラメータでない部分）だけを取り出す。"""
    return [s for s in path.split("/") if s and not RE_PATH_PARAM.match(s)]


def _method_conflicts(method, path, sources):
    """パスに対して、書いたメソッドと違うメソッドだけが定義されていないか。

    `router.get("/only-get")` しか無いのに `POST /only-get` と書いた、という
    誤りを拾う。メソッドが読み取れない書き方（オブジェクトのキーで持つなど）は
    判定できないので、その場合は衝突なしとして扱う。
    """
    segs = static_segments(path)
    if not segs:
        return False
    tail = segs[-1]

    found = set()
    for m in HTTP_METHODS:
        # `.get("/x"` `.get('/x'` `@Get("/x"` `router.get(\`/x\`` などを拾う
        pattern = re.compile(
            r"[.@]" + m.lower() + r"\s*\(\s*[\"\'`][^\"\'`]*" + re.escape(tail),
            re.IGNORECASE,
        )
        if pattern.search(sources):
            found.add(m)

    # メソッド付きの定義が 1 つも見つからないなら判定材料が無い
    if not found:
        return False
    return method not in found


def endpoint_exists(method, path, sources):
    """静的セグメントの最長連続列がソースに現れるかで判定する。

    ルート定義は親でマウントされて分割されていることが多く、フルパスは
    ソース中に現れない。そのため連続する静的セグメントの結合で照合する。
    誤検知を減らす方向に倒し、取りこぼしは許容する。

    パスが見つかっても、**メソッドが食い違っていれば不一致とする。**
    """
    segs = static_segments(path)
    if not segs:
        return True  # ルートパスなど、照合対象が無いものは通す

    if _method_conflicts(method, path, sources):
        return False

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

    # 検査対象そのものにも上限を設ける
    with open(args.doc, "rb") as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        print(
            f"warning: 設計書が {MAX_FILE_BYTES // 1_000_000}MB を超えたため切り詰めた",
            file=sys.stderr,
        )
    doc = raw[:MAX_FILE_BYTES].decode("utf-8", errors="ignore")

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
        # スキーマ修飾を落として照合する。ソースに `public.` は現れない
        bare = strip_schema(table)
        # スキーマ定義は snake_case と PascalCase の両方がありうる
        camel = "".join(p.capitalize() for p in re.split(r"[_\s]+", bare) if p)
        if bare not in sources and camel not in sources:
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
