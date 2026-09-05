#!/usr/bin/env python3
"""リポジトリを走査して、スタックの判定結果と読むべきファイルの候補を出す。

このスクリプトの出力は「どこを読むか」を決めるための足場であって、
資料の生成元ではない。資料は Claude が実際の実装を読んで書く。

判定は依存名の一致だけで行うので、対応表に無いものは「判定できず」になる。
判定できなかったことを推測で埋めない。

使い方:
    python3 inventory.py --repo <path> [--repo <path>...] [--json]

終了コード:
    0  正常
    2  引数・入出力の誤り
"""

import argparse
import fnmatch
import json
import os
import re
import sys

import _secure

# 依存名 → (区分, 表示名)
FRAMEWORK_HINTS = {
    "next": ("frontend", "Next.js"),
    "react": ("frontend", "React"),
    "vue": ("frontend", "Vue"),
    "@angular/core": ("frontend", "Angular"),
    "svelte": ("frontend", "Svelte"),
    "nuxt": ("frontend", "Nuxt"),
    "expo": ("frontend", "Expo"),
    "react-native": ("frontend", "React Native"),
    "flutter": ("frontend", "Flutter"),
    "express": ("backend", "Express"),
    "fastify": ("backend", "Fastify"),
    "@nestjs/core": ("backend", "NestJS"),
    "hono": ("backend", "Hono"),
    "@strapi/strapi": ("backend", "Strapi"),
    "koa": ("backend", "Koa"),
    "fastapi": ("backend", "FastAPI"),
    "django": ("backend", "Django"),
    "flask": ("backend", "Flask"),
    "gin-gonic/gin": ("backend", "Gin"),
    "labstack/echo": ("backend", "Echo"),
}

ORM_HINTS = {
    "prisma": "Prisma",
    "@prisma/client": "Prisma",
    "drizzle-orm": "Drizzle",
    "typeorm": "TypeORM",
    "sequelize": "Sequelize",
    "mongoose": "Mongoose",
    "knex": "Knex",
    "sqlalchemy": "SQLAlchemy",
    "gorm.io/gorm": "GORM",
    "drift": "Drift",
}

DB_HINTS = {
    "pg": "PostgreSQL",
    "postgres": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "psycopg": "PostgreSQL",
    "mysql2": "MySQL",
    "mysql": "MySQL",
    "better-sqlite3": "SQLite",
    "sqlite3": "SQLite",
    "mongodb": "MongoDB",
    "@supabase/supabase-js": "Supabase (PostgreSQL)",
    "supabase": "Supabase (PostgreSQL)",
    "firebase-admin": "Firestore",
}

RE_PY_DEP = re.compile(r"^\s*[\"']([A-Za-z0-9_.\-]+)")
RE_YAML_DEP = re.compile(r"^  ([A-Za-z0-9_]+):")
# ブロック形式（行頭がタブ）と単一行形式（`require x v1.0`）の両方
RE_GO_DEP = re.compile(r"^\s*(?:require\s+)?([a-z0-9.\-]+\.[a-z]{2,}/[^\s]+)\s+v")


RE_POETRY_DEP = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*=")


def _read_pyproject(text):
    """PEP 621 の `[project] dependencies` と Poetry の両方を見る。

    Poetry は `[tool.poetry.dependencies]` の下に `名前 = "^1.2"` の形で並ぶ。
    PEP 621 は `dependencies = ["名前>=1.2", ...]` の配列。書式が違う。
    """
    deps = set()
    in_pep621 = False
    in_poetry = False

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("["):
            in_pep621 = False
            in_poetry = stripped.startswith("[tool.poetry.dependencies]") or \
                stripped.startswith("[tool.poetry.group.") or \
                stripped.startswith("[tool.poetry.dev-dependencies]")
            continue

        if in_poetry:
            m = RE_POETRY_DEP.match(line)
            if m and m.group(1).lower() != "python":
                deps.add(m.group(1).lower())
            continue

        if stripped.startswith("dependencies"):
            in_pep621 = True
            continue

        if in_pep621:
            if stripped.startswith("]"):
                in_pep621 = False
                continue
            m = RE_PY_DEP.match(line)
            if m:
                deps.add(m.group(1).lower())

    return deps


def _read_pubspec(text):
    """dependencies / dev_dependencies 直下の 2 スペースインデントを拾う。"""
    deps = set()
    in_deps = False
    for line in text.splitlines():
        if line.startswith(("dependencies:", "dev_dependencies:")):
            in_deps = True
            continue
        if line and not line.startswith(" "):
            in_deps = False
            continue
        if in_deps:
            m = RE_YAML_DEP.match(line)
            if m:
                deps.add(m.group(1))
    return deps


def _read_go_mod(text):
    """require ブロックのモジュールパスから、ホスト名を除いた部分も拾う。"""
    deps = set()
    for line in text.splitlines():
        m = RE_GO_DEP.match(line)
        if m:
            path = m.group(1)
            parts = path.split("/", 1)
            deps.add(parts[1] if len(parts) > 1 else path)
            deps.add(path)
    return deps


RE_GEM = re.compile(r"^\s*gem\s+[\"\']([A-Za-z0-9_.\-]+)[\"\']")


def _read_gemfile(text):
    """Gemfile の `gem "名前"` を拾う。"""
    return {m.group(1) for m in (RE_GEM.match(ln) for ln in text.splitlines()) if m}


def read_dependencies(manifest_path):
    """manifest から依存名の集合を返す。読めなければ空集合。

    package.json は dependencies と devDependencies の両方を見る。
    devDependencies にしか無いもの（prisma CLI など）も判定材料になるため。
    """
    if not os.path.isfile(manifest_path):
        return set()
    name = os.path.basename(manifest_path)
    try:
        with open(manifest_path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return set()

    if name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return set()
        if not isinstance(data, dict):
            return set()
        deps = set()
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            section = data.get(key)
            # 壊れた manifest や配列形式でも落ちないようにする
            if isinstance(section, dict):
                deps.update(section.keys())
        return deps

    if name == "pyproject.toml":
        return _read_pyproject(text)
    if name == "pubspec.yaml":
        return _read_pubspec(text)
    if name == "go.mod":
        return _read_go_mod(text)
    if name == "Gemfile":
        return _read_gemfile(text)

    return set()


def detect_stack(deps):
    """依存名の集合から、フロント / バック / ORM / DB を判定する。"""
    stack = {"frontend": set(), "backend": set(), "orm": set(), "db": set()}
    for dep in deps:
        if dep in FRAMEWORK_HINTS:
            kind, label = FRAMEWORK_HINTS[dep]
            stack[kind].add(label)
        if dep in ORM_HINTS:
            stack["orm"].add(ORM_HINTS[dep])
        if dep in DB_HINTS:
            stack["db"].add(DB_HINTS[dep])
    return {k: sorted(v) for k, v in stack.items()}


SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", ".turbo", "coverage",
    ".dart_tool", "vendor", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    "ios", "android", ".gradle", "Pods", ".expo", "out",
}

MANIFEST_NAMES = ("package.json", "pyproject.toml", "pubspec.yaml", "go.mod", "Gemfile")

# 種別 → パターン。リポジトリ相対パスに `match_path` で照合する。
#
# 先頭の `*/` は「0 個以上のディレクトリ」を意味する。それ以外の `*` は
# 1 セグメント内だけに効く。`app/*/page.tsx` は 1 階層だけに一致し、
# `app/a/b/c/page.tsx` には一致しない。
CANDIDATE_PATTERNS = {
    "schema": [
        "*/schema.prisma",
        "*/migrations/*.sql", "*/migrations/*/*.sql",
        "*/schema.sql",
        "*/models.py", "*/models/*.py",
        "*/entities/*.ts",
        "*/content-types/*/schema.json",
        # Drizzle は schema.ts の置き場がプロジェクトごとに違う
        "*/schema.ts", "*/schema/*.ts", "*/*.schema.ts",
        "*/drizzle/*.ts",
    ],
    "route": [
        "*/routes/*", "*/routes/*/*",
        "*/controllers/*", "*/controllers/*/*",
        "*/api/*/route.ts", "*/api/*/*/route.ts", "*/api/*/*/*/route.ts",
        "*/pages/api/*", "*/pages/api/*/*",
        "*/handlers/*", "*/endpoints/*", "*/resolvers/*",
        # Supabase / Netlify などの Edge Function も利用者から見える入口
        "*/functions/*/index.ts", "*/functions/*/*.ts",
    ],
    "screen": [
        # App Router。app/ 直下のトップページも画面なので忘れない
        "*/app/page.tsx",
        "*/app/*/page.tsx", "*/app/*/*/page.tsx", "*/app/*/*/*/page.tsx",
        "*/pages/*.tsx",
        "*/screens/*", "*/screens/*/*",
        "*/views/*.vue", "*/views/*.tsx",
        "*/lib/screens/*.dart", "*/lib/pages/*.dart",
    ],
    "integration": [
        "*/gateways/*", "*/clients/*", "*/integrations/*", "*/webhooks/*",
        "*/adapters/*", "*/external/*", "*/providers/*",
        # 名前に webhook を含むディレクトリ。Edge Function として置かれることが多い。
        # route にも入るが、それでよい。入口であり連携でもある
        "*/*webhook*/*",
    ],
}

# 候補から外す拡張子。テストと型定義だけのファイルは入口ではない。
CANDIDATE_SKIP_SUFFIX = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".d.ts", ".snap")

# Expo Router は app/ 配下の .tsx がそのまま画面になる（page.tsx 規約ではない）。
# スタック判定で Expo / React Native と分かったときだけこのパターンを足す。
EXPO_SCREEN_PATTERNS = [
    "*/app/*.tsx",
    "*/app/*/*.tsx",
    "*/app/*/*/*.tsx",
]

# 画面ではないファイル名。レイアウト・状態表示は画面一覧に載せない。
NOT_A_SCREEN = {
    "_layout.tsx", "layout.tsx", "loading.tsx", "error.tsx", "template.tsx",
    "not-found.tsx", "+not-found.tsx", "+html.tsx", "default.tsx", "global-error.tsx",
}


def match_path(rel, pattern):
    """リポジトリ相対パスをパターンに照合する。

    `fnmatch` は `*` が `/` をまたぐため、`app/*/page.tsx` が
    `app/a/b/c/page.tsx` にも一致してしまい、深さ別のパターンが意味をなさない。
    ここではセグメント単位で照合し、`*` は 1 セグメント内だけに効かせる。

    先頭の `*/` だけは「0 個以上のディレクトリ」として扱う。
    `*/routes/*` をリポジトリ直下の `routes/orders.ts` にも当てるため。
    """
    pat_parts = pattern.split("/")
    rel_parts = rel.split("/")

    if pat_parts and pat_parts[0] == "*":
        rest = pat_parts[1:]
        # 0 個以上のディレクトリを読み飛ばして、残りが一致する位置を探す
        for start in range(0, len(rel_parts) - len(rest) + 1):
            if _match_segments(rel_parts[start:], rest):
                return True
        return False

    return _match_segments(rel_parts, pat_parts)


def _match_segments(rel_parts, pat_parts):
    """セグメント数が一致し、各セグメントが fnmatch で一致するか。"""
    if len(rel_parts) != len(pat_parts):
        return False
    return all(
        fnmatch.fnmatch(r, p) for r, p in zip(rel_parts, pat_parts)
    )


def _walk_files(repo):
    """リポジトリ相対パスを列挙する。

    除外ディレクトリには降りない。認証情報が入りうるファイルとディレクトリは
    `_secure` の判定で最初から候補に出さない。
    """
    for root, dirs, files in os.walk(repo):
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and d.lower() not in _secure.SECRET_DIRS
        ]
        for name in files:
            full = os.path.join(root, name)
            if _secure.is_unsafe_to_open(full):
                continue
            yield os.path.relpath(full, repo).replace(os.sep, "/")


def find_manifests(repo):
    """リポジトリ内の manifest をリポジトリ相対パスで返す。"""
    found = [p for p in _walk_files(repo) if os.path.basename(p) in MANIFEST_NAMES]
    return sorted(found)


def find_candidates(repo, stack=None):
    """種別ごとに、読むべきファイルの候補をリポジトリ相対パスで返す。

    stack を渡すと、フレームワークごとの追加パターンを適用する。
    Expo Router は画面の規約が Next.js と違うため、判定結果で切り替える必要がある。
    """
    patterns_by_kind = {k: list(v) for k, v in CANDIDATE_PATTERNS.items()}
    if stack and any(
        name in ("Expo", "React Native") for name in stack.get("frontend", [])
    ):
        patterns_by_kind["screen"].extend(EXPO_SCREEN_PATTERNS)

    result = {kind: set() for kind in patterns_by_kind}
    for rel in _walk_files(repo):
        if rel.endswith(CANDIDATE_SKIP_SUFFIX):
            continue
        basename = os.path.basename(rel)
        for kind, patterns in patterns_by_kind.items():
            if kind == "screen" and basename in NOT_A_SCREEN:
                continue
            for pat in patterns:
                if match_path(rel, pat):
                    result[kind].add(rel)
                    break
    return {k: sorted(v) for k, v in result.items()}


def survey(repos):
    """リポジトリごとに、スタック判定と候補列挙をまとめる。"""
    out = []
    for repo in repos:
        if not os.path.isdir(repo):
            print(f"error: リポジトリが見つかりません: {repo}", file=sys.stderr)
            raise SystemExit(2)
        manifests = find_manifests(repo)
        deps = set()
        for m in manifests:
            deps |= read_dependencies(os.path.join(repo, m))
        stack = detect_stack(deps)
        candidates = find_candidates(repo, stack)
        out.append({
            "path": os.path.abspath(repo),
            "stack": stack,
            "manifests": manifests,
            "candidates": candidates,
            "counts": {k: len(v) for k, v in candidates.items()},
        })
    return {"repos": out}


def _print_human(result):
    for repo in result["repos"]:
        print(f"REPO {repo['path']}")
        stack = repo["stack"]
        for key in ("frontend", "backend", "orm", "db"):
            value = ", ".join(stack[key]) if stack[key] else "判定できず"
            print(f"  {key:<9} {value}")
        print(f"  manifests {', '.join(repo['manifests']) or 'なし'}")
        for kind, count in repo["counts"].items():
            print(f"  {kind:<9} {count} 件")
        print()


def main():
    ap = argparse.ArgumentParser(description="スタック判定と候補列挙")
    ap.add_argument("--repo", action="append", required=True, help="対象リポジトリ（複数可）")
    ap.add_argument("--json", action="store_true", help="JSON で出力する")
    args = ap.parse_args()

    result = survey(args.repo)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
