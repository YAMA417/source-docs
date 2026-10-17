"""source → docs の網羅率。コードにあるもののうち、資料に載っているものの割合。

判定には資料の本文（Markdown）を実際に読む。Project Model から生成した直後なら
ほぼ 100% になるが、コードが変わったあとに analyze だけをやり直すと、新しく増えた
API や画面が「資料に無い」として出る。これが資料の鮮度の検査になる。

区別するもの。

- documented   資料に載っている
- undocumented 資料に載っていない
- unresolved   コードから識別子を確定できなかった（パスが変数など）。網羅率の分母に入れない
- unsupported  解析器が読み取れなかった箇所。実体として数えられないので、一覧だけ出す
- excluded     注釈で意図して資料から外したもの（理由つき）。分母に入れない
"""

import os
import re

from .features import apply_annotations
from .model import normalize_path

RE_ENDPOINT = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|ANY)`?\s*\|?\s*`?(/[A-Za-z0-9_\-/{}\[\]:.$*]*)")

UNSUPPORTED_KIND = {"endpoint": "api", "table": "db", "integration": "integrations",
                    "worker": "entrypoints", "screen": "screens"}


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
        self.text = "\n".join(texts.values())
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


def _kind_result():
    return {"detected": 0, "documented": 0, "coverage": None, "described": 0,
            "undocumented": [], "unresolved": [], "excluded": [], "unsupported": []}


def compute(model, annotations, out_dir):
    texts = read_docs(out_dir)
    docs = DocIndex(texts)
    notes = annotations.get("entities") or {}
    excluded = {e["id"]: e.get("reason") for e in annotations.get("exclusions") or []}
    idx = {}
    for coll in ("endpoints", "tables", "screens", "integrations", "entrypoints", "jobs", "events",
                 "commands"):
        for item in model[coll]:
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

    result = {}
    for kind, coll in (("api", "endpoints"), ("db", "tables"), ("screens", "screens"),
                       ("integrations", "integrations"), ("entrypoints", "entrypoints")):
        r = _kind_result()
        for item in model[coll]:
            r["detected"] += 1
            if item["id"] in excluded:
                r["excluded"].append({"id": item["id"], "reason": excluded[item["id"]]})
                continue
            if item.get("status") == "unresolved":
                r["unresolved"].append({"id": item["id"], "reasons": item.get("unresolved", [])})
                continue
            if notes.get(item["id"], {}).get("summary"):
                r["described"] += 1
            if documented(kind, item):
                r["documented"] += 1
            else:
                r["undocumented"].append(item["id"])
        _ratio(r)
        result[kind] = r
    for u in model["unsupported"]:
        kind = UNSUPPORTED_KIND.get(u["kind"], "entrypoints")
        result[kind]["unsupported"].append({"file": u["file"], "reason": u["reason"]})

    # 機能: 候補の入口が、資料に載っている機能（確定・候補とも）に含まれているか
    r = _kind_result()
    final = apply_annotations(model, annotations)
    for cand in model["features"]:
        r["detected"] += 1
        owners = [f for f in final if set(cand["entrypoints"]) <= set(f["entrypoints"])]
        if any(f["status"] == "confirmed" for f in owners):
            r["described"] += 1
        if any(docs.has_token(f["name"]) for f in owners):
            r["documented"] += 1
        else:
            r["undocumented"].append(cand["id"])
    _ratio(r)
    result["features"] = r

    return {"schema": "source-docs/coverage", "version": 1,
            "generatedCommit": model.get("generatedCommit"), "docs": sorted(texts), "kinds": result}


def _ratio(r):
    base = r["detected"] - len(r["excluded"]) - len(r["unresolved"])
    r["coverage"] = round(r["documented"] / base, 4) if base > 0 else None


KIND_LABEL = {"api": "API", "db": "DB テーブル", "screens": "画面", "integrations": "外部連携",
              "entrypoints": "入口", "features": "機能"}


def format_text(cov):
    lines = []
    for kind, r in cov["kinds"].items():
        pct = "—" if r["coverage"] is None else f"{r['coverage'] * 100:.1f}%"
        lines.append(f"{KIND_LABEL[kind]}: detected {r['detected']} / documented {r['documented']} "
                     f"/ coverage {pct} / described {r['described']}")
        for label, key in (("undocumented", "undocumented"), ("unresolved", "unresolved"),
                           ("excluded", "excluded"), ("unsupported", "unsupported")):
            items = r[key]
            if not items:
                continue
            lines.append(f"  {label}:")
            for it in items[:20]:
                if isinstance(it, str):
                    lines.append(f"    - {it}")
                elif "file" in it:
                    lines.append(f"    - {it['file']}: {it['reason']}")
                elif "reason" in it:
                    lines.append(f"    - {it['id']}（{it['reason']}）")
                else:
                    lines.append(f"    - {it['id']}（{'; '.join(it.get('reasons', []))}）")
            if len(items) > 20:
                lines.append(f"    ほか {len(items) - 20} 件")
    return "\n".join(lines)
