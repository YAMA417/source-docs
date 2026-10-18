"""manifest の読み取りと、技術スタックの表示名。

ここにあるのは「依存名 → 表示名」の対応表（データ）だけ。検出の仕組みは adapters が持つ。
"""

import json
import re

# 依存名 → (区分, 表示名)
FRAMEWORK_HINTS = {
    "next": ("frontend", "Next.js"), "react": ("frontend", "React"), "vue": ("frontend", "Vue"),
    "nuxt": ("frontend", "Nuxt"), "@angular/core": ("frontend", "Angular"),
    "svelte": ("frontend", "Svelte"), "@sveltejs/kit": ("frontend", "SvelteKit"),
    "expo": ("frontend", "Expo"), "expo-router": ("frontend", "Expo Router"),
    "react-native": ("frontend", "React Native"), "flutter": ("frontend", "Flutter"),
    "express": ("backend", "Express"), "fastify": ("backend", "Fastify"),
    "@nestjs/core": ("backend", "NestJS"), "hono": ("backend", "Hono"),
    "@strapi/strapi": ("backend", "Strapi"), "koa": ("backend", "Koa"), "elysia": ("backend", "Elysia"),
    "@trpc/server": ("backend", "tRPC"), "fastapi": ("backend", "FastAPI"),
    "django": ("backend", "Django"), "flask": ("backend", "Flask"),
    "gin-gonic/gin": ("backend", "Gin"), "labstack/echo": ("backend", "Echo"),
    "rails": ("backend", "Ruby on Rails"),
}
ORM_HINTS = {
    "prisma": "Prisma", "@prisma/client": "Prisma", "drizzle-orm": "Drizzle", "typeorm": "TypeORM",
    "sequelize": "Sequelize", "mongoose": "Mongoose", "knex": "Knex", "sqlalchemy": "SQLAlchemy",
    "sqlmodel": "SQLModel", "gorm.io/gorm": "GORM", "drift": "Drift", "activerecord": "ActiveRecord",
}
DB_HINTS = {
    "pg": "PostgreSQL", "postgres": "PostgreSQL", "psycopg2": "PostgreSQL", "psycopg": "PostgreSQL",
    "mysql2": "MySQL", "mysql": "MySQL", "better-sqlite3": "SQLite", "sqlite3": "SQLite",
    "mongodb": "MongoDB", "@supabase/supabase-js": "Supabase (PostgreSQL)",
    "supabase": "Supabase (PostgreSQL)", "firebase-admin": "Firestore", "redis": "Redis",
    "ioredis": "Redis", "bullmq": "Redis (BullMQ)",
}

MANIFEST_NAMES = ("package.json", "pyproject.toml", "requirements.txt", "pubspec.yaml", "go.mod",
                  "Gemfile", "composer.json")

RE_PY_DEP = re.compile(r"^\s*[\"']([A-Za-z0-9_.\-]+)")
RE_POETRY_DEP = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*=")
RE_YAML_DEP = re.compile(r"^  ([A-Za-z0-9_]+):")
RE_GO_DEP = re.compile(r"^\s*(?:require\s+)?([a-z0-9.\-]+\.[a-z]{2,}/[^\s]+)\s+v")
RE_GEM = re.compile(r"^\s*gem\s+[\"']([A-Za-z0-9_.\-]+)[\"']")
RE_REQ = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def read_manifest(name, text):
    """(依存名の集合, パッケージ名)。読めなければ (空集合, None)。"""
    if name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return set(), None
        if not isinstance(data, dict):
            return set(), None
        deps = set()
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            section = data.get(key)
            if isinstance(section, dict):
                deps.update(section.keys())
        return deps, data.get("name")
    if name == "pyproject.toml":
        return _pyproject(text), _toml_name(text)
    if name == "requirements.txt":
        deps = set()
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            m = RE_REQ.match(line)
            if m and not line.startswith("-"):
                deps.add(m.group(1).lower().split("[")[0])
        return deps, None
    if name == "pubspec.yaml":
        return _pubspec(text), _yaml_name(text)
    if name == "go.mod":
        deps = set()
        for line in text.splitlines():
            m = RE_GO_DEP.match(line)
            if m:
                path = m.group(1)
                deps.add(path)
                parts = path.split("/", 1)
                if len(parts) > 1:
                    deps.add(parts[1])
        mod = re.search(r"^module\s+(\S+)", text, re.M)
        return deps, mod.group(1) if mod else None
    if name == "Gemfile":
        return {m.group(1) for m in (RE_GEM.match(ln) for ln in text.splitlines()) if m}, None
    if name == "composer.json":
        try:
            data = json.loads(text)
        except ValueError:
            return set(), None
        return set((data.get("require") or {}).keys()), data.get("name")
    return set(), None


def _pyproject(text):
    deps, in_pep621, in_poetry = set(), False, False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_pep621 = False
            in_poetry = stripped.startswith(("[tool.poetry.dependencies]", "[tool.poetry.group.",
                                             "[tool.poetry.dev-dependencies]"))
            continue
        if in_poetry:
            m = RE_POETRY_DEP.match(line)
            if m and m.group(1).lower() != "python":
                deps.add(m.group(1).lower())
            continue
        if stripped.startswith("dependencies"):
            inline = re.search(r"\[(.*)\]", stripped)
            if inline:    # 1 行で書いた `dependencies = ["fastapi", "sqlmodel"]`
                for name in re.findall(r"[\"']([A-Za-z0-9_.\-]+)", inline.group(1)):
                    deps.add(name.lower())
                continue
            in_pep621 = True
            continue
        if in_pep621:
            if stripped.startswith("]"):
                in_pep621 = False
                continue
            m = RE_PY_DEP.match(line)
            if m:
                deps.add(m.group(1).lower().split("[")[0])
    return deps


def _toml_name(text):
    m = re.search(r"^name\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
    return m.group(1) if m else None


def _pubspec(text):
    deps, in_deps = set(), False
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


def _yaml_name(text):
    m = re.search(r"^name:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def detect_stack(deps):
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


def app_kind(stack):
    mobile = {"Expo", "Expo Router", "React Native", "Flutter"}
    if mobile & set(stack["frontend"]):
        return "mobile"
    if stack["frontend"] and stack["backend"]:
        return "fullstack"
    if stack["frontend"]:
        return "frontend"
    if stack["backend"]:
        return "backend"
    return "unknown"
