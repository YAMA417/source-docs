"""ソースコードから Project Model を作る。

1. リポジトリを走査し、manifest のあるディレクトリをアプリケーションとみなす
2. アプリケーションごとに、当てはまるアダプタで実体を集める
3. 関係（テーブル操作・API 呼び出し・遷移・外部連携…）を解決する
4. 公開用のモデルへ書き出し、入口から機能の候補を作る
"""

import os

from . import dbstate, fs
from ..detectors import all_adapters
from ..detectors.base import AppContext
from .features import candidates
from .model import Builder
from .relations import apply_env_examples, report_pending, resolve
from ..detectors.stacks import MANIFEST_NAMES, app_kind, detect_stack, read_manifest


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

    # DB の最終状態（migration と ORM の突き合わせ）を先に決める。関係の解決はその後
    dbstate.merge(b)
    resolve(b, apps, adapters_by_app)
    apply_env_examples(b, apps)
    report_pending(b)

    for app in apps:
        stack = detect_stack(app.own_deps or app.deps)
        for kind, labels in b.stack_hints.get(app.id, {}).items():
            stack[kind] = sorted(set(stack[kind]) | labels)
        counts = _counts(b, app.id)
        _report_unread(b, app, stack, counts)
        b.applications.append({
            "id": app.id, "key": app.key, "repository": f"repo:{app.repo.key}",
            "path": app.root_rel or ".", "name": app.name,
            "manifests": app.manifests, "stack": stack,
            "kind": _kind(counts, stack), "adapters": [a.name for a in app.adapters],
            "counts": counts,
        })

    head = fs.git_head(repos[0].root) if repos else None
    model = b.finalize(head, multi_repo=len(repos) > 1)
    model["dataStores"] = _data_stores(model)
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


def _report_unread(b, app, stack, counts):
    """依存から DB・画面があるはずなのに 1 件も読み取れなかったら、解析の対象外として残す。

    「テーブル 0 件」「画面 0 件」を確定した事実として出さないため（読めなかったのか、無いのかを区別する）。
    """
    manifest = (app.manifests or [app.root_rel or "."])[0]
    if stack["orm"] and not any(True for _ in b.items("tables")):
        b.add_unsupported({"id": app.id}, manifest, "tables",
                          f"DB を使う依存（{', '.join(stack['orm'])}）があるのに、テーブルの定義を読み取れなかった")
    # バックエンドの枠組み（Strapi の管理画面の React など）が使う依存は、そのアプリの画面ではない
    if stack["frontend"] and not stack["backend"] and not counts["screens"]:
        b.add_unsupported({"id": app.id}, manifest, "screens",
                          f"画面を作る依存（{', '.join(stack['frontend'])}）があるのに、画面のルーティングを読み取れなかった")


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


def _data_stores(model):
    """アプリごとの DB（Data Store）。テーブルの物理状態をどこから読んだかも持つ。"""
    out = []
    for app in model["applications"]:
        tables = [t for t in model["tables"] if t["app"] == app["id"]]
        if not tables:
            continue
        out.append({
            "id": f"datastore:{app['key']}", "app": app["id"], "engines": app["stack"]["db"],
            "tables": [t["id"] for t in tables],
            "physicalSources": sorted({t.get("physicalSource") for t in tables if t.get("physicalSource")}),
        })
    return out


__all__ = ["analyze", "discover_apps"]
