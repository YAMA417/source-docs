"""Fact（主語・述語・目的語と状態）。品質ゲートが「何について、何を主張していて、その主張がどの状態か」を
比べるための形。

    {"subject": "integration:github-oauth", "predicate": "uses_env", "object": "SMTP_FROM",
     "status": "conflict", "place": "pending", "source": "analysis", "evidence": [...], "reason": "..."}

- subject    実体の ID。カラムは `column:<テーブル>.<カラム>`
- predicate  主張の種類（下の PREDICATES）
- object     主張の値。複数の値を持てる述語（uses_env など）では、値ごとに別の Fact になる
- status     confirmed / inferred / unresolved / conflict / unsupported
- place      body（資料の本文に出る）/ pending（未確定）/ unverified（未確認。分からないと書いたもの）
- source     analysis（解析器）/ annotation（Claude が書いた注釈）

**Raw Fact** は Project Model（解析の生の結果）から、**Resolved Fact** は品質ゲートを通った
Resolved Project Model から作る。描画されるのは Resolved Fact の place=body だけ。
"""

import re

from .model import COLLECTIONS, RELATION_FIELDS

# 述語 → 1 つの主語に値を 1 つしか持てないか（single）、複数持てるか（multi）
PREDICATES = {
    "calls": "multi", "links": "multi", "uses_table": "multi", "uses_integration": "multi",
    "publishes": "multi", "enqueues": "multi", "uses_env": "multi", "request_field": "multi",
    "orm_relation": "multi",
    "type": "single", "nullable": "single", "default": "single", "unique": "single",
    "primary_key": "single", "physical_foreign_key": "single", "unauthenticated_status": "single",
}

REL_PREDICATE = {"tables": "uses_table", "integrations": "uses_integration", "calls": "calls",
                 "links": "links", "publishes": "publishes", "enqueues": "enqueues"}
FIELD_PREDICATE = dict(REL_PREDICATE, envVars="uses_env", request="request_field",
                       ormRelations="orm_relation", physicalForeignKeys="physical_foreign_key")


def fact(subject, predicate, obj, status, place, source="analysis", evidence=None, reason=None):
    out = {"subject": subject, "predicate": predicate, "object": obj, "status": status, "place": place,
           "source": source}
    if evidence:
        out["evidence"] = evidence
    if reason:
        out["reason"] = reason
    return out


def key(f):
    """同じ事実かどうかを比べるキー。single の述語は値を含めない（値が違えば矛盾）。"""
    if PREDICATES.get(f["predicate"]) == "single":
        return (f["subject"], f["predicate"])
    return (f["subject"], f["predicate"], _hashable(f["object"]))


def _hashable(v):
    return v if isinstance(v, (str, int, float, bool, type(None))) else repr(v)


def collect(view):
    """Resolved（または Raw）Project Model から Fact の列を作る。"""
    out = []
    for coll in COLLECTIONS + ("entrypoints",):
        for item in view[coll]:
            sid = item["id"]
            for field in RELATION_FIELDS:
                for r in item.get(field, []) if isinstance(item.get(field), list) else []:
                    if isinstance(r, dict):
                        out.append(fact(sid, REL_PREDICATE[field], r["id"], r.get("status", "confirmed"), "body",
                                        evidence=r.get("evidence")))
            for e in item.get("envVarDetails", item.get("envVars", []) if coll == "integrations" else []):
                if isinstance(e, dict):
                    out.append(fact(sid, "uses_env", e["name"], e["status"], "body", evidence=e.get("evidence")))
            if coll == "tables":
                out += _table_facts(item)
            if coll == "endpoints":
                out += _endpoint_facts(item)
            for p in item.get("pending", []):
                pred = FIELD_PREDICATE.get(p["field"], p["field"])
                subject = f"column:{item['key']}.{p['value']}" if p["field"] == "columns" else sid
                out.append(fact(subject, "exists" if p["field"] == "columns" else pred,
                                True if p["field"] == "columns" else p["value"], p["status"], "pending",
                                source=p.get("source", "analysis"), evidence=p.get("evidence"),
                                reason=p.get("reason")))
    return out


def _table_facts(t):
    out = []
    for c in t["columns"]:
        subject = f"column:{t['key']}.{c['name']}"
        st = c.get("status", "confirmed")
        for pred, value in (("type", c.get("type")), ("nullable", c.get("nullable")),
                            ("default", c.get("default")), ("unique", bool(c.get("unique"))),
                            ("primary_key", bool(c.get("primaryKey")))):
            if value is not None:
                out.append(fact(subject, pred, value, st, "body"))
    for f in t.get("physicalForeignKeys", []):
        out.append(fact(f"column:{t['key']}.{','.join(f['columns'])}", "physical_foreign_key",
                        f"{f['references']['table']}.{','.join(f['references']['columns'])}",
                        f.get("status", "confirmed"), "body"))
    for r in t.get("ormRelations", []):
        out.append(fact(t["id"], "orm_relation", r.get("name") or ",".join(r["columns"]),
                        r.get("status", "confirmed"), "body"))
        if r.get("physicalForeignKey") is False and r["columns"]:
            # ORM の関係はあるが、DB の外部キーは無いと migration で確かめた（矛盾ではない）
            out.append(fact(f"column:{t['key']}.{','.join(r['columns'])}", "physical_foreign_key", False,
                            "confirmed", "body", reason=r.get("physicalForeignKeyReason")))
    return out


def _endpoint_facts(e):
    out = []
    a = e.get("authCheck")
    if a and a.get("session"):
        place = "body" if a["status"] in ("confirmed", "inferred") else "pending"
        out.append(fact(e["id"], "unauthenticated_status", a.get("unauthenticatedStatus"), a["status"], place,
                        evidence=a.get("evidence"), reason=a.get("reason")))
    for f in (e.get("requestSchema") or {}).get("fields", []):
        out.append(fact(e["id"], "request_field", f["name"], e["requestSchema"]["status"], "body"))
    return out


# --- 注釈（Claude が書いた文）から、何についての主張かを読み取る ----------------------------------

# 応答の本文の文字列（"Unauthorized"）は条件ではないので含めない。
# 「セッションを確認し、無ければ 403」のように、セッション・ログインが無い場合を言う形も含める
RE_UNAUTH = re.compile(r"未ログイン|未認証|ログインしていない|ログインしていなければ|認証されていない|"
                       r"ログインが必要|"
                       r"(?:セッション|ログイン)[^。]{0,15}?(?:無|な)(?:ければ|い(?:場合|とき|時|なら))|"
                       r"unauthenticated|not (?:logged|signed) in|without (?:a )?session", re.I)
CLAIM_WINDOW = 30  # 「未ログイン」の言い回しと、ステータスが同じ文のこの文字数の内にあるときだけ主張とみなす
RE_STATUS_CODE = re.compile(r"(?<!\d)([1-5]\d\d)(?!\d)")
COLUMN_PREDICATE = [(re.compile(r"一意|ユニーク|UNIQUE", re.I), "unique"),
                    (re.compile(r"NULL|null 可|必須かどうか", re.I), "nullable"),
                    (re.compile(r"外部キー|\bFK\b"), "physical_foreign_key"),
                    (re.compile(r"(?:カラム|列)の型|\b型\b"), "type"),
                    (re.compile(r"既定値|デフォルト値"), "default")]
RE_ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")


def about(entity, item_text, explicit=None):
    """未確認の 1 項目が、どの事実について「分からない」と言っているか。[(subject, predicate, object)]。

    注釈に `about` があればそれを使う。無ければ文から読み取る（述語ごとの決まった言い回しだけ）。
    """
    if explicit:
        items = explicit if isinstance(explicit, list) else [explicit]
        return [(a.get("subject") or entity["id"], a["predicate"], a.get("object")) for a in items
                if isinstance(a, dict) and a.get("predicate")]
    text = item_text or ""
    kind = entity["id"].split(":", 1)[0]
    out = []
    if kind == "endpoint" and RE_UNAUTH.search(text):
        out.append((entity["id"], "unauthenticated_status", None))
    if kind == "table":
        for c in entity.get("columns", []):
            if re.search(r"(?<![\w])" + re.escape(c["name"]) + r"(?![\w])", text):
                for rx, pred in COLUMN_PREDICATE:
                    if rx.search(text):
                        out.append((f"column:{entity['key']}.{c['name']}", pred, None))
    if kind == "integration":
        names = {e["name"] for e in entity.get("envVarDetails", [])}
        for n in RE_ENV_NAME.findall(text):
            if n in names:
                out.append((entity["id"], "uses_env", n))
    return out


def unauth_claims(text):
    """文が「未ログインのとき〇〇を返す」と言っていれば、そのステータスの集合。

    同じ文（。; 改行で区切る）の中で、言い回しの前後 CLAIM_WINDOW 文字にあるステータスだけを拾う。
    「入力の検証エラーは 422、それ以外は Unauthorized などの文字列」を「未ログインなら 422」と読まないため。
    """
    out = set()
    for sentence in re.split(r"[。．;\n]", text or ""):
        for m in RE_UNAUTH.finditer(sentence):
            near = sentence[max(0, m.start() - CLAIM_WINDOW // 2):m.end() + CLAIM_WINDOW]
            out |= {int(c) for c in RE_STATUS_CODE.findall(near)}
    return out
