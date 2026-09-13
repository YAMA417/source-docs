#!/usr/bin/env python3
"""設計書に書いた識別子がソースコードに実在するかを照合する。

照合する対象は資料の種類（--mode）で変わる。CHECKS を参照。
定数の「値」は設計書「30 分」・コード `1800` のような表記揺れで誤検知が出るため対象外にしている。

拾えるのは「実在しないものを書いた」誤りだけで、「分岐条件の説明が実装と違う」
「書かれていない分岐がある」は検出できない。そこは人間が読むしかない。

使い方:
    python3 verify.py --doc <md> --repo <path> [--repo <path>...]
        [--mode structure|flow|detail] [--json]

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
    ".yaml", ".yml", ".json", ".arb",
}

# 拡張子では表せないファイル名。`os.path.splitext(".env.example")` は
# `(".env", ".example")` を返すので、拡張子の集合には入れられない。
# 読んでよいテンプレートの定義は `_secure` を正本にする。
SOURCE_NAMES = frozenset(_secure.ENV_SAFE)

SKIP_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Podfile.lock"}

MAX_FILE_BYTES = 1_000_000

# 連結したソース全体の上限。大きなリポジトリでメモリを食い尽くさないため。
# 超えたら打ち切り、打ち切ったことを呼び出し元に伝える
MAX_TOTAL_BYTES = 50_000_000

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# 連結したソースの中で、ファイルの切れ目を示す印。
# Next.js App Router のようにパスをディレクトリで、メソッドを名前付き export で
# 表す書き方は、ファイルの対応が取れないと判定できない。
FILE_MARKER = "\n//---- source-docs:file "

# `export async function POST(` / `export function GET(` を拾う
RE_NEXT_METHOD = {
    m: re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+" + m + r"\b")
    for m in HTTP_METHODS
}
# `app/` 配下の `route.ts`。間のディレクトリ列がそのまま URL のパスになる
RE_NEXT_ROUTE_FILE = re.compile(r"(?:^|/)app/(.*/)?route\.[jt]sx?$")

# 入口ではないファイル。テストにも同じ形の export が書かれることがある
NOT_A_ROUTE_SUFFIX = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

# md 内の `POST /me/prize-orders` 形式。表・本文・コードブロックのどこにあっても拾う。
RE_ENDPOINT = re.compile(
    r"\b(" + "|".join(HTTP_METHODS) + r")`?\s*\|?\s*`?(/[A-Za-z0-9_\-/{}\[\]:\.\$\*]*)"
)

# インラインコードの中の大文字スネーク。errorType の候補。
RE_ERROR_TYPE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")

# 環境変数の名前は errorType ではない。資料に名前を書くのは正しい書き方なので、
# 照合対象に入れると正しい資料が毎回不一致になる。末尾の語で見分ける。
ENV_VAR_TAILS = (
    "_KEY", "_SECRET", "_TOKEN", "_URL", "_URI", "_HOST", "_PORT", "_ID",
    "_PASSWORD", "_ENDPOINT", "_REGION", "_BUCKET", "_DSN", "_WEBHOOK",
    "_API", "_ENV", "_PATH", "_DIR", "_NAME", "_VERSION", "_MODE", "_FLAG",
)

# Mermaid のコードブロック。図の中身は照合しない。
# シーケンス図には外部サービスや概念上のパスが出るので、拾えば必ず誤検知になる。
RE_MERMAID_BLOCK = re.compile(r"^```\s*mermaid\s*$.*?^```\s*$", re.M | re.S)

# インラインコードのうち、実装ファイルのパスらしきもの。
# `frontend/app/api/articles/route.ts` のように、スラッシュと拡張子を両方持つ。
RE_IMPL_PATH = re.compile(r"`([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-\[\]()]+)+\.[A-Za-z0-9]+)`")

# 文書の種類ごとに、何を照合するか。
# flow は内部の識別子をほとんど書かないので errorType を見ない。
# detail は実装ファイルのパスを書くので、その実在を確かめる。
CHECKS = {
    "structure": ("endpoint", "errorType", "table"),
    "flow": ("endpoint", "table"),
    "detail": ("endpoint", "table", "path"),
}


# パスパラメータ。`:id` `{id}` `<id>` `$userId` の表記揺れを吸収する。
RE_PATH_PARAM = re.compile(r"^(\{.*\}|:.+|<.+>|\$.+|\[.+\])$")

# `### `public.orders` — 注文` / `### public.orders — 注文` の両方を拾う。
# 説明のダッシュが続くものだけをテーブル定義の見出しとみなす。
# 「### 3. テーブル一覧」のような章見出しは識別子の形に合わないので拾わない。
RE_TABLE_HEADING = re.compile(
    r"^#{2,4}\s+`?([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)`?\s*[\u2014\u2013-]\s+\S"
)


def strip_mermaid(doc):
    """Mermaid のコードブロックを取り除く。抽出の前段に必ず通す。"""
    return RE_MERMAID_BLOCK.sub("", doc)


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
                if (name.lower() not in SOURCE_NAMES
                        and os.path.splitext(name)[1] not in SOURCE_EXT):
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
                        rel = os.path.relpath(path, repo).replace(os.sep, "/")
                        chunks.append(FILE_MARKER + rel + "\n" + f.read())
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
    for method, path in RE_ENDPOINT.findall(strip_mermaid(doc)):
        path = path.rstrip(".,)）」`")
        # `POST / PUT` のような併記から拾った `/` だけのパスは照合材料にならない。
        # 静的セグメントが無いものは無条件で通ってしまうので、最初から外す
        if path.strip("/") == "":
            continue
        found.append((method, path))
    return sorted(set(found))


def extract_error_types(doc):
    found = [
        name for name in RE_ERROR_TYPE.findall(strip_mermaid(doc))
        if not name.endswith(ENV_VAR_TAILS)
    ]
    return sorted(set(found))


def extract_paths(doc):
    """資料に書かれた実装ファイルのパスを拾う。"""
    return sorted(set(RE_IMPL_PATH.findall(strip_mermaid(doc))))


def path_exists(rel, repos):
    """資料に書かれたパスが、いずれかのリポジトリに実在するか。

    リポジトリ直下からの相対パスとして見る。見つからない場合は、
    モノレポのワークスペース名が頭に付いている / 落ちている可能性があるので、
    末尾の一致でも探す。
    """
    for repo in repos:
        if os.path.exists(os.path.join(repo, rel)):
            return True
    tail = rel.split("/")[-1]
    for repo in repos:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            if tail in files:
                full = os.path.join(root, tail).replace(os.sep, "/")
                if full.endswith(rel):
                    return True
    return False


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
    lines = [ln.strip() for ln in strip_mermaid(doc).splitlines()]
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

    # 縦持ちの表（`| テーブル | \`articles\` (C) |`）も拾う。
    # flow / detail の「関連」表は種別を行で並べるため、列では見つからない。
    # 行ラベルが「テーブル」そのものである場合だけに限る。
    # 「（3 テーブル）」のような説明文を巻き込まないため
    for stripped in lines:
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == "テーブル":
            tables.extend(cell_identifiers(cells[1]))

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


def url_shape(path):
    """パスを、パラメータを `*` に置き換えた形にする。

    `/api/articles/{id}` → `["api", "articles", "*"]`
    ファイル名でルーティングする書き方と位置ごとに比べるために使う。
    """
    return [
        "*" if RE_PATH_PARAM.match(s) else s
        for s in path.split("/") if s
    ]


def _next_route_segments(header):
    """Next.js App Router のルートファイルから、静的セグメント列を取り出す。

    `frontend/app/api/articles/route.ts` → `["api", "articles"]`

    動的セグメント `[id]` `[...path]` とルートグループ `(shop)` はパスに出ないので落とす。
    ルートファイルでなければ `None` を返す。
    """
    m = RE_NEXT_ROUTE_FILE.search(header)
    if not m:
        return None
    inner = (m.group(1) or "").strip("/")
    if not inner:
        return []
    segs = []
    for seg in inner.split("/"):
        # ルートグループは URL に出ない
        if seg.startswith("(") and seg.endswith(")"):
            continue
        # 動的セグメントは位置を保つ。落とすと `/api/articles/[id]` が
        # `/api/articles` と同じ形になり、別のルートを同一視してしまう
        segs.append("*" if seg.startswith("[") else seg)
    return segs


def _next_route_blocks(sources, segs):
    """指定したパスをそのまま表しているルートファイルの断片を返す。

    セグメントの包含で見ると、`/api/articles` の照合に
    `app/api/articles/[id]/route.ts` まで拾ってしまう。**完全一致**を要求する。
    """
    out = []
    for block in sources.split(FILE_MARKER)[1:]:
        header = block.split("\n", 1)[0]
        if header.endswith(NOT_A_ROUTE_SUFFIX) or "__tests__/" in header:
            continue
        if _next_route_segments(header) == segs:
            out.append(block)
    return out


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

    # ファイル名でルーティングする書き方なら、そのファイルが定義の正本。
    # 呼び出し側の `.delete("/api/articles/" + id)` のような記述より確度が高いので、
    # ルートファイルが見つかった時点で他のパターンは見ない
    blocks = _next_route_blocks(sources, url_shape(path))
    if blocks:
        for block in blocks:
            found |= {m for m in HTTP_METHODS if RE_NEXT_METHOD[m].search(block)}
        return bool(found) and method not in found

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
    ap.add_argument(
        "--mode",
        choices=sorted(CHECKS),
        default="structure",
        help="資料の種類。照合する対象が変わる（既定: structure）",
    )
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

    kinds = CHECKS[args.mode]
    findings = []
    checked = {kind: 0 for kind in kinds}

    if "endpoint" in kinds:
        for method, path in extract_endpoints(doc):
            checked["endpoint"] += 1
            if not endpoint_exists(method, path, sources):
                findings.append({
                    "kind": "endpoint",
                    "value": f"{method} {path}",
                    "reason": "ルート定義に該当するパスが見つからない",
                })

    if "errorType" in kinds:
        for et in extract_error_types(doc):
            checked["errorType"] += 1
            if et not in sources:
                findings.append({
                    "kind": "errorType",
                    "value": et,
                    "reason": "ソースにこの識別子が存在しない",
                })

    if "table" in kinds:
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

    if "path" in kinds:
        for rel in extract_paths(doc):
            checked["path"] += 1
            if not path_exists(rel, args.repo):
                findings.append({
                    "kind": "path",
                    "value": rel,
                    "reason": "このパスのファイルが存在しない",
                })

    result = {
        "ok": not findings,
        "doc": args.doc,
        "mode": args.mode,
        "repos": args.repo,
        "scannedFiles": file_count,
        "checked": checked,
        "findings": findings,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        counts = " ".join(f"{k}={v}" for k, v in checked.items())
        print(
            f"MODE={args.mode} CHECKED {counts} FILES={file_count} "
            f"RESULT={'OK' if result['ok'] else 'MISMATCH'}"
        )
        for f_ in findings:
            print(f"  [{f_['kind']}] {f_['value']} — {f_['reason']}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
