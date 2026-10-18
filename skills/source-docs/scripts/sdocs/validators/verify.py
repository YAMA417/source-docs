"""docs → source の照合。資料に書いた識別子が、コードに実在するかを確かめる。

照合するもの。

- エンドポイント（`POST /api/orders`）— Project Model にあるか。無ければソース本文も探す
- テーブル（表の「テーブル」列・`### \\`orders\\` — …` 見出し）— 同上
- ファイルのパス（`src/api/orders.ts`）— リポジトリに実在するか
- errorType（`ORDER_NOT_FOUND` のような大文字スネーク）— ソース本文にあるか

拾えるのは「実在しないものを書いた」誤りだけ。「分岐条件の説明が実装と違う」は検出できない。
Mermaid の図の中は照合しない（外部サービスや概念上の名前が出るため）。
"""

import os
import re

from ..core import fs
from ..core.model import normalize_path

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
RE_MERMAID_BLOCK = re.compile(r"^```\s*mermaid\s*$.*?^```\s*$", re.M | re.S)
RE_ENDPOINT = re.compile(r"\b(" + "|".join(HTTP_METHODS) + r")`?\s*\|?\s*`?(/[A-Za-z0-9_\-/{}\[\]:.$*]*)")
RE_ERROR_TYPE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")
RE_IMPL_PATH = re.compile(r"`([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-\[\]()+@]+)+\.[A-Za-z0-9]+)`")
RE_TABLE_HEADING = re.compile(
    r"^#{2,4}\s+`?([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)`?\s*[—–-]\s+\S")
# 環境変数の名前は errorType ではない。名前を書くのは正しい書き方なので照合しない
ENV_VAR_TAILS = (
    "_KEY", "_SECRET", "_TOKEN", "_URL", "_URI", "_HOST", "_PORT", "_ID", "_PASSWORD", "_ENDPOINT",
    "_REGION", "_BUCKET", "_DSN", "_WEBHOOK", "_API", "_ENV", "_PATH", "_DIR", "_NAME", "_VERSION",
    "_MODE", "_FLAG",
)
CHECKS = {
    "structure": ("endpoint", "errorType", "table"),
    "flow": ("endpoint", "table"),
    "detail": ("endpoint", "table", "path"),
}


def strip_mermaid(doc):
    return RE_MERMAID_BLOCK.sub("", doc)


def extract_endpoints(doc):
    found = set()
    for method, path in RE_ENDPOINT.findall(strip_mermaid(doc)):
        path = path.rstrip(".,)）」`")
        if path.strip("/"):
            found.add((method, path))
    return sorted(found)


def extract_error_types(doc):
    return sorted({n for n in RE_ERROR_TYPE.findall(strip_mermaid(doc)) if not n.endswith(ENV_VAR_TAILS)})


def extract_paths(doc):
    return sorted(set(RE_IMPL_PATH.findall(strip_mermaid(doc))))


def _cells(line):
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _is_divider(cells):
    joined = "".join(cells)
    return bool(cells) and "-" in joined and set(joined) <= set("-: ")


def cell_identifiers(cell):
    names = re.findall(r"`([^`]+)`", cell)
    if not names:
        text = re.sub(r"[（(][^）)]*[）)]", "", cell)
        names = re.split(r"[/、,]", text)
    out = []
    for name in names:
        name = re.sub(r"[（(][^）)]*[）)]", "", name).strip().strip("`").strip()
        if name and name not in {"—", "-"}:
            out.append(name)
    return out


def extract_tables(doc):
    """表の「テーブル」列と、`### \\`orders\\` — 注文` 形式の見出しからテーブル名を拾う。

    ヘッダーとみなすのは、直後に区切り行が続く行だけ。見出し行そのものは値として拾わない。
    """
    tables = []
    lines = [ln.strip() for ln in strip_mermaid(doc).splitlines()]
    col = None
    in_table = False
    for i, line in enumerate(lines):
        m = RE_TABLE_HEADING.match(line)
        if m:
            tables.append(m.group(1))
            in_table, col = False, None
            continue
        if not line.startswith("|"):
            in_table, col = False, None
            continue
        cells = _cells(line)
        if not in_table:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt.startswith("|") and _is_divider(_cells(nxt)):
                in_table = True
                col = next((j for j, c in enumerate(cells) if c.endswith("テーブル")), None)
                # 縦持ちの表（| テーブル | `orders` |）は見出し行の次から値を読むので、ここでは拾わない
            continue
        if _is_divider(cells):
            continue
        if col is not None and col < len(cells):
            tables.extend(cell_identifiers(cells[col]))
        elif len(cells) >= 2 and cells[0] == "テーブル":
            tables.extend(cell_identifiers(cells[1]))
    return sorted(set(tables))


class Source:
    """照合に使うソース本文。Project Model に載っていないものの最後の確認に使う。"""

    def __init__(self, model, out_dir):
        self._model = model
        self._out = out_dir
        self._repos = None
        self._text = None

    @property
    def repos(self):
        # 走査は重いので、Project Model だけで判定できなかったときに初めて行う
        if self._repos is None:
            self._repos = []
            for r in self._model["repositories"]:
                root = os.path.normpath(os.path.join(self._out, r["path"]))
                if os.path.isdir(root):
                    self._repos.append(fs.Repo(r["key"], root, exclude=[os.path.abspath(self._out)]))
        return self._repos

    @property
    def text(self):
        if self._text is None:
            self._text = "\n".join(repo.read(f) for repo in self.repos for f in repo.files)
        return self._text

    def path_exists(self, rel):
        for repo in self.repos:
            if repo.exists(rel):
                return True
        # モノレポのワークスペース名が付いている / 落ちている場合
        return any(f.endswith("/" + rel) or rel.endswith("/" + f) for repo in self.repos for f in repo.files)


def verify(model, out_dir, docs, mode=None):
    """docs: {相対パス: 本文}。mode を省くと、置き場所から判定する（detail/ なら detail）。"""
    endpoints = {(e["method"], normalize_path(e["path"])) for e in model["endpoints"] if e["protocol"] == "http"}
    any_method = {normalize_path(e["path"]) for e in model["endpoints"] if e["method"] == "ANY"}
    tables = set()
    for t in model["tables"]:
        tables |= {t["key"], t["name"], *t.get("aliases", [])}
    source = Source(model, out_dir)
    findings, checked = [], {"endpoint": 0, "errorType": 0, "table": 0, "path": 0}

    for rel, doc in sorted(docs.items()):
        doc_mode = mode or ("detail" if rel.startswith("detail/") else
                            "flow" if rel.startswith("flow/") else "structure")
        kinds = CHECKS[doc_mode]
        if "endpoint" in kinds:
            for method, path in extract_endpoints(doc):
                checked["endpoint"] += 1
                key = (method, normalize_path(path))
                if key in endpoints or key[1] in any_method:
                    continue
                if path in source.text:
                    findings.append(_f(rel, "endpoint", f"{method} {path}",
                                       "ソースに文字列はあるが、解析器はエンドポイントとして検出していない"))
                else:
                    findings.append(_f(rel, "endpoint", f"{method} {path}", "ルート定義に該当するものが無い"))
        if "errorType" in kinds:
            for name in extract_error_types(doc):
                checked["errorType"] += 1
                if name not in source.text:
                    findings.append(_f(rel, "errorType", name, "ソースにこの識別子が存在しない"))
        if "table" in kinds:
            for name in extract_tables(doc):
                checked["table"] += 1
                bare = name.split(".")[-1]
                if name in tables or bare in tables:
                    continue
                camel = "".join(p.capitalize() for p in re.split(r"[_\s]+", bare) if p)
                if bare in source.text or camel in source.text:
                    findings.append(_f(rel, "table", name, "ソースに文字列はあるが、スキーマ定義として検出していない"))
                else:
                    findings.append(_f(rel, "table", name, "スキーマ定義にこのテーブル / モデルが存在しない"))
        if "path" in kinds:
            for p in extract_paths(doc):
                if p.startswith(".source-docs/"):
                    continue  # 資料側の索引ファイル。ソースではない
                checked["path"] += 1
                if not source.path_exists(p):
                    findings.append(_f(rel, "path", p, "このパスのファイルが存在しない"))
    return {"ok": not findings, "checked": checked, "findings": findings}


def _f(doc, kind, value, reason):
    return {"doc": doc, "kind": kind, "value": value, "reason": reason}
