"""機能（Feature）の組み立て。出力済みの Project Model だけを入力にする純粋な関数。

機能の起点は画面に限らない。どの入口も起点になる。

- 画面 — その画面と、画面から呼ばれる API をまとめて 1 機能
- API — 画面からも定期実行からも呼ばれていないもの（外部向け API・Webhook）
- 定期実行・キュー処理・ワーカー・イベント処理・CLI・DB トリガ・ライフサイクルフック

**機能の業務上の名前はコードからは決まらない。** 候補の名前は入口の識別子のままにし
（`nameSource: derived`）、注釈（annotations.json）で名前が付いたものだけを確定とする。
"""

import re

from .model import OPERATIONS, index

FEATURE_TYPE = {
    "screen": "screen", "http": "api", "webhook": "webhook", "graphql": "graphql",
    "rpc": "rpc", "websocket": "websocket", "cron": "cron", "scheduler": "scheduler",
    "queue-consumer": "queue-consumer", "worker": "worker", "event-handler": "event-handler",
    "cli": "cli", "db-trigger": "db-trigger", "lifecycle-hook": "lifecycle-hook",
}

TYPE_LABEL = {
    "screen": "画面操作", "api": "API", "webhook": "Webhook", "graphql": "GraphQL", "rpc": "RPC",
    "websocket": "WebSocket", "cron": "定期実行", "scheduler": "スケジューラ",
    "queue-consumer": "キュー処理", "worker": "ワーカー", "event-handler": "イベント処理",
    "cli": "CLI", "db-trigger": "DB トリガ", "lifecycle-hook": "ライフサイクルフック",
    "composite": "複合",
}

API_TYPES = ("http", "graphql", "rpc", "websocket")


def slug(text):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s or "root"


class _Acc:
    """1 機能ぶんの関係を集める。"""

    def __init__(self, idx):
        self.idx = idx
        self.entrypoints, self.screens, self.endpoints = set(), set(), set()
        self.jobs, self.events, self.integrations = set(), set(), set()
        self.tables = {}
        self.files = set()
        self.evidence = []
        self.unresolved = []
        self._seen = set()

    def add_entrypoint(self, ep):
        self.entrypoints.add(ep["id"])
        self._absorb(ep)
        # イベント処理・ライフサイクルフックは、入口そのものが関係を持つ
        self._relations(ep)
        target = self.idx.get(ep.get("target")) if ep.get("target") else None
        if target:
            self.add_entity(target)

    def add_entity(self, item):
        if item["id"] in self._seen:
            return
        self._seen.add(item["id"])
        kind = item["id"].split(":", 1)[0]
        if kind == "screen":
            self.screens.add(item["id"])
        elif kind == "endpoint":
            self.endpoints.add(item["id"])
        elif kind == "job":
            self.jobs.add(item["id"])
        elif kind == "event":
            self.events.add(item["id"])
        elif kind == "table":
            self.tables.setdefault(item["id"], set())
        self._absorb(item)
        self._relations(item)

    def _relations(self, item):
        for rel in item.get("tables", []):
            self.tables.setdefault(rel["id"], set()).update(rel.get("operations", []))
            self._files(rel)
        for rel in item.get("integrations", []):
            self.integrations.add(rel["id"])
            self._files(rel)
        for rel in item.get("publishes", []):
            self.events.add(rel["id"])
            self._files(rel)
        for rel in item.get("enqueues", []):
            self.jobs.add(rel["id"])
            self._files(rel)
        for rel in item.get("calls", []):
            self._files(rel)
            target = self.idx.get(rel["id"])
            if target:
                self.add_entity(target)

    def _absorb(self, item):
        if item.get("file"):
            self.files.add(item["file"])
        for ev in item.get("evidence", []):
            if ev not in self.evidence:
                self.evidence.append(ev)
        for reason in item.get("unresolved", []):
            note = f"{item['id']}: {reason}"
            if note not in self.unresolved:
                self.unresolved.append(note)
        for u in item.get("unmatched", []):
            note = f"{item['id']}: {u['kind']} {u['value']} の行き先が検出結果に無い"
            if note not in self.unresolved:
                self.unresolved.append(note)

    def _files(self, rel):
        for ev in rel.get("evidence", []):
            self.files.add(ev["file"])

    def export(self, fid, name, name_source, ftype, status, summary=None):
        return {
            "id": fid, "name": name, "nameSource": name_source, "type": ftype, "status": status,
            "summary": summary,
            "entrypoints": sorted(self.entrypoints), "screens": sorted(self.screens),
            "endpoints": sorted(self.endpoints),
            "tables": [{"id": t, "operations": sorted(ops, key=OPERATIONS.index)}
                       for t, ops in sorted(self.tables.items())],
            "integrations": sorted(self.integrations), "jobs": sorted(self.jobs),
            "events": sorted(self.events), "implementationFiles": sorted(self.files),
            "evidence": self.evidence[:20], "unresolved": self.unresolved,
        }


def _ep_by_target(model):
    out = {}
    for ep in model["entrypoints"]:
        if ep.get("target"):
            out.setdefault(ep["target"], []).append(ep)
    return out


def candidates(model):
    """入口から機能の候補を作る。"""
    idx = index(model)
    by_target = _ep_by_target(model)

    # 画面や定期実行から呼ばれている API は、呼び出し元の機能に含める
    # 画面・定期実行・他の API（中継）から呼ばれている API は、呼び出し元の機能に含める
    called = set()
    for coll in ("screens", "jobs", "endpoints"):
        for item in model[coll]:
            for rel in item.get("calls", []):
                if rel["id"] != item["id"]:
                    called.add(rel["id"])

    features, used_ids = [], set()
    # どこからも呼ばれていない API は、同じリソース（`/api/articles` と `/api/articles/:id`）を 1 つの候補に
    # まとめる。1 本ずつ機能にすると、CRUD が自動で生える基盤（Strapi など）で一覧が API の羅列になる
    api_groups = {}
    for ep in sorted(model["entrypoints"], key=lambda e: (_order(e["type"]), e["id"])):
        if ep["type"] in API_TYPES and ep.get("target") in called:
            continue
        if ep["type"] == "http":
            target = idx.get(ep.get("target")) or {}
            api_groups.setdefault((ep["app"], resource_of(target.get("path") or ep["key"])), []).append(ep)
            continue
        acc = _Acc(idx)
        acc.add_entrypoint(ep)
        # 呼び出し先の API の入口も、この機能の入口として持つ
        for endpoint_id in sorted(acc.endpoints):
            for api_ep in by_target.get(endpoint_id, []):
                acc.entrypoints.add(api_ep["id"])
        features.append(_candidate(acc, ep, used_ids))
    position = sum(1 for f in features if f["type"] == "screen")
    for (_, resource), eps in sorted(api_groups.items(), key=lambda kv: kv[0][1]):
        acc = _Acc(idx)
        for ep in eps:
            acc.add_entrypoint(ep)
        for endpoint_id in sorted(acc.endpoints):
            for api_ep in by_target.get(endpoint_id, []):
                acc.entrypoints.add(api_ep["id"])
        if len(eps) == 1:
            feature = _candidate(acc, eps[0], used_ids)
        else:
            suffix = eps[0]["id"].split("@", 1)[1] if "@" in eps[0]["id"] else ""
            name = f"{resource} の API" + (f"@{suffix}" if suffix else "")
            fid = _unique(f"feature:api-{slug(resource + ('-' + suffix if suffix else ''))}", used_ids)
            feature = acc.export(fid, name, "derived", "api", "candidate")
        features.insert(position, feature)
        position += 1
    # 呼び合っている API どうしは、どちらも起点にならずに漏れうる。どの機能にも入らなかった入口は単独で起点にする
    covered = {e for f in features for e in f["entrypoints"]}
    for ep in sorted(model["entrypoints"], key=lambda e: e["id"]):
        if ep["id"] not in covered:
            acc = _Acc(idx)
            acc.add_entrypoint(ep)
            features.append(_candidate(acc, ep, used_ids))
            covered |= acc.entrypoints
    return features


def resource_of(path):
    """パスから、末尾のパラメータを除いたリソースのパスを作る（`/api/posts/:id` → `/api/posts`）。"""
    segs = [x for x in path.split("/") if x]
    while segs and (segs[-1].startswith((":", "{", "[", "*", "<", "$"))):
        segs.pop()
    return "/" + "/".join(segs)


def _candidate(acc, ep, used_ids):
    ftype = FEATURE_TYPE[ep["type"]]
    # 名前は入口の ID の自然キー部分。アプリをまたいで同じキーがあれば `@アプリ` が付いて区別される
    name = ep["id"].split(":", 2)[2]
    fid = _unique(f"feature:{ftype}-{slug(name)}", used_ids)
    return acc.export(fid, name, "derived", ftype, "candidate")


def _order(ep_type):
    order = list(FEATURE_TYPE)
    return order.index(ep_type) if ep_type in order else len(order)


def _unique(fid, used):
    base, n = fid, 2
    while fid in used:
        fid = f"{base}-{n}"
        n += 1
    used.add(fid)
    return fid


def apply_annotations(model, annotations):
    """注釈で名前を付けた・まとめ直した機能を、候補に重ねる。

    - `id` が候補の ID と同じなら、その候補に名前と概要を付けて確定にする
    - `entrypoints` を持つなら、その入口から機能を組み直す。`replaces` に挙げた候補と、
      入口がすべて新しい機能に含まれる候補は消す
    """
    idx = index(model)
    features = {f["id"]: dict(f) for f in model["features"]}
    order = [f["id"] for f in model["features"]]
    for spec in (annotations or {}).get("features", []):
        fid = spec["id"]
        if spec.get("entrypoints"):
            acc = _Acc(idx)
            for ep_id in spec["entrypoints"]:
                ep = idx.get(ep_id)
                if ep:
                    acc.add_entrypoint(ep)
            types = {FEATURE_TYPE[idx[e]["type"]] for e in spec["entrypoints"] if e in idx}
            ftype = spec.get("type") or (types.pop() if len(types) == 1 else _primary_type(spec, idx))
            new = acc.export(fid, spec["name"], "annotation", ftype, "confirmed", spec.get("summary"))
            covered = set(new["entrypoints"])
            slot = None
            for cid in list(features):
                c = features[cid]
                if cid in spec.get("replaces", []) or (
                        c["status"] == "candidate" and c["entrypoints"]
                        and set(c["entrypoints"]) <= covered):
                    features.pop(cid)
                    if cid in order:
                        pos = order.index(cid)
                        slot = pos if slot is None else min(slot, pos)
                        order[pos] = None
            features[fid] = new
            # 置き換えた候補があった位置に入れる。一覧の並び（画面 → API → …）を崩さない
            if slot is None:
                order.append(fid)
            else:
                order[slot] = fid
        elif fid in features:
            f = features[fid]
            f["name"] = spec["name"]
            f["nameSource"] = "annotation"
            f["status"] = "confirmed"
            if spec.get("summary"):
                f["summary"] = spec["summary"]
    return [features[i] for i in order if i and i in features]


def _primary_type(spec, idx):
    for ep_id in spec["entrypoints"]:
        ep = idx.get(ep_id)
        if ep and ep["type"] != "screen" and ep["type"] not in API_TYPES:
            return FEATURE_TYPE[ep["type"]]
    for ep_id in spec["entrypoints"]:
        ep = idx.get(ep_id)
        if ep and ep["type"] == "screen":
            return "screen"
    return "composite"


def feature_slug(feature_id):
    return feature_id.split(":", 1)[-1]
