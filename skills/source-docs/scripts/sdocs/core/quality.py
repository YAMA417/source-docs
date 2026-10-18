"""品質ゲート。Resolved Project Model を描画に渡してよいかを、Fact 単位で確かめる。

描画の直前（render）と `check` が、同じこの処理を通す。

| 検査 | 重大度 |
| --- | --- |
| 本文の事実に、確定していない状態（unresolved / conflict / unsupported）が混ざっている | ERROR（gate.contaminated） |
| 同じ事実（主語・述語）に、本文で違う値が 2 つある | ERROR（gate.multiple-values） |
| 本文で確定している事実を、未確定・未確認でも扱っている（本文と末尾の一覧が矛盾） | ERROR（gate.body-vs-pending / gate.body-vs-unverified） |
| 共通仕様（注釈）が、個別の API の事実より強く断定している | ERROR（gate.common-overclaim） |
| 個別の API の注釈が、コードで確かめられない未ログイン時の応答を断定している | ERROR（gate.endpoint-overclaim） |
| DB の最終状態が一意に決まっていない（同じテーブルに同名のカラムが複数） | ERROR（gate.db-not-final） |
| 注釈（本文に出る文）に認証情報らしき値がある | ERROR（secret.annotation） |
| 根拠の無い推定（inferred）の関係 | WARNING（未確定へ移す） |

ERROR のうち `BLOCKING`（本文の正しさに関わるもの）があると、render は資料を書き出さない。
根拠どうしの矛盾（conflict）そのものは、本文から外して末尾の「未確定」に出したうえで ERROR として数える。
"""

import json

from . import facts as facts_mod
from .model import COLLECTIONS, RELATION_FIELDS
from .status import ACCEPTED, diagnostic

BLOCKING = ("gate.", "secret.")
AUTH_TEXT_FIELDS = ("auth", "authorization", "errorFormat")


def gate(view):
    """Resolved Project Model に品質ゲートを通す。診断・事実の件数・資料の状態を view に書く。"""
    diags = view["diagnostics"]
    _demote_unbacked_inferences(view, diags)
    view["apiCommon"] = _api_common(view)
    found = facts_mod.collect(view)
    unverified = _unverified_facts(view)
    diags += _check_facts(found, unverified)
    diags += _check_common_spec(view)
    diags += _check_endpoint_notes(view)
    diags += _check_db_final(view)
    diags += _check_secrets(view)
    view["diagnostics"] = _sort(_unique(diags))
    view["facts"] = sorted(found + unverified, key=lambda f: (f["subject"], f["predicate"], str(f["object"])))
    from .resolve import quality
    view["quality"] = quality(view)
    view["quality"]["errors"] = sum(1 for d in view["diagnostics"] if d["severity"] == "ERROR")
    view["documentationStatus"] = status_of(view)
    return view


def validate_resolved_model(view):
    """描画を止めるべき誤り（BLOCKING）の列。空なら描画してよい。"""
    return [d for d in view["diagnostics"] if d["severity"] == "ERROR" and d["code"].startswith(BLOCKING)]


# 資料全体の状態（Overall Status）の判定。上から順に当てはめ、件数が 1 以上の条件があった最初の状態にする。
# どれにも当たらなければ SAFE。件数（view["quality"]）だけで決まるので、同じモデルと診断からは同じ結果になる。
# 資料の古さと、書き出した資料の機密検査は check が重ねる（validators/health.py の result_of）
STATUS_RULES = (
    ("FAILED", (("errors", "品質ゲートの ERROR"), ("conflict", "根拠どうしの矛盾（conflict）"))),
    ("WARNING", (("unresolved", "未解決の事実（unresolved）"), ("unsupported", "解析の対象外（unsupported）"))),
)


def status_of(view):
    return status_reasons(view)[0]


def status_reasons(view):
    """(状態, [(項目名, 件数, その件数で決まる状態)])。理由の一覧は判定に使った件数をすべて並べる。"""
    q = view["quality"]
    status = "SAFE"
    rows = []
    for st, conditions in STATUS_RULES:
        for k, label in conditions:
            n = q.get(k, 0)
            rows.append((label, n, st))
            if n and status == "SAFE":
                status = st
    return status, rows


# --- 本文の事実 -------------------------------------------------------------------

def _demote_unbacked_inferences(view, diags):
    """根拠（evidence）の無い inferred の関係は、本文に出さず未確定へ移す。"""
    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            for field in RELATION_FIELDS:
                rels = item.get(field)
                if not isinstance(rels, list):
                    continue
                keep = []
                for r in rels:
                    if isinstance(r, dict) and r.get("status") == "inferred" and not r.get("evidence"):
                        item.setdefault("pending", []).append({
                            "field": field, "value": r["id"], "status": "unresolved",
                            "reason": "推定だが根拠（ファイルと行）が無い"})
                        diags.append(diagnostic("WARNING", "gate.unbacked-inference",
                                                f"{item['id']} {facts_mod.REL_PREDICATE[field]} {r['id']} — 根拠の無い推定を本文から外した",
                                                entity=item["id"]))
                    else:
                        keep.append(r)
                item[field] = keep


def _unverified_facts(view):
    """注釈の「未確認」の各項目が、どの事実について分からないと言っているか。"""
    out = []
    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            for u in (item.get("notes") or {}).get("unverified") or []:
                if not isinstance(u, dict):
                    continue
                for subject, predicate, obj in facts_mod.about(item, u.get("item"), u.get("about")):
                    out.append(facts_mod.fact(subject, predicate, obj, "unresolved", "unverified",
                                              source="annotation", reason=u.get("item")))
    return out


def _check_facts(found, unverified):
    out = []
    body = {}
    for f in found:
        if f["place"] != "body":
            continue
        if f["status"] not in ACCEPTED:
            out.append(diagnostic("ERROR", "gate.contaminated",
                                  f"{_label(f)} — 状態が {f['status']} なのに本文に入っている", entity=f["subject"]))
        body.setdefault(facts_mod.key(f), []).append(f)
    for k, fs in body.items():
        values = {repr(f["object"]) for f in fs}
        if facts_mod.PREDICATES.get(fs[0]["predicate"]) == "single" and len(values) > 1:
            out.append(diagnostic("ERROR", "gate.multiple-values",
                                  f"{fs[0]['subject']} {fs[0]['predicate']} — 本文に違う値がある: {', '.join(sorted(values))}",
                                  entity=fs[0]["subject"]))
    for f in found:
        if f["place"] == "pending" and facts_mod.key(f) in body:
            shown = body[facts_mod.key(f)][0]
            out.append(diagnostic("ERROR", "gate.body-vs-pending",
                                  f"{_label(shown)} が本文にあり、同じ事実が未確定（{f['status']}）にもある"
                                  + (f": {f['reason']}" if f.get("reason") else ""), entity=f["subject"]))
    for u in unverified:
        hits = [b for b in body.get(_unverified_key(u, body), [])]
        if hits:
            out.append(diagnostic("ERROR", "gate.body-vs-unverified",
                                  f"{_label(hits[0])} を本文で確定しているが、未確認に「{u['reason']}」とある。"
                                  "本文が誤りなら disputes で外し、分かっているなら未確認から消す",
                                  entity=u["subject"]))
    return out


def _unverified_key(u, body):
    """未確認の項目は値を言わないことが多い。single の述語は主語と述語だけで突き合わせる。"""
    if u["object"] is None:
        for k in body:
            if k[0] == u["subject"] and k[1] == u["predicate"]:
                return k
        return None
    return facts_mod.key(u)


def _label(f):
    return f"{f['subject']} {f['predicate']} {f['object']}"


# --- API の共通仕様は、個別の API の事実を集めたもの ------------------------------------

def _api_common(view):
    """未ログイン時の応答を、ログイン状態を確かめている API ごとに集める。共通仕様の表はこれを出す。"""
    confirmed, unresolved, conflict = {}, [], []
    for e in sorted(view["endpoints"], key=lambda e: e["id"]):
        a = e.get("authCheck") or {}
        if not a.get("session"):
            continue
        if a["status"] in ACCEPTED:
            confirmed.setdefault(str(a["unauthenticatedStatus"]), []).append(e["id"])
        elif a["status"] == "conflict":
            conflict.append(e["id"])
        else:
            unresolved.append(e["id"])
    return {"unauthenticated": {"confirmed": confirmed, "unresolved": unresolved, "conflict": conflict,
                                "sessionEndpoints": sum(len(v) for v in confirmed.values())
                                + len(unresolved) + len(conflict)}}


def _check_common_spec(view):
    """注釈の共通仕様が「未ログインなら 403」のように断定しているなら、全 API がそうか確かめる。"""
    out = []
    common = (view.get("narrative") or {}).get("api") or {}
    agg = view["apiCommon"]["unauthenticated"]
    for field in AUTH_TEXT_FIELDS:
        claimed = facts_mod.unauth_claims(str(common.get(field) or ""))
        if not claimed:
            continue
        wrong = [eid for status, ids in agg["confirmed"].items() if int(status) not in claimed
                 for eid in ids] if all(s.isdigit() for s in agg["confirmed"]) else []
        unknown = agg["unresolved"] + agg["conflict"]
        if wrong or unknown:
            detail = []
            if unknown:
                detail.append("確定できない API: " + ", ".join(i.split(":", 1)[1] for i in unknown))
            if wrong:
                detail.append("違う応答の API: " + ", ".join(i.split(":", 1)[1] for i in wrong))
            out.append(diagnostic("ERROR", "gate.common-overclaim",
                                  f"共通仕様の {field} が未ログイン時の応答を {', '.join(map(str, sorted(claimed)))} と断定しているが、"
                                  f"個別の API がそうなっていない（{'。'.join(detail)}）。共通仕様には全 API に当てはまることだけを書く"))
    return out


def _check_endpoint_notes(view):
    out = []
    for e in view["endpoints"]:
        claimed = facts_mod.unauth_claims(str((e.get("notes") or {}).get("auth") or ""))
        if not claimed:
            continue
        a = e.get("authCheck") or {}
        ok = a.get("status") in ACCEPTED and a.get("unauthenticatedStatus") in claimed
        if not ok:
            out.append(diagnostic("ERROR", "gate.endpoint-overclaim",
                                  f"{e['id']} の認証の注釈が未ログイン時の応答を {', '.join(map(str, sorted(claimed)))} と書いているが、"
                                  f"コードの分岐で確かめられない（{a.get('reason') or 'コードの分岐と値が違う'}）",
                                  entity=e["id"]))
    return out


def _check_db_final(view):
    out = []
    for t in view["tables"]:
        names = [c["name"] for c in t["columns"]]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            out.append(diagnostic("ERROR", "gate.db-not-final",
                                  f"{t['id']}: 最終状態に同名のカラムが複数ある: {', '.join(dup)}", entity=t["id"]))
    return out


def _check_secrets(view):
    from ..validators.secrets import blocking, masked, scan
    notes = {"narrative": view.get("narrative"),
             "notes": {i["id"]: i["notes"] for c in COLLECTIONS + ("entrypoints",) for i in view[c] if i.get("notes")}}
    found = blocking(scan(json.dumps(notes, ensure_ascii=False, indent=1), []))
    return [diagnostic("ERROR", "secret.annotation", f"注釈 [{f['label']}] {f['value']}") for f in masked(found)]


def _sort(diags):
    order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    return sorted(diags, key=lambda d: (order[d["severity"]], d["code"], d.get("entity") or "", d["message"]))


def _unique(items):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
    return out
