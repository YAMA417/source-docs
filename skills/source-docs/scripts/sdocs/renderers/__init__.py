"""資料一式の書き出し。Markdown・HTML・JSON（evidence）を、同じ解決済みの view から作る。

描画の前に必ず `core.resolve.resolve` を通す。未確定の事実を本文に出さないため。
"""

import os

from ..validators.annotations import normalize_evidence
from ..core.features import feature_slug
from ..core.model import COLLECTIONS, model_dir, write_json
from ..core.quality import validate_resolved_model
from . import markdown as md

RENDERERS = {
    "overview": md.render_overview, "features": md.render_features, "database": md.render_database,
    "api": md.render_api, "screens": md.render_screens, "integrations": md.render_integrations,
}


class QualityGateError(Exception):
    """品質ゲートの ERROR（本文の正しさに関わるもの）があるため、資料を書き出さなかった。"""

    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__("\n".join(f"{d['code']}: {d['message']}" for d in diagnostics))


def find_feature(features, key):
    """ID・スラッグ・名前のどれでも機能を引けるようにする。"""
    for f in features:
        if key in (f["id"], feature_slug(f["id"]), f["name"]):
            return f
    return None


def render_docs(resolved, out_dir, only=None, feature_keys=(), kinds=("flow", "detail")):
    """Resolved Project Model から Markdown を書き、書いたパスの列を返す。

    resolved — `core.resolve.resolve()` の結果（品質ゲートを通ったもの）。注釈ファイルは読まない。
    only — 書く全体資料の名前（None ならすべて）。空の列なら全体資料は書かない。
    feature_keys — flow / detail を書く機能。注釈が無くても、モデルから分かる範囲で骨組みを書く。
    注釈に flows / details がある機能は、指定が無くても書く。
    """
    blocking = validate_resolved_model(resolved)
    if blocking:
        raise QualityGateError(blocking)
    features = resolved["features"]
    narrative = resolved.get("narrative") or {}
    flows = dict(narrative.get("flows") or {})
    details = dict(narrative.get("details") or {})
    targets = []
    for key in feature_keys:
        f = find_feature(features, key)
        if f is None:
            raise SystemExit(f"error: 機能が見つからない: {key}（`features` で候補を確認する）")
        targets.append(f["id"])
        if "flow" in kinds:
            flows.setdefault(f["id"], {})
        if "detail" in kinds:
            details.setdefault(f["id"], {})

    written = []
    selected = list(RENDERERS) if only is None else [k for k in RENDERERS if k in only]
    ctx = md.Ctx(resolved, set(details), set(flows))
    for key in selected:
        path = os.path.join(out_dir, f"{key}.md")
        _write(path, RENDERERS[key](ctx))
        written.append(path)

    sub_ctx = md.Ctx(resolved, set(details), set(flows), prefix="../")
    by_id = {f["id"]: f for f in features}
    for kind, specs, fn in (("flow", flows, md.render_flow), ("detail", details, md.render_detail)):
        if kind not in kinds:
            continue
        for fid, spec in sorted(specs.items()):
            if fid not in by_id:
                continue
            if feature_keys and fid not in targets and only == []:
                continue
            path = os.path.join(out_dir, kind, f"{feature_slug(fid)}.md")
            _write(path, fn(sub_ctx, by_id[fid], spec))
            written.append(path)
    return written


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def evidence_index(resolved):
    """実体ごとに、どのソースを根拠にしたかをまとめる。資料本文に載せきれない索引。"""
    view = resolved
    out = {}
    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            entry = {"status": item["status"], "sources": item.get("evidence", [])}
            rels = {}
            for field in ("tables", "integrations", "calls", "links", "publishes", "enqueues"):
                if item.get(field):
                    rels[field] = [{"id": r["id"], "status": r["status"], "sources": r.get("evidence", [])}
                                   for r in item[field]]
            if rels:
                entry["relations"] = rels
            if item.get("envVarDetails"):
                entry["envVars"] = [{"name": e["name"], "status": e["status"], "via": e["source"],
                                     "sources": e["evidence"]} for e in item["envVarDetails"]]
            if item.get("pending"):
                entry["pending"] = item["pending"]
            out[item["id"]] = entry
    for f in view["features"]:
        out[f["id"]] = {"sources": f["evidence"], "implementationFiles": f["implementationFiles"]}
    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            ev = (item.get("notes") or {}).get("evidence")
            if ev and item["id"] in out:
                out[item["id"]]["annotation"] = [normalize_evidence(e) for e in ev]
    for kind in ("flows", "details"):
        for fid, spec in ((view.get("narrative") or {}).get(kind) or {}).items():
            sources = []
            for key, value in spec.items():
                if isinstance(value, dict) and value.get("evidence"):
                    sources += [normalize_evidence(e) for e in value["evidence"]]
                if isinstance(value, list):
                    for v in value:
                        if isinstance(v, dict) and v.get("evidence"):
                            sources += [normalize_evidence(e) for e in v["evidence"]]
            if sources and fid in out:
                out[fid].setdefault("annotation", []).extend(sources)
    return out


def write_evidence(resolved, out_dir):
    """evidence.json（根拠の索引）と facts.json（品質ゲートを通った Fact の一覧）を書く。"""
    path = os.path.join(model_dir(out_dir), "evidence.json")
    write_json(path, {"schema": "source-docs/evidence", "version": 2, "entities": evidence_index(resolved)})
    facts_path = os.path.join(model_dir(out_dir), "facts.json")
    write_json(facts_path, {"schema": "source-docs/facts", "version": 1,
                            "documentationStatus": resolved.get("documentationStatus"),
                            "quality": resolved.get("quality"), "facts": resolved.get("facts", []),
                            "diagnostics": resolved.get("diagnostics", [])})
    return path
