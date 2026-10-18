"""解決段階。Project Model（解析の生の事実と状態）と注釈から、Resolved Project Model を作る。

    Project Model + annotations
        ↓ evidence_resolution   注釈を実体に結び付ける（notes / narrative）。生のモデルは変えない
        ↓ conflict_resolution   注釈の異議（disputes）・注釈の主張と解析結果の突き合わせ・
                                確定と未確定の振り分け・機能の組み立て
        ↓ quality.gate          本文と未確認の矛盾・根拠の無い推定・共通仕様の言い過ぎ・機密 → 診断
    Resolved Project Model（描画・coverage・check はこれだけを読む）

**未確定の値を本文用の場所に残さない。** 「本文に誤情報を出して、未確認欄で訂正する」流れを作らないため。
"""

import copy

from . import facts as facts_mod
from .features import apply_annotations, candidates, capabilities
from .model import COLLECTIONS, RELATION_FIELDS
from .status import ACCEPTED, STATUSES, diagnostic

DISPUTE_FIELDS = RELATION_FIELDS + ("envVars", "columns", "physicalForeignKeys", "ormRelations")
NARRATIVE_KEYS = ("system", "applications", "api", "flows", "details", "exclusions")


def resolve(model, annotations=None):
    """Resolved Project Model を作る（品質ゲートまで通す）。"""
    from . import quality
    annotations = annotations or {}
    view = evidence_resolution(model, annotations)
    conflict_resolution(view, annotations)
    quality.gate(view)
    return view


# --- 1. Evidence Resolution -------------------------------------------------------

def evidence_resolution(model, annotations):
    """注釈を実体に結び付ける。描画は注釈ファイルを読まず、ここで付けた notes / narrative だけを読む。"""
    view = copy.deepcopy(model)
    view["diagnostics"] = list(model.get("diagnostics", []))
    notes = annotations.get("entities") or {}
    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            if item["id"] in notes and isinstance(notes[item["id"]], dict):
                item["notes"] = copy.deepcopy(notes[item["id"]])
    view["narrative"] = {k: copy.deepcopy(annotations.get(k) or ({} if k != "exclusions" else []))
                         for k in NARRATIVE_KEYS}
    view["schemaVersion"] = {"model": model.get("version"), "resolved": 1}
    return view


# --- 2. Conflict Resolution -------------------------------------------------------

def conflict_resolution(view, annotations):
    diags = view["diagnostics"]
    by_id = {item["id"]: item for coll in COLLECTIONS + ("entrypoints",) for item in view[coll]}
    for d in annotations.get("disputes") or []:
        _apply_dispute(by_id, d, diags)
    for e in view["endpoints"]:
        _resolve_unauth_claims(e, diags)
        _resolve_request(e)
    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            _split(item)
    view["features"] = candidates(view)
    view["features"] = apply_annotations(view, annotations)
    view["capabilities"] = capabilities(view, annotations, view["features"])
    for cap in view["capabilities"]:
        for fid in cap["unknownFeatures"]:
            diags.append(diagnostic("WARNING", "annotation.capability-unknown-feature",
                                    f"{cap['id']}: 機能 {fid} が無い（古い注釈）", entity=cap["id"]))
    view["diagnostics"] = _unique(diags)
    return view


def _apply_dispute(by_id, d, diags):
    """注釈での異議。解析器の結果がコードと食い違うと Claude（または人）が確かめたもの。

    `{"entity": "integration:github-oauth", "field": "envVars", "value": "SMTP_FROM",
      "reason": "EmailProvider の引数で、GitHub のものではない", "evidence": ["lib/auth.ts:30"]}`
    既定の状態は conflict。`"status": "unresolved"` も指定できる。
    """
    status = d.get("status") or "conflict"
    entity = by_id.get(d.get("entity"))
    field, value = d.get("field"), d.get("value")
    target = None
    if entity is not None and field in DISPUTE_FIELDS:
        for x in entity.get(field) or []:
            if not isinstance(x, dict):
                continue
            k = x.get("id") or x.get("name")
            if field in ("physicalForeignKeys", "ormRelations"):
                k = x.get("name") or ",".join(x.get("columns", []))
            if k == value:
                target = x
                break
    if target is None:
        diags.append(diagnostic("WARNING", "annotation.dispute-unknown",
                                f"異議の対象が見つからない（古い注釈）: {d.get('entity')} {field} {value}",
                                entity=d.get("entity")))
        return
    target["status"] = status
    target["reason"] = d.get("reason") or "注釈で異議が出された"
    target["disputed"] = True
    severity = "ERROR" if status == "conflict" else "WARNING"
    diags.append(diagnostic(severity, f"dispute.{field}",
                            f"{d['entity']} {facts_mod.FIELD_PREDICATE.get(field, field)} {value} — {target['reason']}",
                            entity=d["entity"], evidence=d.get("evidence")))


def _resolve_unauth_claims(e, diags):
    """注釈のエラー表に書いた「未ログインのとき 〇〇」を、コードの分岐（解析結果）と突き合わせる。

    未ログイン時の応答は、コードの分岐で確かめたときだけ確定させる（precedence の api.auth）。
    解析結果と同じ値なら残す。違う値なら矛盾、解析で確かめられないなら未解決として本文から外す。
    """
    notes = e.get("notes") or {}
    check = e.get("authCheck") or {}
    machine = check.get("unauthenticatedStatus") if check.get("status") in ACCEPTED else None
    keep = []
    for row in notes.get("errors") or []:
        claimed = facts_mod.unauth_claims(str(row.get("condition") or "")) if isinstance(row, dict) else set()
        status = row.get("status") if isinstance(row, dict) else None
        if not claimed and not (isinstance(row, dict) and facts_mod.RE_UNAUTH.search(str(row.get("condition") or ""))):
            keep.append(row)
            continue
        value = _int(status) if status is not None else (min(claimed) if claimed else None)
        if machine is not None and value == machine:
            keep.append(row)
            continue
        if machine is not None:
            # コードの分岐（解析結果）が注釈より強い。注釈の行は採らず、決着が付いたので未確定にも送らない
            diags.append(diagnostic("WARNING", "precedence.unauthenticated_status",
                                    f"{e['id']} unauthenticated_status — 注釈では {value}、コードの分岐では {machine}。"
                                    "コードの分岐を採り、注釈の行は資料に出さない", entity=e["id"]))
            continue
        else:
            st = "unresolved"
            reason = "注釈には書いてあるが、未ログインのときに返す分岐をコードで確かめられない" + (
                f"（{check['reason']}）" if check.get("reason") else "")
            diags.append(diagnostic("WARNING", "unresolved.unauthenticated_status",
                                    f"{e['id']} unauthenticated_status {value} — {reason}", entity=e["id"]))
        e.setdefault("pending", []).append({"field": "unauthenticated_status", "value": value, "status": st,
                                            "reason": reason, "source": "annotation"})
    if notes.get("errors") is not None:
        notes["errors"] = keep
    if check and check.get("session") and check.get("status") not in ACCEPTED:
        e.setdefault("pending", []).append({"field": "unauthenticated_status", "value": None,
                                            "status": check["status"], "reason": check.get("reason"),
                                            "evidence": check.get("evidence", [])})


def _resolve_request(e):
    """リクエストの項目を決める。実行時の入力検証 > 注釈 > フレームワークの既定（precedence の api.request）。

    実行時の検証があれば、その項目だけを本文に出す（説明は注釈から借りる）。
    注釈にしか無い項目は本文に出さず、未確定（矛盾）に送る。
    """
    notes = e.get("notes") or {}
    spec = e.get("spec") or {}
    schema = e.get("requestSchema")
    by_note = {r.get("name"): r for r in notes.get("request") or [] if isinstance(r, dict)}
    if not schema:
        if notes.get("request"):
            e["resolvedRequest"] = {"source": "annotation", "fields": notes["request"]}
        elif spec.get("request"):
            e["resolvedRequest"] = {"source": "framework", "fields": spec["request"]}
        return
    fields = []
    for f in schema["fields"]:
        n = by_note.get(f["name"]) or {}
        fields.append({"in": f.get("in") or n.get("in"), "name": f["name"], "type": f.get("type") or n.get("type"),
                       "required": f.get("required"), "constraints": f.get("constraints") or n.get("constraints"),
                       "description": n.get("description")})
    e["resolvedRequest"] = {"source": "runtime-schema", "library": schema.get("library"), "fields": fields,
                            "evidence": schema.get("evidence", [])}
    names = {f["name"] for f in schema["fields"]}
    for name in by_note:
        if name not in names:
            e.setdefault("pending", []).append({
                "field": "request", "value": name, "status": "conflict", "source": "annotation",
                "reason": "注釈にあるが、実行時の入力検証のスキーマに無い（スキーマを採用した）",
                "evidence": schema.get("evidence", [])})


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _split(item):
    """確定した事実と未確定の事実を分ける。未確定は `pending` へ移し、本文用の場所から外す。"""
    pending = item.setdefault("pending", [])
    for field in RELATION_FIELDS:
        rels = item.get(field)
        if not isinstance(rels, list):
            continue
        keep = []
        for r in rels:
            if not isinstance(r, dict) or r.get("status", "confirmed") in ACCEPTED:
                keep.append(r)
            else:
                pending.append({"field": field, "value": r["id"], "status": r["status"],
                                "reason": r.get("reason") or "確定できない", "evidence": r.get("evidence", [])})
        item[field] = keep
    if "envVars" in item and item["envVars"] and isinstance(item["envVars"][0], dict):
        details = item["envVars"]
        item["envVarDetails"] = [e for e in details if e["status"] in ACCEPTED]
        item["envVars"] = [e["name"] for e in item["envVarDetails"]]
        for e in details:
            if e["status"] not in ACCEPTED:
                pending.append({"field": "envVars", "value": e["name"], "status": e["status"],
                                "reason": e.get("reason") or "確定できない", "evidence": e.get("evidence", [])})
    elif "envVars" in item:
        item.setdefault("envVarDetails", [])
    if "columns" in item:
        keep = []
        for c in item["columns"]:
            if c.get("status", "confirmed") in ACCEPTED:
                keep.append(c)
            else:
                pending.append({"field": "columns", "value": c["name"], "status": c["status"],
                                "reason": c.get("reason") or "確定できない"})
        item["columns"] = keep
        for group in ("physicalForeignKeys", "ormRelations"):
            keep = []
            for f in item.get(group, []):
                if f.get("status", "confirmed") in ACCEPTED:
                    keep.append(f)
                else:
                    pending.append({"field": group, "value": f.get("name") or ",".join(f["columns"]),
                                    "status": f["status"], "reason": f.get("reason") or "確定できない"})
            item[group] = keep
    item["pending"] = sorted(_unique(pending), key=lambda p: (p["field"], str(p["value"])))
    if not item["pending"]:
        item.pop("pending")


def quality(view):
    """事実の状態ごとの件数。実体・関係・環境変数・カラム・FK・解析できなかった箇所を数える。"""
    counts = {s: 0 for s in STATUSES}

    def add(status):
        counts[status if status in counts else "unresolved"] += 1

    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            if coll == "entrypoints" and item.get("target"):
                continue    # 画面・API・ジョブから作った入口は、元の実体で数えている
            add(item.get("status", "confirmed"))
            for field in RELATION_FIELDS:
                for r in item.get(field, []) if isinstance(item.get(field), list) else []:
                    if isinstance(r, dict):
                        add(r.get("status", "confirmed"))
            for e in item.get("envVarDetails", []):
                add(e["status"])
            for c in item.get("columns", []) if coll == "tables" else []:
                add(c.get("status", "confirmed"))
            for f in item.get("physicalForeignKeys", []) + item.get("ormRelations", []) if coll == "tables" else []:
                add(f.get("status", "confirmed"))
            a = item.get("authCheck") if coll == "endpoints" else None
            if a and a.get("session") and a["status"] in ACCEPTED:
                add(a["status"])
            for p in item.get("pending", []):
                add(p["status"])
    for _ in view.get("unsupported", []):
        add("unsupported")
    return counts


def _unique(items):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
    return out
