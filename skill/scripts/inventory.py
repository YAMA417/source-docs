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

import json
import os
import re

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
RE_GO_DEP = re.compile(r"^\s+([a-z0-9.\-]+\.[a-z]{2,}/[^\s]+)\s+v")


def _read_pyproject(text):
    """[project] の dependencies と [tool.poetry.dependencies] の両方を見る。"""
    deps = set()
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies"):
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("]") or stripped.startswith("["):
                in_deps = False
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
        deps = set()
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update(data.get(key, {}).keys())
        return deps

    if name == "pyproject.toml":
        return _read_pyproject(text)
    if name == "pubspec.yaml":
        return _read_pubspec(text)
    if name == "go.mod":
        return _read_go_mod(text)

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
