"""`check` の本体。資料一式をどこまで信用してよいかを診断にまとめる。

調べるもの（それぞれ重大度つきの診断になる）。

| 区分 | 内容 | 重大度 |
| --- | --- | --- |
| model | Project Model が壊れている・版が違う | ERROR |
| annotation | 注釈の誤り（存在しない ID・根拠の無い implemented…） | ERROR |
| annotation | 古い注釈・注釈の無い API | WARNING |
| secret | 注釈・資料（Markdown / JSON / HTML）に認証情報らしき値がある | ERROR |
| conflict | 根拠どうしが矛盾している事実がある | ERROR |
| db / relation | ORM と migration の食い違い、行き先の決まらない関係 | WARNING |
| stale | 資料を作ったあとにコードが変わった（コミット・再解析した結果の差分） | WARNING（--strict で ERROR） |
| coverage | 網羅率が閾値を下回った | WARNING（--strict で ERROR） |

終了コードは ERROR が 1 件でもあれば 1。
"""

import json
import os

from ..core import fs
from ..core.analyzer import analyze
from ..core.model import COLLECTIONS, RELATION_FIELDS, model_dir, model_path, validate_model
from ..core.resolve import resolve
from ..core.status import diagnostic
from . import annotations as ann_mod
from . import coverage as cov_mod
from . import secrets as sec_mod

STALE_KINDS = (("endpoints", "API"), ("screens", "画面"), ("tables", "テーブル"),
               ("integrations", "外部連携"), ("jobs", "ジョブ"), ("events", "イベント"),
               ("entrypoints", "入口"))

THRESHOLDS = (("min_api_coverage", "api", "API"), ("min_screen_coverage", "screens", "画面"),
              ("min_table_coverage", "db", "DB テーブル"))


def run(out_dir, strict=False, thresholds=None, reanalyze=True):
    """診断の列と、それを作るのに使った情報を返す。"""
    thresholds = thresholds or {}
    diags = []
    info = {}
    path = model_path(out_dir)
    if not os.path.isfile(path):
        diags.append(diagnostic("ERROR", "model.missing", f"Project Model が無い。先に analyze を実行する: {path}"))
        return diags, info
    with open(path, encoding="utf-8") as f:
        model = json.load(f)
    errors = validate_model(model)
    if errors:
        for e in errors[:20]:
            diags.append(diagnostic("ERROR", "model.invalid", e))
        return diags, info

    annotations = ann_mod.load(out_dir)
    lint = ann_mod.check(model, annotations, out_dir)
    for e in lint.errors:
        diags.append(diagnostic("ERROR", "annotation.invalid", e))
    for w in lint.warnings:
        diags.append(diagnostic("WARNING", "annotation.warning", w))
    for f in sec_mod.masked(lint.secret_findings):
        diags.append(diagnostic("ERROR", "secret.annotation",
                                f"annotations.json L{f['line']} [{f['label']}] {f['value']}"))

    view = resolve(model, annotations)
    diags.extend(view["diagnostics"])

    stale_sev = "ERROR" if strict else "WARNING"
    info["stale"] = stale = _stale(model, out_dir, reanalyze)
    for line in stale["messages"]:
        diags.append(diagnostic(stale_sev, "stale", line))

    docs = cov_mod.read_docs(out_dir)
    for p, items in sorted(sec_mod.scan_paths([out_dir]).items()):
        for f in sec_mod.masked(items):
            rel = os.path.relpath(p, out_dir).replace(os.sep, "/")
            diags.append(diagnostic("ERROR", "secret.docs", f"{rel} L{f['line']} [{f['label']}] {f['value']}"))

    if docs:
        cov = cov_mod.compute(model, annotations, out_dir, view=view)
        info["coverage"] = cov
        for opt, kind, label in THRESHOLDS:
            limit = thresholds.get(opt)
            if limit is None:
                continue
            got = cov["detection"][kind]["coverage"]
            if got is not None and got * 100 < limit:
                diags.append(diagnostic(stale_sev, "coverage.below",
                                        f"{label} の網羅率 {got * 100:.1f}% が閾値 {limit}% を下回った"))
    elif any(v is not None for v in thresholds.values()):
        diags.append(diagnostic(stale_sev, "coverage.no-docs", "資料がまだ無いため網羅率を測れない（render を先に実行する）"))
    info["view"] = view
    return _unique(diags), info


def _stale(model, out_dir, reanalyze):
    """資料（と Project Model）が今のコードに追いついているか。"""
    out = {"messages": [], "added": {}, "removed": {}, "relations": []}
    cov_path = os.path.join(model_dir(out_dir), "coverage.json")
    if os.path.isfile(cov_path):
        with open(cov_path, encoding="utf-8") as f:
            rendered = json.load(f).get("generatedCommit")
        if rendered != model.get("generatedCommit"):
            out["messages"].append(f"資料の生成コミット {_short(rendered)} と Project Model のコミット "
                                   f"{_short(model.get('generatedCommit'))} が違う（render をやり直す）")
    roots = [os.path.normpath(os.path.join(out_dir, r["path"])) for r in model["repositories"]]
    for r, root in zip(model["repositories"], roots):
        head = fs.git_head(root) if os.path.isdir(root) else None
        if head and r.get("commit") and head != r["commit"]:
            out["messages"].append(f"{r['key']}: 解析したコミット {_short(r['commit'])} のあとに "
                                   f"コミットが進んでいる（今は {_short(head)}）")
    if not reanalyze or not all(os.path.isdir(p) for p in roots):
        return out
    fresh = analyze(roots, out_dir)
    for coll, label in STALE_KINDS:
        before = {i["id"] for i in model[coll]}
        after = {i["id"] for i in fresh[coll]}
        added, removed = sorted(after - before), sorted(before - after)
        if added:
            out["added"][coll] = added
            out["messages"].append(f"新しい{label} {len(added)} 件: {_head(added)}")
        if removed:
            out["removed"][coll] = removed
            out["messages"].append(f"無くなった{label} {len(removed)} 件: {_head(removed)}")
    old_rel, new_rel = _relations(model), _relations(fresh)
    changed = sorted(k for k in set(old_rel) & set(new_rel) if old_rel[k] != new_rel[k])
    if changed:
        out["relations"] = changed
        out["messages"].append(f"関係が変わった実体 {len(changed)} 件: {_head(changed)}")
    return out


def _relations(model):
    out = {}
    for coll in COLLECTIONS + ("entrypoints",):
        for item in model[coll]:
            out[item["id"]] = sorted((field, r["id"]) for field in RELATION_FIELDS
                                     for r in item.get(field, []) if isinstance(r, dict))
    return out


def _short(commit):
    return commit[:12] if commit else "不明"


def _head(ids, n=5):
    more = f" ほか {len(ids) - n} 件" if len(ids) > n else ""
    return ", ".join(ids[:n]) + more


def _unique(items):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def failed(diags):
    return any(d["severity"] == "ERROR" for d in diags)


def result_of(diags, view=None):
    """SAFE / WARNING / FAILED。品質ゲートの状態に、古さ・機密・注釈の検査を重ねる。"""
    if any(d["severity"] == "ERROR" for d in diags) or (view and view.get("documentationStatus") == "FAILED"):
        return "FAILED"
    if any(d["severity"] == "WARNING" for d in diags) or (view and view.get("documentationStatus") == "WARNING"):
        return "WARNING"
    return "SAFE"


def format_text(diags, view=None):
    order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    lines = []
    if view is not None:
        q = view["quality"]
        lines += ["Quality Gate", ""]
        for k, label in (("confirmed", "Confirmed"), ("inferred", "Inferred"), ("unresolved", "Unresolved"),
                         ("conflict", "Conflict"), ("unsupported", "Unsupported")):
            lines.append(f"  {label + ':':<13}{q[k]:>5}")
        lines.append("")
    for d in sorted(diags, key=lambda d: order[d["severity"]]):
        lines.append(f"  {d['severity']:<7} {d['code']}: {d['message']}")
    counts = {s: sum(1 for d in diags if d["severity"] == s) for s in ("ERROR", "WARNING", "INFO")}
    lines.append(f"Result: {result_of(diags, view)}")
    lines.append(f"CHECK errors={counts['ERROR']} warnings={counts['WARNING']} info={counts['INFO']} "
                 f"RESULT={'FAIL' if counts['ERROR'] else 'OK'}")
    return "\n".join(lines)
