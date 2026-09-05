# source-docs 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ソースコードから DB 定義・API 仕様・画面遷移・外部連携の資料を生成する Claude Code スキル `source-docs` を実装し、3 プロジェクトで検証して GitHub へ公開する。

**Architecture:** 決定的なスクリプト（`inventory.py`）が探索の足場を作り、Claude が実装を読んで Markdown を書き、`md2html.py` が HTML へ変換する。中間表現ファイルは持たない。フレームワーク固有の知識は `references/stacks/` に分離し、後から追加できる形にする。

**Tech Stack:** Python 3（標準ライブラリのみ）/ Markdown / Mermaid / Claude Code スキル機構

**Spec:** `docs/design.md`（図とサンプルは `docs/design-overview.html` と `docs/output-sample.html`）

## Global Constraints

- **追加依存を作らない。** Python 3 の標準ライブラリのみ。`pip install` を利用者に要求しない
- **テストは `unittest`。** 実行は `python3 -m unittest discover -s tests -v`
- **生成する資料の言語は日本語。** スクリプトの docstring とコメントも日本語
- **出力 md の見出しは `###` まで。** 将来の Notion 変換で書き直しが出ない範囲の制約
- **SKILL.md は 250 行以内。** 常時コンテキストに載るため
- **コミットは Conventional Commits。** `feat:` / `fix:` / `chore:` / `docs:` / `test:`
- **スキル名は `source-docs`。** インストール先は `~/.claude/skills/source-docs/`
- **機密を書かない。** 環境変数は名前と所在まで。値・トークン・接続文字列は載せない

---

## ファイル構成

作るもの / 変えるもの。

| パス | 責務 |
| --- | --- |
| `skill/SKILL.md` | ワークフローとゲート。固有知識は持たない（新規・旧 SKILL.md を置き換え） |
| `skill/references/detect.md` | スタック判定の手順 |
| `skill/references/db.md` | DB をどう読むか |
| `skill/references/api.md` | API をどう読むか |
| `skill/references/screens.md` | 画面と遷移をどう読むか |
| `skill/references/integrations.md` | 外部連携をどう読むか |
| `skill/references/writing.md` | 章立て・図・文体・書かないこと |
| `skill/references/stacks/*.md` | フレームワーク固有の読み方。検証で必要になったものだけ作る |
| `skill/templates/*.md` | 資料 5 本の章の定義 |
| `skill/scripts/inventory.py` | スタック判定と候補列挙（新規） |
| `skill/scripts/verify.py` | 識別子の実在照合（既存を調整） |
| `skill/scripts/secrets.py` | 機密の混入検出（既存をそのまま流用） |
| `skill/scripts/md2html.py` | md → 複数ページ HTML（新規） |
| `tests/` | `unittest` のテスト一式 |
| `tests/fixtures/` | 判定と列挙を試すための小さなダミープロジェクト |
| `README.md` | インストール手順・使い方・限界（書き換え） |
| `LICENSE` | MIT |

削除するもの: `skill/template.md`（`skill/templates/` へ分割するため）。

---

### Task 1: リポジトリの骨格とテスト基盤

**Files:**
- Create: `tests/__init__.py`, `tests/test_smoke.py`
- Create: `tests/fixtures/next-prisma/package.json`
- Create: `tests/fixtures/next-prisma/prisma/schema.prisma`
- Create: `tests/fixtures/next-prisma/app/books/page.tsx`
- Create: `tests/fixtures/next-prisma/src/routes/orders.ts`
- Create: `tests/fixtures/next-prisma/src/gateways/payment.ts`
- Create: `tests/fixtures/strapi/package.json`
- Create: `tests/fixtures/strapi/src/api/book/content-types/book/schema.json`
- Create: `LICENSE`
- Delete: `skill/template.md`
- Create: `skill/references/stacks/.gitkeep`, `skill/templates/.gitkeep`

**Interfaces:**
- Consumes: なし
- Produces: `tests/fixtures/next-prisma` と `tests/fixtures/strapi` の 2 つの fixture パス。以降のテストはこれを対象に判定・列挙を検証する

- [ ] **Step 1: fixture プロジェクトを作る**

`tests/fixtures/next-prisma/package.json`:

```json
{
  "name": "fixture-next-prisma",
  "dependencies": {
    "next": "15.0.0",
    "react": "19.0.0",
    "@prisma/client": "6.0.0",
    "pg": "8.13.0"
  },
  "devDependencies": {
    "prisma": "6.0.0",
    "typescript": "5.6.0"
  }
}
```

`tests/fixtures/next-prisma/prisma/schema.prisma`:

```prisma
model Book {
  id    String @id @default(uuid())
  isbn  String @unique
  title String
}

model Order {
  id     String @id @default(uuid())
  userId String
  status String @default("pending")
}
```

`tests/fixtures/next-prisma/app/books/page.tsx`:

```tsx
export default function BooksPage() {
  return <div>books</div>;
}
```

`tests/fixtures/next-prisma/src/routes/orders.ts`:

```ts
export const routes = [
  { method: "POST", path: "/api/orders" },
  { method: "GET", path: "/api/orders" },
];
```

`tests/fixtures/next-prisma/src/gateways/payment.ts`:

```ts
export async function createPayment(amount: number) {
  return fetch("https://api.example-pay.test/v1/payments", { method: "POST" });
}
```

`tests/fixtures/strapi/package.json`:

```json
{
  "name": "fixture-strapi",
  "dependencies": {
    "@strapi/strapi": "5.0.0",
    "pg": "8.13.0"
  }
}
```

`tests/fixtures/strapi/src/api/book/content-types/book/schema.json`:

```json
{
  "kind": "collectionType",
  "collectionName": "books",
  "attributes": {
    "title": { "type": "string", "required": true },
    "isbn": { "type": "string", "unique": true }
  }
}
```

- [ ] **Step 2: テストの入れ物を作る**

`tests/__init__.py` は空ファイル。`tests/test_smoke.py`:

```python
"""テスト基盤が動くことだけを確かめる。"""

import os
import unittest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class SmokeTest(unittest.TestCase):
    def test_fixtures_exist(self):
        self.assertTrue(os.path.isdir(os.path.join(FIXTURES, "next-prisma")))
        self.assertTrue(os.path.isdir(os.path.join(FIXTURES, "strapi")))
```

- [ ] **Step 3: テストを走らせて通ることを確認**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS（1 test）

- [ ] **Step 4: 旧 template.md を消し、新しいディレクトリを用意**

```bash
git rm skill/template.md
mkdir -p skill/references/stacks skill/templates
touch skill/references/stacks/.gitkeep skill/templates/.gitkeep
```

- [ ] **Step 5: LICENSE を置く**

MIT ライセンス。著作権者は `ymnk417`、年は 2026。

- [ ] **Step 6: コミット**

```bash
git add -A
git commit -m "chore: テスト基盤と fixture を追加し旧 template.md を撤去"
```

---

### Task 2: inventory.py — スタック判定

**Files:**
- Create: `skill/scripts/inventory.py`
- Create: `tests/test_inventory_stack.py`

**Interfaces:**
- Consumes: Task 1 の fixture
- Produces:
  - `read_dependencies(manifest_path: str) -> set[str]` — manifest から依存名の集合を返す
  - `detect_stack(deps: set[str]) -> dict` — `{"frontend": [str], "backend": [str], "orm": [str], "db": [str]}` を返す。各値は名前のソート済みリスト
  - 定数 `FRAMEWORK_HINTS` / `ORM_HINTS` / `DB_HINTS` — 依存名から表示名への対応表。Task 9 以降で項目を追加する

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_inventory_stack.py`:

```python
"""スタック判定のテスト。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "scripts"))
import inventory  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class ReadDependenciesTest(unittest.TestCase):
    def test_package_json(self):
        path = os.path.join(FIXTURES, "next-prisma", "package.json")
        deps = inventory.read_dependencies(path)
        self.assertIn("next", deps)
        self.assertIn("@prisma/client", deps)
        self.assertIn("typescript", deps)  # devDependencies も含める

    def test_missing_file_returns_empty(self):
        self.assertEqual(inventory.read_dependencies("/no/such/file.json"), set())


class DetectStackTest(unittest.TestCase):
    def test_next_prisma_postgres(self):
        stack = inventory.detect_stack({"next", "react", "@prisma/client", "pg"})
        self.assertEqual(stack["frontend"], ["Next.js", "React"])
        self.assertEqual(stack["orm"], ["Prisma"])
        self.assertEqual(stack["db"], ["PostgreSQL"])
        self.assertEqual(stack["backend"], [])

    def test_strapi(self):
        stack = inventory.detect_stack({"@strapi/strapi", "pg"})
        self.assertEqual(stack["backend"], ["Strapi"])
        self.assertEqual(stack["db"], ["PostgreSQL"])

    def test_expo(self):
        stack = inventory.detect_stack({"expo", "react-native"})
        self.assertEqual(stack["frontend"], ["Expo", "React Native"])

    def test_unknown_deps_produce_empty_lists(self):
        stack = inventory.detect_stack({"lodash"})
        self.assertEqual(stack, {"frontend": [], "backend": [], "orm": [], "db": []})
```

- [ ] **Step 2: テストを走らせて失敗を確認**

Run: `python3 -m unittest tests.test_inventory_stack -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'inventory'`

- [ ] **Step 3: 最小の実装を書く**

`skill/scripts/inventory.py`:

```python
#!/usr/bin/env python3
"""リポジトリを走査して、スタックの判定結果と読むべきファイルの候補を出す。

このスクリプトの出力は「どこを読むか」を決めるための足場であって、
資料の生成元ではない。資料は Claude が実際の実装を読んで書く。

使い方:
    python3 inventory.py --repo <path> [--repo <path>...] [--json]

終了コード:
    0  正常
    2  引数・入出力の誤り
"""

import json
import os

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
```

- [ ] **Step 4: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_inventory_stack -v`
Expected: PASS（5 tests）

- [ ] **Step 5: pyproject / pubspec / go.mod の読み取りを足す**

先にテストを `tests/test_inventory_stack.py` へ追加する。

```python
class ReadDependenciesOtherManifestsTest(unittest.TestCase):
    def test_pyproject(self):
        path = os.path.join(FIXTURES, "py-fastapi", "pyproject.toml")
        deps = inventory.read_dependencies(path)
        self.assertIn("fastapi", deps)
        self.assertIn("sqlalchemy", deps)

    def test_pubspec(self):
        path = os.path.join(FIXTURES, "flutter-app", "pubspec.yaml")
        deps = inventory.read_dependencies(path)
        self.assertIn("flutter", deps)
        self.assertIn("drift", deps)

    def test_go_mod(self):
        path = os.path.join(FIXTURES, "go-gin", "go.mod")
        deps = inventory.read_dependencies(path)
        self.assertIn("gin-gonic/gin", deps)
```

fixture を 3 つ作る。

`tests/fixtures/py-fastapi/pyproject.toml`:

```toml
[project]
name = "fixture-fastapi"
dependencies = [
    "fastapi>=0.115",
    "sqlalchemy>=2.0",
    "psycopg[binary]>=3.2",
]
```

`tests/fixtures/flutter-app/pubspec.yaml`:

```yaml
name: fixture_flutter
dependencies:
  flutter:
    sdk: flutter
  drift: ^2.20.0
  go_router: ^14.0.0
```

`tests/fixtures/go-gin/go.mod`:

```
module example.com/fixture

go 1.23

require (
	github.com/gin-gonic/gin v1.10.0
	gorm.io/gorm v1.25.0
)
```

実装を `read_dependencies` に足す。正規表現で名前だけ拾う。バージョン指定子は落とす。

```python
import re

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
    """require ブロックのモジュールパスから、ホスト名を除いた部分を拾う。"""
    deps = set()
    for line in text.splitlines():
        m = RE_GO_DEP.match(line)
        if m:
            path = m.group(1)
            parts = path.split("/", 1)
            deps.add(parts[1] if len(parts) > 1 else path)
            deps.add(path)
    return deps
```

`read_dependencies` の末尾を差し替える。

```python
    if name == "pyproject.toml":
        return _read_pyproject(text)
    if name == "pubspec.yaml":
        return _read_pubspec(text)
    if name == "go.mod":
        return _read_go_mod(text)

    return set()
```

`gorm.io/gorm` は `_read_go_mod` が `gorm.io/gorm` をそのまま入れるので `ORM_HINTS` に一致する。`gin-gonic/gin` はホスト名を落とした形が一致する。

- [ ] **Step 6: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_inventory_stack -v`
Expected: PASS（8 tests）

- [ ] **Step 7: コミット**

```bash
git add skill/scripts/inventory.py tests/test_inventory_stack.py tests/fixtures
git commit -m "feat: inventory.py にスタック判定を実装"
```

---

### Task 3: inventory.py — 候補列挙と CLI

**Files:**
- Modify: `skill/scripts/inventory.py`
- Create: `tests/test_inventory_candidates.py`

**Interfaces:**
- Consumes: Task 2 の `read_dependencies` / `detect_stack`
- Produces:
  - `find_manifests(repo: str) -> list[str]` — リポジトリ内の manifest の相対パス一覧
  - `find_candidates(repo: str) -> dict` — `{"schema": [str], "route": [str], "screen": [str], "integration": [str]}`。値はリポジトリ相対パスのソート済みリスト
  - `survey(repos: list[str]) -> dict` — CLI が出力する全体の結果。`{"repos": [{"path", "stack", "manifests", "candidates", "counts"}]}`
  - CLI: `python3 inventory.py --repo <path> [--repo ...] [--json]`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_inventory_candidates.py`:

```python
"""候補列挙と CLI のテスト。"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "scripts"))
import inventory  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NEXT = os.path.join(FIXTURES, "next-prisma")
STRAPI = os.path.join(FIXTURES, "strapi")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "skill", "scripts", "inventory.py")


class FindManifestsTest(unittest.TestCase):
    def test_finds_package_json(self):
        self.assertEqual(inventory.find_manifests(NEXT), ["package.json"])


class FindCandidatesTest(unittest.TestCase):
    def test_next_prisma(self):
        c = inventory.find_candidates(NEXT)
        self.assertIn("prisma/schema.prisma", c["schema"])
        self.assertIn("src/routes/orders.ts", c["route"])
        self.assertIn("app/books/page.tsx", c["screen"])
        self.assertIn("src/gateways/payment.ts", c["integration"])

    def test_strapi_content_types(self):
        c = inventory.find_candidates(STRAPI)
        self.assertIn("src/api/book/content-types/book/schema.json", c["schema"])

    def test_skips_node_modules(self):
        c = inventory.find_candidates(NEXT)
        self.assertFalse(any("node_modules" in p for p in c["schema"]))


class SurveyTest(unittest.TestCase):
    def test_counts_match_candidates(self):
        result = inventory.survey([NEXT])
        repo = result["repos"][0]
        self.assertEqual(repo["counts"]["schema"], len(repo["candidates"]["schema"]))
        self.assertEqual(repo["stack"]["orm"], ["Prisma"])


class CliTest(unittest.TestCase):
    def test_json_output(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--repo", NEXT, "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["repos"]), 1)

    def test_human_output_mentions_counts(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--repo", NEXT],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("schema", proc.stdout)

    def test_missing_repo_exits_2(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--repo", "/no/such/dir"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)
```

- [ ] **Step 2: テストを走らせて失敗を確認**

Run: `python3 -m unittest tests.test_inventory_candidates -v`
Expected: FAIL — `AttributeError: module 'inventory' has no attribute 'find_manifests'`

- [ ] **Step 3: 実装を書く**

`skill/scripts/inventory.py` に追記する。

```python
import argparse
import fnmatch
import sys

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", ".turbo", "coverage",
    ".dart_tool", "vendor", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    "ios", "android", ".gradle", "Pods", ".expo", "out",
}

MANIFEST_NAMES = ("package.json", "pyproject.toml", "pubspec.yaml", "go.mod", "Gemfile")

# 種別 → glob パターン。リポジトリ相対パスに対して照合する。
CANDIDATE_PATTERNS = {
    "schema": [
        "*schema.prisma", "*/migrations/*.sql", "*/migrations/*/*.sql",
        "*/schema.sql", "*/models.py", "*/models/*.py", "*/entities/*.ts",
        "*/content-types/*/schema.json", "*/schema/*.ts", "*.schema.ts",
        "*/db/schema*.ts", "*/drizzle/*.ts",
    ],
    "route": [
        "*/routes/*", "*/routes/*/*", "*/controllers/*", "*/controllers/*/*",
        "*/api/*/route.ts", "*/api/*/*/route.ts", "*/pages/api/*",
        "*/pages/api/*/*", "*/handlers/*", "*/endpoints/*", "*/resolvers/*",
    ],
    "screen": [
        "*/app/*/page.tsx", "*/app/*/*/page.tsx", "app/*/page.tsx",
        "app/*/*/page.tsx", "*/pages/*.tsx", "*/screens/*", "*/views/*.vue",
        "*/views/*.tsx", "*/lib/screens/*.dart", "*/lib/pages/*.dart",
    ],
    "integration": [
        "*/gateways/*", "*/clients/*", "*/integrations/*", "*/webhooks/*",
        "*/adapters/*", "*/external/*", "*/providers/*",
    ],
}

# 候補から外す拡張子。テストと型定義だけのファイルは入口ではない。
CANDIDATE_SKIP_SUFFIX = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".d.ts", ".snap")


def _walk_files(repo):
    """リポジトリ相対パスを列挙する。除外ディレクトリは降りない。"""
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            full = os.path.join(root, name)
            yield os.path.relpath(full, repo).replace(os.sep, "/")


def find_manifests(repo):
    """リポジトリ内の manifest をリポジトリ相対パスで返す。"""
    found = [p for p in _walk_files(repo) if os.path.basename(p) in MANIFEST_NAMES]
    return sorted(found)


def find_candidates(repo):
    """種別ごとに、読むべきファイルの候補をリポジトリ相対パスで返す。"""
    result = {kind: set() for kind in CANDIDATE_PATTERNS}
    for rel in _walk_files(repo):
        if rel.endswith(CANDIDATE_SKIP_SUFFIX):
            continue
        for kind, patterns in CANDIDATE_PATTERNS.items():
            for pat in patterns:
                if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch("/" + rel, "/" + pat):
                    result[kind].add(rel)
                    break
    return {k: sorted(v) for k, v in result.items()}


def survey(repos):
    """リポジトリごとに、スタック判定と候補列挙をまとめる。"""
    out = []
    for repo in repos:
        if not os.path.isdir(repo):
            raise SystemExit(f"error: リポジトリが見つかりません: {repo}")
        manifests = find_manifests(repo)
        deps = set()
        for m in manifests:
            deps |= read_dependencies(os.path.join(repo, m))
        candidates = find_candidates(repo)
        out.append({
            "path": os.path.abspath(repo),
            "stack": detect_stack(deps),
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
```

`SystemExit(f"error: ...")` は文字列を伴うと終了コード 1 になる。テストは 2 を期待しているので、`survey` の該当箇所を次に変える。

```python
        if not os.path.isdir(repo):
            print(f"error: リポジトリが見つかりません: {repo}", file=sys.stderr)
            raise SystemExit(2)
```

- [ ] **Step 4: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_inventory_candidates -v`
Expected: PASS（7 tests）

- [ ] **Step 5: 全テストを走らせる**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add skill/scripts/inventory.py tests/test_inventory_candidates.py
git commit -m "feat: inventory.py に候補列挙と CLI を実装"
```

---

### Task 4: verify.py を source-docs の書式に合わせる

**Files:**
- Modify: `skill/scripts/verify.py`
- Create: `tests/test_verify.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `extract_tables(doc: str) -> list[str]` — 表のヘッダーに「テーブル」を含む列と、`### <schema>.<table> — 説明` 形式の見出しの両方から拾う
  - `strip_schema(name: str) -> str` — `public.orders` → `orders`

現状の 3 つの問題を直す。

1. ヘッダー判定が完全一致（`"テーブル" in cells`）なので、`テーブル（CRUD）` という列名を拾えない
2. DB 定義書はテーブルごとに見出しを立てる形式になったが、見出しからテーブル名を拾えない
3. スキーマ修飾（`public.orders`）がソースの `orders` と一致しない

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_verify.py`:

```python
"""verify.py の抽出ロジックのテスト。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "scripts"))
import verify  # noqa: E402


class StripSchemaTest(unittest.TestCase):
    def test_removes_schema_prefix(self):
        self.assertEqual(verify.strip_schema("public.orders"), "orders")

    def test_keeps_plain_name(self):
        self.assertEqual(verify.strip_schema("orders"), "orders")


class ExtractTablesTest(unittest.TestCase):
    def test_header_with_suffix(self):
        doc = """
| 画面 | 操作 | API | テーブル（CRUD） |
| --- | --- | --- | --- |
| カート | 表示 | `GET /api/cart` | cart_items (R) |
"""
        self.assertIn("cart_items", verify.extract_tables(doc))

    def test_heading_form(self):
        doc = "### `public.orders` — 注文\n\n本文\n"
        self.assertIn("public.orders", verify.extract_tables(doc))

    def test_heading_without_schema(self):
        doc = "### `books` — 書籍マスタ\n"
        self.assertIn("books", verify.extract_tables(doc))

    def test_ignores_non_table_heading(self):
        doc = "### 3. テーブル一覧\n"
        self.assertEqual(verify.extract_tables(doc), [])


class EndpointTest(unittest.TestCase):
    def test_extracts_method_and_path(self):
        doc = "| `POST` | `/api/orders` | 注文を確定する |"
        self.assertIn(("POST", "/api/orders"), verify.extract_endpoints(doc))
```

`test_extracts_method_and_path` は現行の `RE_ENDPOINT` がバッククォートで区切られた表セルを跨げるかの確認。跨げない場合は正規表現を直す。

- [ ] **Step 2: テストを走らせて失敗を確認**

Run: `python3 -m unittest tests.test_verify -v`
Expected: FAIL — `AttributeError: module 'verify' has no attribute 'strip_schema'` ほか

- [ ] **Step 3: 実装を直す**

`verify.py` に追加する。

```python
# `### `public.orders` — 注文` / `### public.orders — 注文` の両方を拾う。
# 説明のダッシュ（— または -）が続くものだけをテーブル定義の見出しとみなす。
RE_TABLE_HEADING = re.compile(
    r"^#{2,4}\s+`?([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)`?\s*[—–-]\s+\S"
)


def strip_schema(name):
    """`public.orders` のようなスキーマ修飾を落とす。"""
    return name.split(".")[-1] if "." in name else name
```

`extract_tables` を差し替える。

```python
def extract_tables(doc):
    """テーブル名を 2 つの経路で拾う。

    1. ヘッダーに「テーブル」を含む列を持つ表の、その列
    2. `### public.orders — 注文` 形式の見出し

    見出しは「識別子 + ダッシュ + 説明」の形のものだけを対象にする。
    「### 3. テーブル一覧」のような章見出しを拾わないため。
    """
    tables = []
    in_table = False
    col = None

    for line in doc.splitlines():
        stripped = line.strip()

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
            for i, cell in enumerate(cells):
                if "テーブル" in cell:
                    in_table = True
                    col = i
                    break
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # 区切り行
        if col is None or col >= len(cells):
            continue
        tables.extend(cell_identifiers(cells[col]))

    return sorted(set(tables))
```

`cell_identifiers` は `cart_items (R)` のような CRUD 注記を落とす必要がある。末尾の括弧を除く処理を足す。

```python
def cell_identifiers(cell):
    """表のセルからテーブル名だけを取り出す。

    セルには `orders (C)` や `Device` / `UserDevice` のように CRUD 注記や
    複数名が同居する。バッククォート内を優先して拾い、無ければ注記を落とす。
    """
    names = re.findall(r"`([^`]+)`", cell)
    if not names:
        text = re.sub(r"[（(][^）)]*[）)]", "", cell)
        names = re.split(r"[/、,]", text)
    out = []
    for name in names:
        name = re.sub(r"[（(][^）)]*[）)]", "", name)
        name = name.strip().strip("`").strip()
        if name and name not in {"—", "-", ""}:
            out.append(name)
    return out
```

`main()` の照合部分で、スキーマ修飾を落とした名前でも試す。

```python
    for table in extract_tables(doc):
        checked["table"] += 1
        bare = strip_schema(table)
        camel = "".join(p.capitalize() for p in re.split(r"[_\s]+", bare) if p)
        if bare not in sources and camel not in sources:
            findings.append({
                "kind": "table",
                "value": table,
                "reason": "スキーマ定義にこのテーブル / モデルが存在しない",
            })
```

- [ ] **Step 4: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_verify -v`
Expected: PASS（7 tests）

`EndpointTest` が落ちた場合は `RE_ENDPOINT` を次に直す。バッククォートと表の区切りを跨げるようにする。

```python
RE_ENDPOINT = re.compile(
    r"\b(" + "|".join(HTTP_METHODS) + r")`?\s*\|?\s*`?(/[A-Za-z0-9_\-/{}:\.\$\*]*)"
)
```

- [ ] **Step 5: 全テストを走らせる**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add skill/scripts/verify.py tests/test_verify.py
git commit -m "fix: verify.py を source-docs の表・見出し形式に対応"
```

---

### Task 5: md2html.py — Markdown を複数ページ HTML に変換

**Files:**
- Create: `skill/scripts/md2html.py`
- Create: `tests/test_md2html.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `render_inline(text: str) -> str` — インライン記法（`` `code` ``, `**bold**`, `[text](url)`）を HTML にする
  - `parse_blocks(md: str) -> list[tuple]` — `("heading", level, text)` / `("para", text)` / `("ul", [items])` / `("ol", [items])` / `("table", [rows])` / `("code", lang, text)` / `("quote", text)` を返す
  - `convert(md: str) -> tuple[str, str]` — `(title, body_html)`
  - `write_site(src_dir: str, out_dir: str) -> list[str]` — 生成したファイルのパス一覧
  - CLI: `python3 md2html.py --src <dir> --out <dir>`

Markdown の全仕様は実装しない。**このスキルが出力する書式だけ**を対象にする。見出し（`#`〜`###`）、段落、箇条書き、番号付き、表、コードブロック、Mermaid ブロック、引用、インラインコード、太字、リンク。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_md2html.py`:

```python
"""md2html.py のテスト。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skill", "scripts"))
import md2html  # noqa: E402


class RenderInlineTest(unittest.TestCase):
    def test_code(self):
        self.assertEqual(md2html.render_inline("`x`"), "<code>x</code>")

    def test_bold(self):
        self.assertEqual(md2html.render_inline("**強い**"), "<strong>強い</strong>")

    def test_link(self):
        self.assertEqual(
            md2html.render_inline("[a](b.md)"), '<a href="b.html">a</a>'
        )

    def test_escapes_html(self):
        self.assertEqual(md2html.render_inline("a < b"), "a &lt; b")

    def test_code_content_is_escaped(self):
        self.assertEqual(md2html.render_inline("`<a>`"), "<code>&lt;a&gt;</code>")


class ParseBlocksTest(unittest.TestCase):
    def test_heading(self):
        self.assertEqual(md2html.parse_blocks("## 概要"), [("heading", 2, "概要")])

    def test_table(self):
        md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        blocks = md2html.parse_blocks(md)
        self.assertEqual(blocks[0][0], "table")
        self.assertEqual(blocks[0][1], [["a", "b"], ["1", "2"]])

    def test_mermaid_block(self):
        md = "```mermaid\nflowchart TD\n  A --> B\n```"
        blocks = md2html.parse_blocks(md)
        self.assertEqual(blocks[0][0], "code")
        self.assertEqual(blocks[0][1], "mermaid")

    def test_unordered_list(self):
        blocks = md2html.parse_blocks("- 一\n- 二")
        self.assertEqual(blocks[0], ("ul", ["一", "二"]))


class ConvertTest(unittest.TestCase):
    def test_title_from_first_h1(self):
        title, body = md2html.convert("# DB 定義\n\n本文")
        self.assertEqual(title, "DB 定義")
        self.assertIn("<p>本文</p>", body)

    def test_mermaid_becomes_pre_mermaid(self):
        _, body = md2html.convert("```mermaid\nflowchart TD\n```")
        self.assertIn('<pre class="mermaid">', body)

    def test_table_scrolls(self):
        _, body = md2html.convert("| a |\n| --- |\n| 1 |")
        self.assertIn('class="scroll"', body)


class WriteSiteTest(unittest.TestCase):
    def test_creates_index_and_pages(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            with open(os.path.join(src, "database.md"), "w", encoding="utf-8") as f:
                f.write("# DB 定義\n\n本文\n")
            with open(os.path.join(src, "api.md"), "w", encoding="utf-8") as f:
                f.write("# API 仕様\n\n本文\n")

            written = md2html.write_site(src, out)

            self.assertTrue(os.path.isfile(os.path.join(out, "index.html")))
            self.assertTrue(os.path.isfile(os.path.join(out, "database.html")))
            self.assertTrue(os.path.isfile(os.path.join(out, "style.css")))
            self.assertEqual(len(written), 4)

            with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
                index = f.read()
            self.assertIn("DB 定義", index)
            self.assertIn('href="api.html"', index)
```

- [ ] **Step 2: テストを走らせて失敗を確認**

Run: `python3 -m unittest tests.test_md2html -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'md2html'`

- [ ] **Step 3: 実装を書く**

`skill/scripts/md2html.py`:

```python
#!/usr/bin/env python3
"""source-docs が出力した Markdown を複数ページの HTML に変換する。

Markdown の全仕様は実装しない。このスキルが出力する書式だけを対象にする。
見出し（# 〜 ###）／段落／箇条書き／番号付き／表／コードブロック／
Mermaid ブロック／引用／インラインコード／太字／リンク。

Mermaid は CDN の mermaid.js でブラウザ描画する。ネットに繋がらない環境では
図が出ないが、テキストは読める。

使い方:
    python3 md2html.py --src <md のあるディレクトリ> --out <出力先>

終了コード:
    0  正常
    2  引数・入出力の誤り
"""

import argparse
import html
import os
import re
import sys

MERMAID_CDN = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js"

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
RE_FENCE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
RE_UL = re.compile(r"^[-*]\s+(.*)$")
RE_OL = re.compile(r"^\d+\.\s+(.*)$")
RE_QUOTE = re.compile(r"^>\s?(.*)$")
RE_TABLE_DIVIDER = re.compile(r"^\|[\s:|-]+\|$")

RE_INLINE_CODE = re.compile(r"`([^`]+)`")
RE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def render_inline(text):
    """インライン記法を HTML にする。コードの中身も含めてエスケープする。

    先にコードを取り出して退避し、残りをエスケープしてから戻す。
    そうしないとコード内の < > がタグとして扱われる。
    """
    slots = []

    def stash_code(m):
        slots.append(html.escape(m.group(1)))
        return f"\x00{len(slots) - 1}\x00"

    text = RE_INLINE_CODE.sub(stash_code, text)
    text = html.escape(text)
    text = RE_BOLD.sub(r"<strong>\1</strong>", text)

    def link(m):
        label, href = m.group(1), m.group(2)
        if href.endswith(".md"):
            href = href[:-3] + ".html"
        return f'<a href="{href}">{label}</a>'

    text = RE_LINK.sub(link, text)

    for i, code in enumerate(slots):
        text = text.replace(f"\x00{i}\x00", f"<code>{code}</code>")
    return text


def parse_blocks(md):
    """Markdown をブロックの列に分解する。"""
    blocks = []
    lines = md.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        fence = RE_FENCE.match(line)
        if fence:
            lang = fence.group(1)
            i += 1
            body = []
            while i < n and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス
            blocks.append(("code", lang, "\n".join(body)))
            continue

        heading = RE_HEADING.match(line)
        if heading:
            blocks.append(("heading", len(heading.group(1)), heading.group(2).strip()))
            i += 1
            continue

        if line.strip().startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                raw = lines[i].strip()
                if not RE_TABLE_DIVIDER.match(raw):
                    rows.append([c.strip() for c in raw.strip("|").split("|")])
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue

        if RE_UL.match(line):
            items = []
            while i < n and RE_UL.match(lines[i]):
                items.append(RE_UL.match(lines[i]).group(1))
                i += 1
            blocks.append(("ul", items))
            continue

        if RE_OL.match(line):
            items = []
            while i < n and RE_OL.match(lines[i]):
                items.append(RE_OL.match(lines[i]).group(1))
                i += 1
            blocks.append(("ol", items))
            continue

        if RE_QUOTE.match(line):
            parts = []
            while i < n and RE_QUOTE.match(lines[i]):
                parts.append(RE_QUOTE.match(lines[i]).group(1))
                i += 1
            blocks.append(("quote", " ".join(parts)))
            continue

        if not line.strip():
            i += 1
            continue

        parts = []
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            parts.append(lines[i].strip())
            i += 1
        if parts:
            blocks.append(("para", " ".join(parts)))

    return blocks


def _starts_block(line):
    return bool(
        RE_HEADING.match(line)
        or RE_FENCE.match(line)
        or RE_UL.match(line)
        or RE_OL.match(line)
        or RE_QUOTE.match(line)
        or line.strip().startswith("|")
    )


def render_blocks(blocks):
    """ブロックの列を HTML にする。"""
    out = []
    for block in blocks:
        kind = block[0]

        if kind == "heading":
            level = min(block[1], 6)
            out.append(f"<h{level}>{render_inline(block[2])}</h{level}>")

        elif kind == "para":
            out.append(f"<p>{render_inline(block[1])}</p>")

        elif kind == "ul":
            items = "".join(f"<li>{render_inline(x)}</li>" for x in block[1])
            out.append(f"<ul>{items}</ul>")

        elif kind == "ol":
            items = "".join(f"<li>{render_inline(x)}</li>" for x in block[1])
            out.append(f"<ol>{items}</ol>")

        elif kind == "quote":
            out.append(f"<blockquote>{render_inline(block[1])}</blockquote>")

        elif kind == "code":
            lang, body = block[1], block[2]
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{html.escape(body)}</pre>')
            else:
                cls = f' class="lang-{lang}"' if lang else ""
                out.append(f"<pre><code{cls}>{html.escape(body)}</code></pre>")

        elif kind == "table":
            rows = block[1]
            head = "".join(f"<th>{render_inline(c)}</th>" for c in rows[0])
            body = "".join(
                "<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in row) + "</tr>"
                for row in rows[1:]
            )
            out.append(
                f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>"
            )

    return "\n".join(out)


def convert(md):
    """Markdown を (タイトル, 本文 HTML) にする。タイトルは最初の h1。"""
    blocks = parse_blocks(md)
    title = "無題"
    for block in blocks:
        if block[0] == "heading" and block[1] == 1:
            title = block[2]
            break
    return title, render_blocks(blocks)
```

ページの組み立てと CLI を続けて書く。

```python
STYLE = """\
:root { color-scheme: light dark; --ink:#14181a; --ground:#f4f6f5; --line:#d7ddda;
  --accent:#1c6a57; --surface:#fff; --ink-2:#495356; }
@media (prefers-color-scheme: dark) { :root { --ink:#e3e8e7; --ground:#101416;
  --line:#29312f; --accent:#4fbf9f; --surface:#171c1f; --ink-2:#a5b0b0; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--ground); color:var(--ink); line-height:1.85;
  font-family: "Hiragino Sans","Noto Sans JP",system-ui,sans-serif; }
.wrap { max-width: 900px; margin:0 auto; padding: 40px 24px 80px; }
nav.top { border-bottom:1px solid var(--line); padding-bottom:12px; margin-bottom:32px;
  display:flex; gap:16px; flex-wrap:wrap; font-size:14px; }
nav.top a { color:var(--accent); text-decoration:none; }
h1 { font-size:30px; line-height:1.3; margin:0 0 24px; }
h2 { font-size:22px; margin:40px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
h3 { font-size:17px; margin:28px 0 8px; }
p, li { font-size:15px; }
ul, ol { padding-left:22px; }
code { font-family: ui-monospace,"SFMono-Regular",Consolas,monospace; font-size:.88em;
  background:var(--surface); padding:1px 5px; border-radius:2px; }
pre { background:var(--surface); border:1px solid var(--line); border-radius:3px;
  padding:16px; overflow-x:auto; }
pre code { background:none; padding:0; }
pre.mermaid { text-align:center; }
blockquote { margin:0 0 16px; padding:12px 16px; border-left:2px solid var(--accent);
  background:var(--surface); }
.scroll { overflow-x:auto; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:13.5px; min-width:520px; }
th { text-align:left; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--ink-2); border-bottom:1px solid var(--line); padding:8px 10px 8px 0; }
td { border-bottom:1px solid var(--line); padding:9px 10px 9px 0; vertical-align:top;
  color:var(--ink-2); }
td:first-child { color:var(--ink); }
"""

PAGE = """\
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="wrap">
<nav class="top">{nav}</nav>
{body}
</div>
<script src="{cdn}"></script>
<script>mermaid.initialize({{ startOnLoad: true, securityLevel: "loose" }});</script>
</body>
</html>
"""


def _nav_html(pages, current):
    """ページ間のリンク。現在のページはリンクにしない。"""
    parts = ['<a href="index.html">目次</a>']
    for name, title in pages:
        if name == current:
            parts.append(f"<span>{html.escape(title)}</span>")
        else:
            parts.append(f'<a href="{name}.html">{html.escape(title)}</a>')
    return " ".join(parts)


def write_site(src_dir, out_dir):
    """src_dir の md をすべて変換し、index と style を含めて out_dir に書く。"""
    if not os.path.isdir(src_dir):
        print(f"error: 入力ディレクトリが見つかりません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)

    names = sorted(f[:-3] for f in os.listdir(src_dir) if f.endswith(".md"))
    if not names:
        print(f"error: md ファイルがありません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)

    os.makedirs(out_dir, exist_ok=True)

    parsed = []
    for name in names:
        with open(os.path.join(src_dir, f"{name}.md"), encoding="utf-8") as f:
            title, body = convert(f.read())
        parsed.append((name, title, body))

    pages = [(name, title) for name, title, _ in parsed]
    written = []

    for name, title, body in parsed:
        path = os.path.join(out_dir, f"{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(PAGE.format(
                title=html.escape(title), nav=_nav_html(pages, name),
                body=body, cdn=MERMAID_CDN,
            ))
        written.append(path)

    items = "".join(
        f'<li><a href="{name}.html">{html.escape(title)}</a></li>' for name, title in pages
    )
    index_body = f"<h1>設計資料</h1><ul>{items}</ul>"
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(PAGE.format(
            title="設計資料", nav=_nav_html(pages, None),
            body=index_body, cdn=MERMAID_CDN,
        ))
    written.append(index_path)

    style_path = os.path.join(out_dir, "style.css")
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(STYLE)
    written.append(style_path)

    return written


def main():
    ap = argparse.ArgumentParser(description="md を複数ページ HTML に変換する")
    ap.add_argument("--src", required=True, help="md のあるディレクトリ")
    ap.add_argument("--out", required=True, help="出力先ディレクトリ")
    args = ap.parse_args()

    written = write_site(args.src, args.out)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`WriteSiteTest` は 2 つの md から 4 ファイル（2 ページ + index + style）を期待している。上の実装と一致する。

- [ ] **Step 4: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_md2html -v`
Expected: PASS（12 tests）

- [ ] **Step 5: 全テストを走らせる**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add skill/scripts/md2html.py tests/test_md2html.py
git commit -m "feat: md2html.py で md を複数ページ HTML に変換"
```

---

### Task 6: templates/ — 資料 5 本の章の定義

**Files:**
- Create: `skill/templates/overview.md`
- Create: `skill/templates/database.md`
- Create: `skill/templates/api.md`
- Create: `skill/templates/screens.md`
- Create: `skill/templates/integrations.md`
- Delete: `skill/templates/.gitkeep`
- Create: `tests/test_skill_files.py`

**Interfaces:**
- Consumes: なし
- Produces: 5 つのテンプレートファイル。Task 8 の SKILL.md が名前で参照する

各テンプレートは章の見出しと、その章に何を書くかの指示だけを持つ。**サンプルの文章は `docs/output-sample.html` を正とし、テンプレートには書式ルールだけを書く。**

- [ ] **Step 1: テンプレートの存在を確かめるテストを書く**

`tests/test_skill_files.py`:

```python
"""スキルのファイル構成が壊れていないことを確かめる。"""

import os
import re
import unittest

SKILL = os.path.join(os.path.dirname(__file__), "..", "skill")

TEMPLATES = ["overview", "database", "api", "screens", "integrations"]
REFERENCES = ["detect", "db", "api", "screens", "integrations", "writing"]


class TemplateTest(unittest.TestCase):
    def test_all_templates_exist(self):
        for name in TEMPLATES:
            path = os.path.join(SKILL, "templates", f"{name}.md")
            self.assertTrue(os.path.isfile(path), f"missing: {path}")

    def test_templates_have_headings(self):
        for name in TEMPLATES:
            with open(os.path.join(SKILL, "templates", f"{name}.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertRegex(text, r"^## ", f"{name}.md に章見出しがない")

    def test_no_heading_deeper_than_h3(self):
        """出力 md の見出しは ### まで。テンプレート自身もそれに従う。"""
        for name in TEMPLATES:
            with open(os.path.join(SKILL, "templates", f"{name}.md"), encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if re.match(r"^#{4,}\s", line):
                        self.fail(f"{name}.md:{lineno} 見出しが h4 以上")
```

`test_templates_have_headings` は `re.MULTILINE` が要る。`self.assertRegex(text, r"(?m)^## ")` と書く。

- [ ] **Step 2: テストを走らせて失敗を確認**

Run: `python3 -m unittest tests.test_skill_files -v`
Expected: FAIL — `missing: .../templates/overview.md`

- [ ] **Step 3: テンプレートを 5 本書く**

`skill/templates/database.md` の骨子（他の 4 本も同じ形式で書く）:

```markdown
# DB 定義

生成コミット: `<hash>`

## 1. 概要

このシステムのデータがどう置かれているかを 3〜5 行で説明する。DB の種類、
スキーマの数、テーブルのおおよその本数。読み手が全体の規模を掴めるようにする。

## 2. スキーマ一覧

**スキーマが `public` だけなら、この章ごと省く。** 単一スキーマで修飾を付けるのはノイズ。

| スキーマ | 用途 | テーブル数 | 管理主体 / 定義元 |

管理主体は「このリポジトリ」「外部管理（サービス名）」「手動」のいずれか。
外部管理のスキーマはこちらのマイグレーションで変更できないため、必ず区別する。

## 3. テーブル一覧

スキーマごとにグループ化する。列は テーブル / 用途 / 主な参照元。

## 4. テーブル定義

テーブルごとに 1 見出し。スキーマが複数あるなら `### public.orders — 注文` の形。

カラム表は 6 列固定。

| カラム | 型 | NULL | Default | 制約 | 説明 |

- 説明欄に**単位と業務ルール**を書く。「integer」だけでは円かポイントか分からない
- FK はスキーマ修飾で書く（`FK → auth.users.id`）
- 用途はコードを読んで書く。命名から推測しない。読めなければ 8 章へ送る

表のあとに Index / 参照元 / スキーマ / 実装ファイルを並べる。

## 5. Enum 定義

| 値 | 意味 | その値をセットする処理 |

「その値をセットする処理」を必ず書く。テスト仕様書を書くときここが最も効く。
セットする実装が見つからない値は「未確認」と書き、8 章へ送る。

## 6. ER 図

Mermaid の `erDiagram`。エンティティ名にドットは使えないので、スキーマは
`auth_users` のような接頭辞で表す。`public` には接頭辞を付けない。

- テーブル 15 本以下 → 1 枚にカラム付きで描く
- 16 本以上 → ドメイン単位に分割し、加えて全体図をリレーションのみで 1 枚
- カラムは PK / FK / UK と業務上の主要な列に絞る
- 中間テーブルも省略しない

## 7. ビュー・マテリアライズドビュー

| 名前 | 種別 | 元テーブル | 更新 | 用途 / 実装ファイル |

- **更新のタイミングを必ず書く。** マテビューは「いつのデータか」が仕様
- **利用者に見える経路があるかを書く。** 公開画面が直接参照するなら更新失敗が利用者に見える
- ビューは ER 図に入れない

## 8. 未確認一覧

| 対象 | 分からなかったこと | 調べた範囲 |

「調べた範囲」まで書く。次に読む人が同じ調査を繰り返さずに済む。
```

`overview.md` の章: 1. このシステムは何か / 2. 全体構成図（Mermaid）/ 3. リポジトリと責務 / 4. 技術スタック / 5. 主要な業務フロー / 6. 資料の読み方 / 7. 未確認一覧。

`api.md` の章: 1. 概要 / 2. 共通仕様（認証・エラー形式・ページング）/ 3. エンドポイント一覧 / 4. エンドポイント詳細 / 5. 未確認一覧。

`screens.md` の章: 1. 概要 / 2. 画面一覧 / 3. 画面遷移図 / 4. 画面詳細 / 5. 画面-API-DB 関連 / 6. 未確認一覧。

`integrations.md` の章: 1. 連携先一覧 / 2. 送信 / 3. 受信 / 4. 認証情報の所在 / 5. 障害時の挙動 / 6. 未確認一覧。

各章の指示内容は `docs/output-sample.html` の「この資料の書式ルール」をそのまま移す。

- [ ] **Step 4: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_skill_files -v`
Expected: PASS（3 tests）

- [ ] **Step 5: コミット**

```bash
git rm skill/templates/.gitkeep
git add skill/templates tests/test_skill_files.py
git commit -m "feat: 資料 5 本のテンプレートを追加"
```

---

### Task 7: references/ — 解析手順

**Files:**
- Create: `skill/references/detect.md`
- Create: `skill/references/db.md`
- Create: `skill/references/api.md`
- Create: `skill/references/screens.md`
- Create: `skill/references/integrations.md`
- Create: `skill/references/writing.md`
- Modify: `tests/test_skill_files.py`

**Interfaces:**
- Consumes: なし
- Produces: 6 つの参照ファイル。Task 8 の SKILL.md が名前で参照する

内容は `docs/design.md` の「ソース解析」節を、実行手順の粒度まで具体化したもの。

- [ ] **Step 1: 参照ファイルの存在を確かめるテストを足す**

`tests/test_skill_files.py` に追加する。

```python
class ReferenceTest(unittest.TestCase):
    def test_all_references_exist(self):
        for name in REFERENCES:
            path = os.path.join(SKILL, "references", f"{name}.md")
            self.assertTrue(os.path.isfile(path), f"missing: {path}")

    def test_stacks_dir_exists(self):
        self.assertTrue(os.path.isdir(os.path.join(SKILL, "references", "stacks")))
```

- [ ] **Step 2: テストを走らせて失敗を確認**

Run: `python3 -m unittest tests.test_skill_files -v`
Expected: FAIL — `missing: .../references/detect.md`

- [ ] **Step 3: 6 本を書く**

`detect.md` — 00 でだけ読む。

```markdown
# スタック判定

`inventory.py` の判定は依存名の一致だけで行う。**判定結果を鵜呑みにしない。**
以下を自分で確かめてから 01 へ進む。

## 確かめること

1. `inventory.py --repo <path>` を実行し、frontend / backend / orm / db を見る
2. 「判定できず」が出た区分は、manifest を直接開いて依存を読む
3. モノレポなら、ワークスペースごとに区分が違う。リポジトリ単位ではなく
   パッケージ単位で判定する
4. `references/stacks/` に該当するファイルがあれば読む。無ければ汎用手順で進み、
   **読み取れなかった書き方は 09 で `stacks/` に書き足す**

## 判定できないときにやること

判定を諦めて汎用手順で進む。推測でフレームワーク名を書かない。
ゲート 1 で「判定できなかった」と人間に伝える。
```

`db.md` / `api.md` / `screens.md` / `integrations.md` — `docs/design.md` の
「ソース解析」の各節を、探す順序・優先順位・判定できないときの扱いまで具体化する。

`writing.md` — 全資料共通の書き方。

```markdown
# 書き方

全資料に共通する規則。テンプレートと矛盾したらテンプレートが勝つ。

## 必ず守ること

- 言語は日本語
- 見出しは `###` まで
- 図は Mermaid。ASCII 箱型図は日本語混じりで崩れるので使わない
- 相対パス・ファイル名はインラインコードで書く。裸で書くと自動リンク化される
- 各行に実装ファイルが辿れること。ファイル単位まで。クラス名・関数名は追わない
- 判定できなかったものは「未確認」と書き、末尾の未確認一覧へ送る。「調べた範囲」も書く
- 冒頭に生成時点のコミットハッシュを記録する（将来の差分更新の前提）

## 書かないこと

- 推測で埋めた仕様。書く場合は「推測」と明示し、根拠にした箇所を併記する
- 検討の経緯や却下案のうち、docs にもコードコメントにも記録が無いもの
- 機密。環境変数は名前と所在まで。値・トークン・接続文字列は書かない

## 分量

削って後悔するより、書きすぎて指摘を受ける方向に倒す。
ただし**情報量を増やすこと自体を目的にしない。** 読み手が構造を掴めることが目的。
```

- [ ] **Step 4: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_skill_files -v`
Expected: PASS（5 tests）

- [ ] **Step 5: コミット**

```bash
git add skill/references tests/test_skill_files.py
git commit -m "feat: 解析手順の参照ファイルを追加"
```

---

### Task 8: SKILL.md

**Files:**
- Modify: `skill/SKILL.md`（旧 design-doc の内容を全面的に置き換える）
- Modify: `tests/test_skill_files.py`

**Interfaces:**
- Consumes: Task 6 の templates、Task 7 の references、Task 2〜5 のスクリプト
- Produces: スキル本体。`~/.claude/skills/source-docs/SKILL.md` として配置される

- [ ] **Step 1: SKILL.md の制約をテストにする**

`tests/test_skill_files.py` に追加する。

```python
class SkillMdTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            self.text = f.read()

    def test_has_frontmatter_with_name(self):
        self.assertTrue(self.text.startswith("---\n"))
        self.assertIn("name: source-docs", self.text)

    def test_under_line_limit(self):
        """常時コンテキストに載るため 250 行以内に収める。"""
        self.assertLessEqual(len(self.text.splitlines()), 250)

    def test_referenced_files_exist(self):
        """SKILL.md が名前を挙げる references / templates / scripts が実在すること。"""
        for match in re.findall(r"`(references/[\w./-]+|templates/[\w./-]+|scripts/[\w./-]+)`", self.text):
            path = os.path.join(SKILL, match)
            if match.endswith("/"):
                continue
            self.assertTrue(
                os.path.exists(path), f"SKILL.md が参照する {match} が存在しない"
            )

    def test_does_not_hardcode_personal_paths(self):
        """個人環境のパスを使用例に書かない。"""
        self.assertNotIn("ymnk417", self.text)
        self.assertNotIn("poke-dex-battle", self.text)
```

- [ ] **Step 2: テストを走らせて失敗を確認**

Run: `python3 -m unittest tests.test_skill_files -v`
Expected: FAIL — `name: source-docs` が無い（旧 SKILL.md は `name: design-doc`）

- [ ] **Step 3: SKILL.md を書く**

frontmatter:

```markdown
---
name: source-docs
description: 既存プロジェクトのソースコードを読み、初見の開発者がシステム構造を理解するための技術資料（システム概要・DB 定義・ER 図・API 仕様・画面一覧・画面遷移図・画面とAPI/DBの関連・外部連携）を生成する。「設計書を作って」「このプロジェクトの構造を知りたい」「DB 定義書を出して」「API 仕様書を作って」「/source-docs」で起動。Markdown を正本に出力し、HTML へも変換できる。ソースから確認できないことは書かず、未確認として残す。既存プロジェクトの資料化・構造把握・設計書作成の話が出たら必ずこのスキルを使う。
---
```

本文の構成（250 行以内）:

1. 冒頭 — 何を作るか、誰のためか、最重要要件（確認できないことを書かない）
2. コマンド — `/source-docs [target] [--format md|html|both] [--out <dir>] [--repo <path>]` と既定値、`target` の取りうる値
3. 生成する資料 — 5 ファイル / 8 資料の対応表
4. 00 スタック判定 — `scripts/inventory.py` の実行と `references/detect.md`
5. 01 列挙 — 候補と件数
6. **ゲート 1** — 判定結果と件数を提示し、対象範囲を人間が確定する。判定できなかった区分は必ず伝える
7. 02 1 資料目 — `templates/` と `references/` を読んで書き、`scripts/verify.py` で照合。不一致は 1 回だけ再生成、2 回目は未確認として残す
8. **ゲート 2** — 1 本目を人間が読む。指摘は文章ではなく規則として記録し、以降に効かせる
9. 03 残り資料 — 同じ手順
10. 04 HTML 変換 — `scripts/md2html.py`
11. 05 完了報告 — 生成ファイル一覧と未確認一覧。`scripts/secrets.py` の検出は人間に回す
12. 書かないこと
13. このスキルの限界

**書かないもの:** フレームワーク固有の読み方、資料の章立ての詳細、特定プロジェクトのパスや値。

使用例は汎用の形にする。

```
/source-docs
/source-docs db
/source-docs api --format html
/source-docs --format both --out docs/architecture
/source-docs --repo ./frontend --repo ./backend
```

- [ ] **Step 4: テストを走らせて通ることを確認**

Run: `python3 -m unittest tests.test_skill_files -v`
Expected: PASS（9 tests）

- [ ] **Step 5: 全テストを走らせる**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add skill/SKILL.md tests/test_skill_files.py
git commit -m "feat: SKILL.md を source-docs の内容に置き換え"
```

---

### Task 9: poke-dex-battle で実行検証

**Files:**
- Create: `~/.claude/skills/source-docs/`（`skill/` の中身を配置）
- Create: `skill/references/stacks/*.md`（この検証で必要と分かったものだけ）
- Modify: `skill/scripts/inventory.py`（判定漏れがあれば対応表に追加）

**Interfaces:**
- Consumes: Task 2〜8 の成果物すべて
- Produces: 実行して分かった不足を反映したスキル

対象は画面・API・DB が揃っているモノレポ。3 プロジェクトの中で最も条件が良いので最初に使う。

- [ ] **Step 1: スキルをインストールする**

```bash
mkdir -p ~/.claude/skills/source-docs
cp -r skill/. ~/.claude/skills/source-docs/
ls ~/.claude/skills/source-docs
```

- [ ] **Step 2: inventory.py を単体で走らせる**

```bash
python3 ~/.claude/skills/source-docs/scripts/inventory.py \
  --repo ~/projects/poke-dex-battle
```

判定と件数を確認する。`判定できず` が出た区分は manifest を開いて確認し、
対応表に足りない依存名があれば `FRAMEWORK_HINTS` / `ORM_HINTS` / `DB_HINTS` へ追加する。
**追加したら Task 2 のテストにケースを足してから実装を直す。**

- [ ] **Step 3: スキルを起動して db 資料を 1 本生成する**

```
/source-docs db --out /tmp/source-docs-poke
```

ゲート 1 で範囲を確認し、生成された `database.md` を読む。

- [ ] **Step 4: verify.py で照合する**

```bash
python3 ~/.claude/skills/source-docs/scripts/verify.py \
  --doc /tmp/source-docs-poke/database.md \
  --repo ~/projects/poke-dex-battle
```

Expected: `RESULT=OK`、または不一致の内容が妥当（実際に資料が誤っている）こと。
誤検知が出た場合は `verify.py` を直し、Task 4 のテストにケースを足す。

- [ ] **Step 5: 残りの資料を生成して照合する**

```
/source-docs --out /tmp/source-docs-poke --format both
```

生成された 5 ファイルと HTML を確認する。`index.html` をブラウザで開き、
Mermaid が描画されること、表が横スクロールすること、ダークテーマで読めることを見る。

- [ ] **Step 6: secrets.py を全ファイルに通す**

```bash
for f in /tmp/source-docs-poke/*.md; do
  python3 ~/.claude/skills/source-docs/scripts/secrets.py --doc "$f"
done
```

検出が出たら内容を確認する。実際の機密が混ざっていれば資料を直す。

- [ ] **Step 7: 分かったことを stacks へ書く**

汎用手順で読み取れなかった書き方があれば `skill/references/stacks/<name>.md` に書く。
書式は「何が見つけにくいか」「どこを見れば分かるか」の 2 点。

- [ ] **Step 8: 全テストを走らせてコミット**

```bash
python3 -m unittest discover -s tests -v
git add -A
git commit -m "fix: poke-dex-battle での検証結果を反映"
```

---

### Task 10: kids-play-monorepo と oshinote で耐性を見る

**Files:**
- Modify: `skill/references/stacks/*.md`
- Modify: `skill/scripts/inventory.py`（判定漏れがあれば）
- Modify: `skill/references/*.md`（手順の不足があれば）

**Interfaces:**
- Consumes: Task 9 で調整済みのスキル
- Produces: 3 スタックで動くことを確認したスキル

Strapi は DB スキーマが独自形式（`content-types/*/schema.json`）、Expo は
画面が URL ルートではなくナビゲーション定義。どちらも Task 9 とは形が違う。

- [ ] **Step 1: kids-play-monorepo で inventory を走らせる**

```bash
python3 ~/.claude/skills/source-docs/scripts/inventory.py \
  --repo ~/projects/kids-play-monorepo/frontend \
  --repo ~/projects/kids-play-monorepo/backend/strapi-app
```

- [ ] **Step 2: db と api を生成して確認する**

```
/source-docs db --out /tmp/source-docs-kids --repo ~/projects/kids-play-monorepo/backend/strapi-app
```

Strapi の `schema.json` からテーブル定義が読み取れているかを見る。
読み取れていなければ `references/stacks/strapi.md` に読み方を書く。

- [ ] **Step 3: oshinote で screens を生成する**

```
/source-docs screens --out /tmp/source-docs-oshinote --repo ~/projects/oshinote
```

Expo Router のファイルベースルーティングから画面が列挙できているか、
`router.push` などから遷移が辿れているかを見る。
読み取れていなければ `references/stacks/expo-router.md` に書く。

- [ ] **Step 4: 3 プロジェクトの結果を突き合わせる**

同じ手順で 3 つとも資料が出ているか。出ていない資料があれば、
それが「そのプロジェクトに該当機能が無いから」なのか「読み取れなかったから」なのかを
区別する。後者なら手順を直す。

- [ ] **Step 5: 全テストを走らせてコミット**

```bash
python3 -m unittest discover -s tests -v
git add -A
git commit -m "fix: Strapi と Expo での検証結果を反映"
```

---

### Task 11: README を書き換えて GitHub へ公開

**Files:**
- Modify: `README.md`（旧 design-doc の説明を全面的に置き換える）
- Create: GitHub リポジトリ `source-docs`（public）

**Interfaces:**
- Consumes: Task 1〜10 のすべて
- Produces: 公開されたリポジトリ

- [ ] **Step 1: README を書く**

構成:

1. 何をするスキルか（3 行）
2. 生成される資料の一覧（5 ファイル / 8 資料）
3. インストール（`~/.claude/skills/source-docs/` へ `skill/` の中身を置く）
4. 使い方（コマンドと引数、実行例）
5. ワークフロー（00〜05 とゲート 2 か所の表）
6. 依存（Python 3 標準ライブラリのみ。追加インストール不要）
7. **このスキルの限界**（`docs/design.md` の該当節をそのまま）
8. 検証済みのスタック（Task 9・10 で実際に動かしたもの）
9. ライセンス（MIT）

**個人環境のパスを書かない。** 旧 README の課題 9 と同じ轍を踏まない。

- [ ] **Step 2: 最終確認**

```bash
python3 -m unittest discover -s tests -v
git status --short
grep -rn "ymnk417" README.md skill/ || echo "個人パスなし"
```

Expected: 全テスト PASS、作業ツリーがクリーン、個人パスなし。

- [ ] **Step 3: notes/ が除外されていることを確認**

```bash
git ls-files | grep -c "^notes/" || echo "notes/ は追跡されていない"
```

Expected: `notes/ は追跡されていない`

- [ ] **Step 4: GitHub リポジトリを作って push**

```bash
gh repo create source-docs --public --source=. --remote=origin --push
```

`gh` が未認証なら、ユーザーに `! gh auth login` を実行してもらう。

- [ ] **Step 5: 公開されたことを確認**

```bash
gh repo view --web
git log --oneline -1
git remote -v
```

- [ ] **Step 6: 最終コミット（必要なら）**

```bash
git add README.md
git commit -m "docs: README を source-docs の内容に書き換え"
git push
```

---

## 自己レビュー

**1. spec 網羅**

| `docs/design.md` の節 | 対応タスク |
| --- | --- |
| アーキテクチャ / ハイブリッドの分担 | Task 2, 3（スクリプト側）、Task 7, 8（Claude 側の手順） |
| コマンド | Task 8 |
| ワークフロー（00〜05・ゲート 2 か所） | Task 8 |
| 生成する資料（8 本） | Task 6 |
| ソース解析（DB / API / 画面 / 外部連携） | Task 7 |
| 不明情報の扱い | Task 4（照合）、Task 6, 7（書式） |
| コンテキスト管理 | Task 3（件数の提示）、Task 8（分割の指示） |
| ディレクトリ構成 | Task 1, 6, 7, 8 |
| MVP に含む / 含まない | 全タスク。含まないものはタスク化していない |
| 将来への備え（コミットハッシュ・関連表） | Task 6（テンプレートに記載） |
| このスキルの限界 | Task 8（SKILL.md）、Task 11（README） |

**2. プレースホルダ** — なし。Task 6・7 のテンプレートと参照ファイルは、章立てと
書式ルールを本文に列挙してある。文章そのものは実装時に書くが、何を書くかは確定している。

**3. 型の一貫性**

- `inventory.py`: `read_dependencies` → `detect_stack` → `survey` の順に依存。
  `find_candidates` の戻り値のキー（`schema` / `route` / `screen` / `integration`）は
  Task 3 のテストと `_print_human` で一致
- `verify.py`: `strip_schema` は Task 4 で定義し、同 Task の `main()` でのみ使う
- `md2html.py`: `parse_blocks` の返すタプル形式（`("code", lang, body)` は 3 要素、
  他は 2 要素）は `render_blocks` の分岐と一致
