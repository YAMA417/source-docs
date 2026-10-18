"""網羅率と資料の健全性。`coverage.json`・overview の Documentation Health・`check` が同じ数字を使う。

3 つに分けて数える。

- detection     コードから見つけた実体（API・画面・テーブル・外部連携・入口・ジョブ・イベント）のうち、
                資料の本文に載っているものの割合。判定には資料の Markdown を実際に読む
- relationships 関係（画面→API、API→テーブル、機能→入口…）のうち、確定できたものの割合
- quality       事実の状態（confirmed / inferred / unresolved / conflict / unsupported）ごとの件数

detection で区別するもの。

- documented   資料に載っている
- undocumented 資料に載っていない（資料が古い・描画から漏れた）
- unresolved   実体そのものが未確定。網羅率の分母に入れない
- excluded     注釈で意図して資料から外したもの（理由つき）。分母に入れない
- unsupported  解析器が読み取れなかった箇所。実体として数えられないので、一覧だけ出す
"""

import os
import re

from ..core import additions
from ..core.model import normalize_path
from ..core.resolve import resolve
from ..core.status import ACCEPTED

RE_ENDPOINT = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|ANY)`?\s*\|?\s*`?(/[A-Za-z0-9_\-/{}\[\]:.$*]*)")

UNSUPPORTED_KIND = {"endpoint": "api", "table": "db", "integration": "integrations",
                    "worker": "jobs", "screen": "screens"}

DETECTION = (("api", "endpoints", "API"), ("screens", "screens", "画面"), ("db", "tables", "DB テーブル"),
             ("integrations", "integrations", "外部連携"), ("entrypoints", "entrypoints", "入口"),
             ("jobs", "jobs", "ジョブ"), ("events", "events", "イベント"))

RELATIONSHIPS = (
    ("screen-api", "画面 → API"), ("api-table", "API → テーブル"), ("feature-entrypoint", "機能 → 入口"),
    ("feature-api", "機能 → API"), ("feature-integration", "機能 → 外部連携"),
    ("integration-env", "外部連携 → 環境変数"),
)

KIND_LABEL = {k: label for k, _, label in DETECTION}
KIND_LABEL["features"] = "機能"


def read_docs(out_dir):
    """資料の本文を読む。`.source-docs/` と `html/` は資料ではないので読まない。"""
    texts = {}
    for root, dirs, files in os.walk(out_dir):
        dirs[:] = [d for d in dirs if d not in (".source-docs", "html")]
        for name in sorted(files):
            if name.endswith(".md"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="ignore") as f:
                    texts[os.path.relpath(path, out_dir).replace(os.sep, "/")] = f.read()
    return texts


class DocIndex:
    def __init__(self, texts):
        # 「未確定」「未確認」の一覧に載っているだけでは、資料に載っているとは数えない
        body = [re.split(r"^## 未確定\s*$", t, maxsplit=1, flags=re.M)[0] for t in texts.values()]
        self.text = "\n".join(body)
        self.endpoints = {(m.group(1), normalize_path(m.group(2).rstrip(".,)`")))
                          for m in RE_ENDPOINT.finditer(self.text)}

    def has_token(self, token, ignore_case=False):
        if not token:
            return False
        flags = re.I if ignore_case else 0
        return re.search(r"(?<![\w/.\-])" + re.escape(token) + r"(?![\w/\-])", self.text, flags) is not None

    def has_endpoint(self, e):
        if e["protocol"] != "http":
            return self.has_token(e["path"])
        return (e["method"], normalize_path(e["path"])) in self.endpoints


# --- 資料を読まずに数えるもの（overview の Health と check が使う） ------------------------

def health(view):
    """Resolved Project Model から、検出・関係・状態の件数を数える。資料の本文は読まない。"""
    detection = {}
    for key, coll, label in DETECTION:
        items = view[coll]
        detection[key] = {
            "label": label, "detected": len(items),
            "described": sum(1 for i in items if (i.get("notes") or {}).get("summary")),
            "pending": sum(1 for i in items if i.get("status") not in ACCEPTED),
        }
    rel = relationships(view)
    diags = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for d in view.get("diagnostics", []):
        diags[d["severity"]] += 1
    return {"status": view.get("documentationStatus"), "detection": detection, "relationships": rel,
            "quality": dict(view["quality"]), "diagnostics": diags}


def relationships(view):
    """関係の確定率。分母は「確定した関係 + 確定できなかった関係（pending）」。"""
    def count(items, field):
        resolved = sum(len(i.get(field, [])) for i in items)
        unresolved = sum(1 for i in items for p in i.get("pending", []) if p["field"] == field)
        return resolved, unresolved

    out = {}
    out["screen-api"] = count(view["screens"], "calls")
    out["api-table"] = count(view["endpoints"], "tables")
    in_feature = {ref for f in view["features"] for ref in f["entrypoints"]}
    eps = [e["id"] for e in view["entrypoints"]]
    out["feature-entrypoint"] = (sum(1 for e in eps if e in in_feature),
                                 sum(1 for e in eps if e not in in_feature))
    apis = {ref for f in view["features"] for ref in f["endpoints"]}
    out["feature-api"] = (sum(1 for e in view["endpoints"] if e["id"] in apis),
                          sum(1 for e in view["endpoints"] if e["id"] not in apis))
    integ = {ref for f in view["features"] for ref in f["integrations"]}
    out["feature-integration"] = (sum(1 for i in view["integrations"] if i["id"] in integ),
                                  sum(1 for i in view["integrations"] if i["id"] not in integ))
    out["integration-env"] = (sum(len(i.get("envVarDetails", [])) for i in view["integrations"]),
                              sum(1 for i in view["integrations"] for p in i.get("pending", [])
                                  if p["field"] == "envVars"))
    result = {}
    for key, label in RELATIONSHIPS:
        resolved, unresolved = out[key]
        total = resolved + unresolved
        result[key] = {"label": label, "resolved": resolved, "unresolved": unresolved,
                       "coverage": round(resolved / total, 4) if total else None}
    return result


# --- 資料の本文を読んで数えるもの ---------------------------------------------------------

def _kind_result():
    return {"detected": 0, "documented": 0, "coverage": None, "described": 0,
            "undocumented": [], "unresolved": [], "excluded": [], "unsupported": []}


def compute(model, annotations, out_dir, view=None):
    model = additions.merge(model, annotations)
    view = view or resolve(model, annotations)
    texts = read_docs(out_dir)
    docs = DocIndex(texts)
    excluded = {e["id"]: e.get("reason") for e in (view.get("narrative") or {}).get("exclusions") or []}
    idx = {}
    for coll in ("endpoints", "tables", "screens", "integrations", "entrypoints", "jobs", "events",
                 "commands"):
        for item in view[coll]:
            idx[item["id"]] = item

    def documented(kind, item):
        if kind == "api":
            return docs.has_endpoint(item)
        if kind == "db":
            return docs.has_token(item["key"]) or docs.has_token(item["name"])
        if kind == "screens":
            return docs.has_token(item["route"])
        if kind == "integrations":
            return docs.has_token(item["name"], ignore_case=True)
        if kind == "jobs":
            return docs.has_token(item["name"]) or docs.has_token(item.get("schedule") or "")
        if kind == "events":
            return docs.has_token(item["name"])
        if kind == "entrypoints":
            target = idx.get(item.get("target")) or {}
            if item["type"] in ("http", "webhook", "graphql", "rpc", "websocket") and target:
                return docs.has_endpoint(target)
            if item["type"] == "screen" and target:
                return docs.has_token(target["route"])
            if item["type"] == "event-handler" and target:
                return docs.has_token(target["name"])
            if target.get("name"):
                return docs.has_token(target["name"]) or docs.has_token(target.get("schedule") or "")
            return docs.has_token(item["key"].split(" <- ")[0])
        return False

    detection = {}
    for kind, coll, _ in DETECTION:
        r = _kind_result()
        for item in view[coll]:
            r["detected"] += 1
            if item["id"] in excluded:
                r["excluded"].append({"id": item["id"], "reason": excluded[item["id"]]})
                continue
            if item.get("status") not in ACCEPTED:
                r["unresolved"].append({"id": item["id"], "reasons": item.get("unresolved", [])})
                continue
            if (item.get("notes") or {}).get("summary"):
                r["described"] += 1
            if documented(kind, item):
                r["documented"] += 1
            else:
                r["undocumented"].append(item["id"])
        _ratio(r)
        detection[kind] = r
    for u in view["unsupported"]:
        kind = UNSUPPORTED_KIND.get(u["kind"], "entrypoints")
        detection[kind]["unsupported"].append({"file": u["file"], "reason": u["reason"]})

    # 機能: 候補の入口が、資料に載っている機能（確定・候補とも）に含まれているか
    r = _kind_result()
    for cand in model["features"]:
        r["detected"] += 1
        owners = [f for f in view["features"] if set(cand["entrypoints"]) <= set(f["entrypoints"])]
        if any(f["status"] == "confirmed" for f in owners):
            r["described"] += 1
        if any(docs.has_token(f["name"]) for f in owners):
            r["documented"] += 1
        else:
            r["undocumented"].append(cand["id"])
    _ratio(r)
    detection["features"] = r

    h = health(view)
    return {"schema": "source-docs/coverage", "version": 2, "documentationStatus": h["status"],
            "generatedCommit": model.get("generatedCommit"), "docs": sorted(texts),
            "detection": detection, "relationships": h["relationships"], "quality": h["quality"],
            "diagnostics": h["diagnostics"]}


def _ratio(r):
    base = r["detected"] - len(r["excluded"]) - len(r["unresolved"])
    r["coverage"] = round(r["documented"] / base, 4) if base > 0 else None


def format_text(cov):
    lines = ["DETECTION（コード → 資料）"]
    for kind, r in cov["detection"].items():
        pct = "—" if r["coverage"] is None else f"{r['coverage'] * 100:.1f}%"
        lines.append(f"  {KIND_LABEL[kind]}: detected {r['detected']} / documented {r['documented']} "
                     f"/ coverage {pct} / described {r['described']}")
        for label, key in (("undocumented", "undocumented"), ("unresolved", "unresolved"),
                           ("excluded", "excluded"), ("unsupported", "unsupported")):
            items = r[key]
            if not items:
                continue
            lines.append(f"    {label}:")
            for it in items[:20]:
                if isinstance(it, str):
                    lines.append(f"      - {it}")
                elif "file" in it:
                    lines.append(f"      - {it['file']}: {it['reason']}")
                elif "reason" in it:
                    lines.append(f"      - {it['id']}（{it['reason']}）")
                else:
                    lines.append(f"      - {it['id']}（{'; '.join(it.get('reasons', []))}）")
            if len(items) > 20:
                lines.append(f"      ほか {len(items) - 20} 件")
    lines.append("RELATIONSHIPS（確定 / 未確定）")
    for r in cov["relationships"].values():
        pct = "—" if r["coverage"] is None else f"{r['coverage'] * 100:.1f}%"
        lines.append(f"  {r['label']}: {r['resolved']} / {r['unresolved']}（{pct}）")
    lines.append("QUALITY " + " / ".join(f"{k} {v}" for k, v in cov["quality"].items()))
    return "\n".join(lines)
