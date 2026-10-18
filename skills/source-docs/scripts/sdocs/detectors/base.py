"""アダプタの共通の型と、ソースを読むための小さな道具。

アダプタは「このフレームワークなら、どこに何が書かれているか」だけを知る。
見つけた事実は `Builder` に渡し、関係の解決や機能の組み立てはしない。

1 つのアダプタが持つ役割は 3 つ。どれも任意。

- `collect`  — 実体（画面・API・テーブル・ジョブ…）を宣言する
- `usages`   — コード片の中で、何を使っているか（テーブル操作・API 呼び出し・遷移…）を拾う
- `imports`  — コード片から辿るべき別ファイルを返す（言語ごとの import 解決）
"""

import re


class AppContext:
    """1 つのアプリケーション（manifest のあるディレクトリ）。"""

    def __init__(self, app_id, key, repo, root_rel, name, deps, manifests, files):
        self.id = app_id
        self.key = key
        self.repo = repo
        self.root_rel = root_rel          # リポジトリ相対。直下なら ""
        self.name = name
        self.deps = deps
        self.manifests = manifests
        self.files = files                # このアプリに属するファイル（リポジトリ相対）
        self.adapters = []

    @property
    def ref(self):
        return {"id": self.id}

    def has_dep(self, *names):
        return any(n in self.deps for n in names)

    def read(self, rel):
        return self.repo.read(rel)

    def app_rel(self, rel):
        """アプリのルートからの相対パス。"""
        if self.root_rel and rel.startswith(self.root_rel + "/"):
            return rel[len(self.root_rel) + 1:]
        return rel

    def ev(self, rel, line=None, rule=None):
        return {"repo": self.repo.key, "file": rel, "line": line, "rule": rule}

    def loc(self, rel, start=0, end=None, line=None):
        return {"repo": self.repo.key, "file": rel, "line": line, "start": start, "end": end}

    def files_matching(self, pattern):
        rx = re.compile(pattern)
        return [f for f in self.files if rx.search(self.app_rel(f))]


class Adapter:
    """フレームワーク・言語ごとの検出器。サブクラスで必要なものだけ上書きする。"""

    name = ""

    def applies(self, app):
        return False

    def collect(self, app, builder):
        pass

    def usages(self, app, rel, text):
        """コード片 text の中で使われているものを返す。

        返す要素は dict。`kind` によって残りのキーが変わる。

        - table       : alias, op（CREATE/READ/UPDATE/DELETE）
        - integration : key, name, direction
        - call        : method（不明なら None）, path
        - navigate    : route, how
        - publish     : event
        - enqueue     : queue
        - env         : name
        """
        return []

    def imports(self, app, rel, text):
        """{ローカル名: 解決先のリポジトリ相対パス}。名前の無い import は `*n` のキー。"""
        return {}

    def aliases(self, app, rel, text):
        """{ローカル名: 元の名前}。`import { books as booksTable }` の読み替えに使う。"""
        return {}


# --- 小さな道具 ----------------------------------------------------------------

def line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def match_brace(text, open_index, open_ch="{", close_ch="}"):
    """open_index の括弧に対応する閉じ括弧の位置。文字列とコメントは飛ばす。"""
    depth = 0
    i = open_index
    n = len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top_level(text, sep=","):
    """括弧の外側にある区切り文字で分ける。"""
    parts, buf, depth, quote = [], [], 0, None
    for c in text:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            continue
        if c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts


def segments(text, starts):
    """定義の開始位置の列から、各定義の範囲 (start, end) を作る。次の定義の手前までを 1 つとみなす。"""
    starts = sorted(set(starts))
    out = []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        out.append((s, end))
    return out


def strip_comments(text):
    """`//` と `/* */` のコメントを、位置を保ったまま空白に置き換える。"""
    out = list(text)
    i, n, quote = 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or (c == "\n" and quote != "`"):
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            i += 1
            continue
        if text.startswith("//", i) and (i == 0 or text[i - 1] != ":"):
            end = text.find("\n", i)
            end = n if end == -1 else end
            for j in range(i, end):
                out[j] = " "
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def route_from_dirs(parts):
    """ファイルベースルーティングのディレクトリ列から URL のパスを作る。

    `(group)` は URL に出ない。`@slot` も出ない。`[id]` は `:id`、`[...rest]` は `*rest`。
    """
    out = []
    for seg in parts:
        if not seg or (seg.startswith("(") and seg.endswith(")")) or seg.startswith("@"):
            continue
        if seg.startswith("[[...") and seg.endswith("]]"):
            out.append("*" + seg[5:-2])
        elif seg.startswith("[...") and seg.endswith("]"):
            out.append("*" + seg[4:-1])
        elif seg.startswith("[") and seg.endswith("]"):
            out.append(":" + seg[1:-1])
        else:
            out.append(seg)
    return "/" + "/".join(out)


def stem(rel):
    base = rel.rsplit("/", 1)[-1]
    return base.split(".", 1)[0]


def dirname(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def join(base, rel):
    """`/` 区切りのパスを結合して正規化する。リポジトリの外へ出るなら None。"""
    parts = []
    for seg in (base.split("/") if base else []) + rel.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


def exists_any(repo, candidates):
    for c in candidates:
        if c and repo.exists(c):
            return c
    return None


def is_cron_expression(value):
    fields = value.split()
    return 5 <= len(fields) <= 6 and all(re.fullmatch(r"[\d*/,\-?LW#A-Za-z]+", f) for f in fields)


def env_name_ok(name):
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", name))


def add_column(table, col, source, replace=False):
    """カラムを、どの情報源から読んだかを付けて記録する。

    情報源が違えば同じ名前でも別々に持つ（migration と ORM の突き合わせは `core.dbstate` が行う）。
    replace=True なら同じ情報源・同じ名前のものを置き換える（migration の MODIFY など）。
    """
    col = dict(col, source=source)
    col.setdefault("status", "confirmed")
    if replace:
        table["columns"] = [c for c in table["columns"]
                            if not (c["source"] == source and c["name"] == col["name"])]
    table["columns"].append(col)
    return col


def column(name, type_, nullable=None, default=None, primary=False, unique=False,
           references=None, note=None):
    col = {"name": name, "type": type_, "nullable": nullable, "default": default,
           "primaryKey": primary, "unique": unique, "references": references}
    if note:
        col["note"] = note
    return col


__all__ = [
    "add_column", "column",
    "Adapter", "AppContext", "line_of", "match_brace", "split_top_level", "segments",
    "strip_comments", "route_from_dirs", "stem", "dirname", "join", "exists_any",
    "is_cron_expression", "env_name_ok",
]
