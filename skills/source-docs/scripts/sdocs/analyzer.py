"""ソースコードから Project Model を作る。

1. リポジトリを走査し、manifest のあるディレクトリをアプリケーションとみなす
2. アプリケーションごとに、当てはまるアダプタで実体を集める
3. 関係（テーブル操作・API 呼び出し・遷移・外部連携…）を解決する
4. 公開用のモデルへ書き出し、入口から機能の候補を作る
"""

import os

from . import fs
from .adapters import all_adapters
from .adapters.base import AppContext
from .features import candidates
from .model import Builder
from .relations import apply_env_examples, resolve
from .stacks import MANIFEST_NAMES, app_kind, detect_stack, read_manifest


def _repo_keys(paths):
    keys, used = [], set()
    for p in paths:
        base = os.path.basename(os.path.abspath(p)) or "repo"
        key, n = base, 2
        while key in used:
            key = f"{base}-{n}"
            n += 1
        used.add(key)
        keys.append(key)
    return keys


def discover_apps(repo):
    """manifest のあるディレクトリをアプリケーションにする。直下は常にアプリとして持つ。"""
    manifests = {}
    for rel in repo.files:
        name = rel.rsplit("/", 1)[-1]
        if name in MANIFEST_NAMES:
            d = rel.rsplit("/", 1)[0] if "/" in rel else ""
            manifests.setdefault(d, []).append(rel)
    dirs = sorted(set(manifests) | {""}, key=lambda d: (-d.count("/") if d else 1, d))
    files_by_dir = {d: [] for d in dirs}
    for rel in repo.files:
        owner = ""
        for d in dirs:
            if d and (rel == d or rel.startswith(d + "/")):
                owner = d
                break
        files_by_dir[owner].append(rel)

    root_deps = set()
    for m in manifests.get("", []):
        deps, _ = read_manifest(m.rsplit("/", 1)[-1], repo.read(m))
        root_deps |= deps

    apps = []
    for d in sorted(dirs):
        own_deps, name = set(), None
        for m in manifests.get(d, []):
            deps, n = read_manifest(m.rsplit("/", 1)[-1], repo.read(m))
            own_deps |= deps
            name = name or n
        if d and not files_by_dir[d]:
            continue
        app_id = f"app:{repo.key}" + (f"/{d}" if d else "")
        key = d or repo.key
        ctx = AppContext(app_id, key, repo, d, name, own_deps | root_deps,
                         manifests.get(d, []), sorted(files_by_dir[d]))
        ctx.own_deps = own_deps
        apps.append(ctx)
    return apps


def analyze(repo_paths, out_dir):
    out_abs = os.path.abspath(out_dir)
    repos = [fs.Repo(k, p, exclude=[out_abs]) for k, p in zip(_repo_keys(repo_paths), repo_paths)]
    b = Builder()
    apps = []
    for repo in repos:
        b.repositories.append({
            "id": f"repo:{repo.key}", "key": repo.key,
            "path": _relpath(repo.root, out_abs),
            "commit": fs.git_head(repo.root), "files": len(repo.files),
        })
        apps.extend(discover_apps(repo))

    adapters_by_app = {}
    for app in apps:
        app.adapters = [a for a in all_adapters() if a.applies(app)]
        adapters_by_app[app.id] = app.adapters
    for app in apps:
        for a in app.adapters:
            a.collect(app, b)

    resolve(b, apps, adapters_by_app)
    apply_env_examples(b, apps)
    _resolve_foreign_keys(b)

    for app in apps:
        stack = detect_stack(app.own_deps or app.deps)
        counts = _counts(b, app.id)
        b.applications.append({
            "id": app.id, "key": app.key, "repository": f"repo:{app.repo.key}",
            "path": app.root_rel or ".", "name": app.name,
            "manifests": app.manifests, "stack": stack,
            "kind": _kind(counts, stack), "adapters": [a.name for a in app.adapters],
            "counts": counts,
        })

    head = fs.git_head(repos[0].root) if repos else None
    model = b.finalize(head, multi_repo=len(repos) > 1)
    model["features"] = candidates(model)
    model["limits"] = {
        "truncated": [r.key for r in repos if r.truncated],
        "skippedLargeFiles": sorted({f for r in repos for f in r.skipped_large}),
    }
    return model


def _relpath(path, start):
    """出力先から見たリポジトリの位置。別ドライブなど相対にできなければ絶対パス。"""
    try:
        return os.path.relpath(path, start).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def _counts(b, app_id):
    out = {}
    for coll in ("screens", "endpoints", "tables", "jobs", "commands"):
        out[coll] = sum(1 for e in b.items(coll) if e["app"] == app_id)
    return out


def _kind(counts, stack):
    """実際に見つかったものから決める。manifest の依存だけでは決めない。"""
    if counts["screens"] and counts["endpoints"]:
        return "fullstack"
    if counts["screens"]:
        return "mobile" if app_kind(stack) == "mobile" else "frontend"
    if counts["endpoints"] or counts["jobs"]:
        return "backend"
    if counts["tables"]:
        return "database"
    return app_kind(stack)


def _resolve_foreign_keys(b):
    """外部キーの参照先を、ORM の変数名・モデル名からテーブル名へ置き換える。"""
    for t in b.items("tables"):
        for fk in t["foreignKeys"]:
            fk["references"]["table"] = _table_name(b, fk["references"]["table"])
        for c in t["columns"]:
            if c.get("references"):
                c["references"]["table"] = _table_name(b, c["references"]["table"])
        for rel in t.get("relations", []):
            if rel.get("target"):
                rel["targetTable"] = _table_name(b, rel["target"])


def _table_name(b, alias):
    uids = b.table_aliases.get(alias, set())
    if len(uids) == 1:
        item = b.get(next(iter(uids)))
        if item:
            return item["key"]
    return alias


__all__ = ["analyze", "discover_apps"]
