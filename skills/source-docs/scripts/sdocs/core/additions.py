"""注釈の `additions`: 解析器が読めなかった実体を、Claude がコードを読んで足す。

解析器はフレームワークごとの書き方しか読めない。知らない書き方（新しい ORM・ルーター・独自のルート定義）でも
資料が作れるように、Claude が読んで見つけたテーブル・API・画面・外部連携を、根拠つきでモデルに足す。

- 各項目は根拠（`file:line`）を必ず持つ。行の前後に名前（テーブル名・パス・ルート）が書かれているかを
  `validators.annotations` が機械で照合し、照合できなければ ERROR にする（資料を書き出さない）
- 足した実体の状態は inferred（根拠あり）。解析器が確かめた confirmed とは分けて扱う
- 解析器が同じものを見つけていれば足さない（解析の結果を優先する）
"""

import copy

KINDS = {"table": "tables", "endpoint": "endpoints", "screen": "screens", "integration": "integrations"}
RULE = "claude.read"


def entity_id(spec):
    kind = spec.get("kind")
    if kind == "table":
        return f"table:{spec.get('name')}"
    if kind == "endpoint":
        return f"endpoint:{str(spec.get('method') or '').upper()} {spec.get('path')}"
    if kind == "screen":
        return f"screen:{spec.get('route')}"
    if kind == "integration":
        return spec.get("id") or f"integration:{str(spec.get('name') or '').lower().replace(' ', '-')}"
    return None


def needles(spec):
    """根拠の行に書かれているはずの文字列（どれか 1 つが見つかればよい）。"""
    kind = spec.get("kind")
    if kind == "table":
        return [spec.get("name")] + list(spec.get("aliases") or [])
    if kind == "endpoint":
        path = spec.get("path") or ""
        out = [path]
        if path.startswith("/api/"):
            out.append(path[4:])          # Strapi などは /api を付けずに定義する
        parts = [p for p in path.split("/") if p and not p.startswith(":")]
        if parts:
            out.append(parts[-1])
        return out
    if kind == "screen":
        route = spec.get("route") or ""
        parts = [p for p in route.split("/") if p and not p.startswith(":")]
        return [route] + ([parts[-1]] if parts else [])
    if kind == "integration":
        return [spec.get("name")] + list(spec.get("hosts") or []) + list(spec.get("sdk") or [])
    return []


def _evidence(spec):
    out = []
    for raw in spec.get("evidence") or []:
        if isinstance(raw, str):
            file, _, line = raw.partition(":")
            out.append({"file": file, "line": int(line), "rule": RULE} if line.isdigit()
                       else {"file": file, "rule": RULE})
        elif isinstance(raw, dict) and raw.get("file"):
            out.append(dict(raw, rule=RULE))
    return out


def _app_of(model, file):
    best = None
    for app in model["applications"]:
        path = app.get("path") or "."
        if path == "." or file == path or file.startswith(path.rstrip("/") + "/"):
            if best is None or len(path) > len(best.get("path") or "."):
                best = app
    return best["id"] if best else None


def merge(model, annotations):
    """additions を足したモデルを返す（元のモデルは変えない）。"""
    specs = (annotations or {}).get("additions") or []
    if not specs:
        return model
    out = copy.deepcopy(model)
    ids = {item["id"] for coll in KINDS.values() for item in out[coll]}
    added_kinds = set()
    for spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
            continue
        eid = entity_id(spec)
        ev = _evidence(spec)
        if not eid or not ev or eid in ids:
            continue
        ids.add(eid)
        file, line = ev[0]["file"], ev[0].get("line")
        base = {"id": eid, "app": _app_of(out, file), "status": "inferred", "unresolved": [],
                "evidence": ev, "file": file, "line": line, "addedBy": "annotation"}
        kind = spec["kind"]
        if kind == "table":
            cols = []
            for c in spec.get("columns") or []:
                if isinstance(c, dict) and c.get("name"):
                    cols.append({"name": c["name"], "type": c.get("type"), "nullable": c.get("nullable"),
                                 "default": c.get("default"), "primaryKey": bool(c.get("primaryKey")),
                                 "unique": bool(c.get("unique")), "references": c.get("references"),
                                 "source": "annotation", "status": "inferred"})
            fks = [{"name": None, "columns": [c["name"]],
                    "references": {"table": c["references"]["table"],
                                   "columns": [c["references"].get("column")] if c["references"].get("column") else []},
                    "source": "annotation", "status": "inferred"}
                   for c in cols if isinstance(c.get("references"), dict) and c["references"].get("table")]
            out["tables"].append(dict(base, name=spec["name"], key=spec["name"], kind="table", schema=None,
                                      columns=cols, primaryKey=[c["name"] for c in cols if c["primaryKey"]],
                                      uniques=[], checks=[], indexes=[], physicalForeignKeys=[],
                                      ormRelations=fks, triggers=[], physicalSource="annotation"))
        elif kind == "endpoint":
            method, path = str(spec["method"]).upper(), spec["path"]
            out["endpoints"].append(dict(base, protocol=spec.get("protocol") or "http", method=method,
                                         path=path, key=f"{method} {path}"))
            out["entrypoints"].append({"id": f"entrypoint:http:{method} {path}", "type": "http",
                                       "app": base["app"], "target": eid, "status": "inferred",
                                       "unresolved": [], "evidence": ev, "key": f"{method} {path}",
                                       "file": file, "line": line})
        elif kind == "screen":
            route = spec["route"]
            out["screens"].append(dict(base, route=route, name=spec.get("name") or route, key=route))
            out["entrypoints"].append({"id": f"entrypoint:screen:{route}", "type": "screen",
                                       "app": base["app"], "target": eid, "status": "inferred",
                                       "unresolved": [], "evidence": ev, "key": route,
                                       "file": file, "line": line})
        elif kind == "integration":
            out["integrations"].append(dict(base, app=None, name=spec["name"],
                                            directions=[spec.get("direction") or "outbound"],
                                            protocols=[], sdk=list(spec.get("sdk") or []),
                                            hosts=list(spec.get("hosts") or []), envVars=[]))
        added_kinds.add(KINDS[kind])
    # Claude が読んで足したものがあれば、「読み取れなかった」は当てはまらない
    out["unsupported"] = [u for u in out.get("unsupported", []) if u.get("kind") not in added_kinds]
    return out
