"""リポジトリの走査と、安全なファイル読み取り。

開いてよいかの判定は `security` に任せ、ここでは「どのファイルを・どこまで読むか」
（除外ディレクトリ・拡張子・サイズの上限）を決める。読んだ内容はキャッシュする。
"""

import os
import sys

from . import security

# 探索から外すディレクトリ。生成物とベンダーコードは構造の根拠にならない
SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", "dist", "build", ".next", ".nuxt",
    ".turbo", ".vercel", ".output", "coverage", ".dart_tool", "vendor",
    "__pycache__", ".venv", "venv", ".idea", ".vscode", "ios", "android",
    ".gradle", "Pods", ".expo", "out", ".cache", ".pytest_cache", ".mypy_cache",
    ".source-docs",
})

# 読み込む拡張子。バイナリと巨大なロックファイルを避ける
SOURCE_EXT = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte",
    ".dart", ".go", ".rb", ".py", ".php", ".java", ".kt", ".swift", ".rs",
    ".prisma", ".sql", ".graphql", ".gql", ".yaml", ".yml", ".json", ".toml",
})

# 拡張子では表せないが読むもの
SOURCE_NAMES = frozenset({"Gemfile", "go.mod", "requirements.txt"}) | security.ENV_SAFE

SKIP_FILES = frozenset({
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Podfile.lock",
    "composer.lock", "Gemfile.lock", "poetry.lock", "go.sum", "bun.lockb",
})

MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 50_000_000


def warn(message):
    print(f"warning: {message}", file=sys.stderr)


def is_source_name(name):
    if name in SKIP_FILES:
        return False
    if name in SOURCE_NAMES or name.lower() in SOURCE_NAMES:
        return True
    return os.path.splitext(name)[1].lower() in SOURCE_EXT


class Repo:
    """1 つのリポジトリ。ファイル一覧と、上限つきの読み取りを持つ。"""

    def __init__(self, key, root, exclude=()):
        self.key = key
        self.root = os.path.abspath(root)
        self._exclude = [os.path.abspath(p) for p in exclude]
        self.files = []          # リポジトリ相対（`/` 区切り）
        self._text = {}
        self._total = 0
        self.truncated = False
        self.skipped_large = []
        self._walk()

    def _excluded(self, path):
        return any(path == e or path.startswith(e + os.sep) for e in self._exclude)

    def _walk(self):
        for root, dirs, files in os.walk(self.root):
            dirs[:] = sorted(
                d for d in dirs
                if d not in SKIP_DIRS
                and d.lower() not in security.SECRET_DIRS
                and not self._excluded(os.path.join(root, d))
            )
            for name in sorted(files):
                full = os.path.join(root, name)
                if not is_source_name(name) or security.is_unsafe_to_open(full):
                    continue
                rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                self.files.append(rel)

    def abspath(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def exists(self, rel):
        return rel in self._index()

    def _index(self):
        if not hasattr(self, "_file_set"):
            self._file_set = set(self.files)
        return self._file_set

    def read(self, rel):
        """ファイルを読む。開いてはいけないもの・大きすぎるものは空文字を返す。"""
        if rel in self._text:
            return self._text[rel]
        path = self.abspath(rel)
        text = ""
        if security.is_unsafe_to_open(path) or not security.is_inside(self.root, path):
            self._text[rel] = text
            return text
        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_BYTES:
                self.skipped_large.append(rel)
            elif self._total + size > MAX_TOTAL_BYTES:
                if not self.truncated:
                    warn(f"読み込みが {MAX_TOTAL_BYTES // 1_000_000}MB を超えたため打ち切った: {self.key}")
                self.truncated = True
            else:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                self._total += size
        except OSError:
            text = ""
        self._text[rel] = text
        return text


def git_head(root):
    """HEAD のコミットハッシュを `.git` から直接読む。git コマンドに頼らない。"""
    git_dir = os.path.join(root, ".git")
    if os.path.isfile(git_dir):
        # worktree / submodule は `gitdir: <path>` を指す
        try:
            with open(git_dir, encoding="utf-8") as f:
                line = f.read().strip()
            if line.startswith("gitdir:"):
                git_dir = os.path.normpath(os.path.join(root, line[len("gitdir:"):].strip()))
        except OSError:
            return None
    head = os.path.join(git_dir, "HEAD")
    try:
        with open(head, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None
    if not content.startswith("ref:"):
        return content or None
    ref = content[len("ref:"):].strip()
    candidates = [git_dir]
    common = os.path.join(git_dir, "commondir")
    if os.path.isfile(common):
        try:
            with open(common, encoding="utf-8") as f:
                candidates.append(os.path.normpath(os.path.join(git_dir, f.read().strip())))
        except OSError:
            pass
    for base in candidates:
        try:
            with open(os.path.join(base, *ref.split("/")), encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            pass
        try:
            with open(os.path.join(base, "packed-refs"), encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(" ")
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
        except OSError:
            pass
    return None
