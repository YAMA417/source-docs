"""Project Model の組み立て・保存・検査。

アダプタは `Builder` に事実を足していくだけで、ID の採番・参照の付け替え・
入口（Entrypoint）の導出は `Builder.finalize()` がまとめて行う。

ID は `<種別>:<自然キー>` の形で、人が読めて安定するものにする。
同じ自然キーが複数のアプリケーションにある場合だけ `@<アプリ>` を付けて区別する。
"""

import json
import os
import re

from . import MODEL_SCHEMA, MODEL_VERSION

ENTRYPOINT_TYPES = (
    "screen", "http", "webhook", "graphql", "rpc", "websocket",
    "cron", "scheduler", "queue-consumer", "worker", "event-handler",
    "cli", "db-trigger", "lifecycle-hook",
)

JOB_KINDS = ("cron", "scheduler", "queue-consumer", "worker")
PROTOCOLS = ("http", "graphql", "rpc", "websocket")
OPERATIONS = ("CREATE", "READ", "UPDATE", "DELETE")
STATUSES = ("detected", "unresolved")

COLLECTIONS = (
    "screens", "endpoints", "tables", "enums", "sequences",
    "integrations", "jobs", "events", "commands",
)

# 種別ごとの ID の接頭辞
PREFIX = {
    "screens": "screen", "endpoints": "endpoint", "tables": "table",
    "enums": "enum", "sequences": "sequence", "integrations": "integration",
    "jobs": "job", "events": "event", "commands": "command",
}

# アプリケーションをまたいで 1 つに数えるもの。DB とイベントと外部サービスは共有される
GLOBAL_KINDS = ("integrations", "events")

RE_PARAM = re.compile(r"^(\{[^}]*\}|:[^/]+|<[^>]+>|\$[^/]+|\[[^\]]+\]|\*[^/]*)$")


def normalize_path(path):
    """パスパラメータの表記揺れを `{}` にそろえる。照合のキーに使う。

    `/orders/:id` `/orders/{id}` `/orders/[id]` `/orders/<id>` → `/orders/{}`
    """
    if not path:
        return path
    path = path.split("?", 1)[0].split("#", 1)[0]
    segs = [s for s in path.split("/") if s]
    out = ["{}" if RE_PARAM.match(s) or "${" in s else s for s in segs]
    return "/" + "/".join(out)


def endpoint_key(method, path):
    return f"{method} {path}"


class Builder:
    """解析中の可変な状態。アダプタはこのメソッドだけを使う。"""

    def __init__(self):
        self.repositories = []
        self.applications = []
        self._items = {c: {} for c in COLLECTIONS}      # uid -> entity
        self._entrypoints = {}                          # uid -> entrypoint
        self.unsupported = []
        self.table_aliases = {}                         # alias -> set(uid)

    # --- 基本 -------------------------------------------------------------

    @staticmethod
    def _uid(collection, app_id, key):
        scope = "" if collection in GLOBAL_KINDS else app_id
        return f"{collection}|{scope}|{key}"

    def _upsert(self, collection, app, key, fields, evidence):
        uid = self._uid(collection, app["id"] if app else "", key)
        item = self._items[collection].get(uid)
        if item is None:
            item = {"_uid": uid, "_collection": collection, "key": key,
                    "app": app["id"] if app else None, "status": "detected",
                    "unresolved": [], "evidence": []}
            self._items[collection][uid] = item
        for k, v in fields.items():
            if v is None:
                continue
            if isinstance(v, list) and isinstance(item.get(k), list):
                for x in v:
                    if x not in item[k]:
                        item[k].append(x)
            elif k not in item or item[k] in (None, "", []):
                item[k] = v
        if evidence:
            add_evidence(item, evidence)
        return item

    def get(self, uid):
        coll = uid.split("|", 1)[0]
        if coll == "entrypoints":
            return self._entrypoints.get(uid)
        return self._items.get(coll, {}).get(uid)

    def items(self, collection):
        return list(self._items[collection].values())

    def mark_unresolved(self, item, reason):
        item["status"] = "unresolved"
        if reason not in item["unresolved"]:
            item["unresolved"].append(reason)

    def add_unsupported(self, app, file, kind, reason):
        entry = {"kind": kind, "file": file, "reason": reason,
                 "app": app["id"] if app else None}
        if entry not in self.unsupported:
            self.unsupported.append(entry)

    # --- 実体 -------------------------------------------------------------

    def add_screen(self, app, route, ev, location=None, name=None):
        item = self._upsert("screens", app, route, {"route": route, "name": name}, ev)
        _set_location(item, location)
        return item

    def add_endpoint(self, app, method, path, ev, location=None, protocol="http",
                     webhook=False, operation=None):
        method = method.upper() if protocol == "http" else method
        key = endpoint_key(method, path) if protocol == "http" else f"{protocol} {path}"
        item = self._upsert("endpoints", app, key, {
            "protocol": protocol, "method": method, "path": path,
            "operation": operation,
        }, ev)
        if webhook:
            item["webhook"] = True
        _set_location(item, location)
        return item

    def add_table(self, app, name, ev, schema=None, kind="table", aliases=()):
        qualified = f"{schema}.{name}" if schema and schema != "public" else name
        item = self._upsert("tables", app, qualified, {
            "name": name, "schema": schema, "kind": kind,
            "columns": [], "primaryKey": [], "uniques": [], "checks": [],
            "indexes": [], "foreignKeys": [], "triggers": [], "aliases": [],
        }, ev)
        for alias in (name, qualified, *aliases):
            self.add_table_alias(item, alias)
        return item

    def add_table_alias(self, table, alias):
        if not alias:
            return
        if alias not in table["aliases"]:
            table["aliases"].append(alias)
        self.table_aliases.setdefault(alias, set()).add(table["_uid"])

    def drop_table(self, app, name, schema=None):
        qualified = f"{schema}.{name}" if schema and schema != "public" else name
        uid = self._uid("tables", app["id"], qualified)
        item = self._items["tables"].pop(uid, None)
        if item:
            for alias in item["aliases"]:
                self.table_aliases.get(alias, set()).discard(uid)

    def add_enum(self, app, name, values, ev):
        item = self._upsert("enums", app, name, {"name": name, "values": []}, ev)
        for v in values:
            if v not in item["values"]:
                item["values"].append(v)
        return item

    def add_sequence(self, app, name, ev):
        return self._upsert("sequences", app, name, {"name": name}, ev)

    def add_integration(self, key, name, ev=None, direction=None, protocol=None,
                        sdk=None, host=None, env_vars=()):
        item = self._upsert("integrations", None, key, {
            "name": name, "directions": [], "protocols": [], "sdk": [],
            "hosts": [], "envVars": [],
        }, ev)
        for field, value in (("directions", direction), ("protocols", protocol),
                             ("sdk", sdk), ("hosts", host)):
            if value and value not in item[field]:
                item[field].append(value)
        for v in env_vars:
            if v not in item["envVars"]:
                item["envVars"].append(v)
        return item

    def add_job(self, app, kind, name, ev, location=None, schedule=None, queue=None,
                trigger=None):
        item = self._upsert("jobs", app, f"{kind}:{name}", {
            "kind": kind, "name": name, "schedule": schedule, "queue": queue,
        }, ev)
        if trigger:
            item["_trigger"] = trigger
        _set_location(item, location)
        return item

    def add_event(self, name, ev=None):
        return self._upsert("events", None, name, {
            "name": name, "publishers": [], "subscribers": [],
        }, ev)

    def add_event_subscriber(self, app, name, ev, location=None):
        event = self.add_event(name, ev)
        ref = _ev_ref(ev)
        if ref not in event["subscribers"]:
            event["subscribers"].append(ref)
        return self.add_entrypoint(app, "event-handler", f"{name} <- {ev['file']}", ev,
                                   target=event["_uid"], location=location)

    def add_event_publisher(self, name, ev):
        event = self.add_event(name, ev)
        ref = _ev_ref(ev)
        if ref not in event["publishers"]:
            event["publishers"].append(ref)
        return event

    def add_command(self, app, name, ev, location=None):
        item = self._upsert("commands", app, name, {"name": name}, ev)
        _set_location(item, location)
        return item

    def add_entrypoint(self, app, ep_type, key, ev, target=None, location=None):
        assert ep_type in ENTRYPOINT_TYPES, ep_type
        uid = f"entrypoints|{app['id'] if app else ''}|{ep_type}:{key}"
        item = self._entrypoints.get(uid)
        if item is None:
            item = {"_uid": uid, "_collection": "entrypoints", "type": ep_type, "key": key,
                    "app": app["id"] if app else None, "target": target,
                    "status": "detected", "unresolved": [], "evidence": []}
            self._entrypoints[uid] = item
        add_evidence(item, ev)
        _set_location(item, location)
        return item

    # --- 関係 -------------------------------------------------------------

    def relate(self, source, field, target_uid, ev, operation=None, tag=None):
        """source の field に target を足す。根拠も一緒に持つ。

        operation は CRUD（テーブルへの関係だけが使う）、tag はそれ以外の補足
        （遷移の種類 push / replace など）。
        """
        rel = source.setdefault("_rel", {}).setdefault(field, {})
        entry = rel.setdefault(target_uid, {"operations": set(), "tags": set(), "evidence": []})
        if operation:
            entry["operations"].add(operation)
        if tag:
            entry["tags"].add(tag)
        if ev and _ev_ref(ev) not in entry["evidence"]:
            entry["evidence"].append(_ev_ref(ev))

    # --- 出力 -------------------------------------------------------------

    def all_entities(self):
        for coll in COLLECTIONS:
            yield from self._items[coll].values()
        yield from self._entrypoints.values()

    def finalize(self, generated_commit, multi_repo):
        """可変な状態から、公開用の Project Model を作る。"""
        self._derive_entrypoints()

        id_map = _assign_ids(self, self.applications)
        out = {
            "schema": MODEL_SCHEMA,
            "version": MODEL_VERSION,
            "generatedCommit": generated_commit,
            "repositories": self.repositories,
            "applications": self.applications,
        }
        for coll in COLLECTIONS:
            out[coll] = sorted(
                (_export(e, id_map, multi_repo) for e in self._items[coll].values()),
                key=lambda e: e["id"],
            )
        out["entrypoints"] = sorted(
            (_export(e, id_map, multi_repo) for e in self._entrypoints.values()),
            key=lambda e: e["id"],
        )
        out["features"] = []
        out["unsupported"] = sorted(self.unsupported, key=lambda u: (u["file"], u["kind"]))
        return out

    def _derive_entrypoints(self):
        """画面・API・ジョブ・コマンドから入口を作る。入口はこれらの上位概念。"""
        for s in self._items["screens"].values():
            app = {"id": s["app"]}
            self.add_entrypoint(app, "screen", s["route"], None, target=s["_uid"],
                                location=s.get("_location"))
        for e in self._items["endpoints"].values():
            app = {"id": e["app"]}
            ep_type = "webhook" if e.get("webhook") else e["protocol"]
            self.add_entrypoint(app, ep_type, e["key"], None, target=e["_uid"],
                                location=e.get("_location"))
        for j in self._items["jobs"].values():
            app = {"id": j["app"]}
            self.add_entrypoint(app, j["kind"], j["name"], None, target=j["_uid"],
                                location=j.get("_location"))
        for c in self._items["commands"].values():
            app = {"id": c["app"]}
            self.add_entrypoint(app, "cli", c["name"], None, target=c["_uid"],
                                location=c.get("_location"))
        for ep in self._entrypoints.values():
            target = self.get(ep["target"]) if ep["target"] else None
            if not ep["evidence"] and target:
                ep["evidence"] = list(target["evidence"])
            if target and target["status"] == "unresolved":
                ep["status"] = "unresolved"
                ep["unresolved"] = list(target["unresolved"])


def add_evidence(item, ev):
    if not ev:
        return
    ref = _ev_ref(ev)
    if ref not in item["evidence"]:
        item["evidence"].append(ref)


def _ev_ref(ev):
    ref = {"file": ev["file"]}
    if ev.get("line"):
        ref["line"] = ev["line"]
    if ev.get("rule"):
        ref["rule"] = ev["rule"]
    if ev.get("repo"):
        ref["repo"] = ev["repo"]
    return ref


def _set_location(item, location):
    if location and "_location" not in item:
        item["_location"] = location


def _assign_ids(builder, applications):
    """自然キーから公開 ID を決める。衝突したものだけアプリ名で区別する。"""
    app_keys = {a["id"]: a["key"] for a in applications}
    id_map = {}
    groups = {}
    for coll in COLLECTIONS:
        for item in builder._items[coll].values():
            groups.setdefault((PREFIX[coll], item["key"]), []).append(item)
    for item in builder._entrypoints.values():
        groups.setdefault(("entrypoint", f"{item['type']}:{item['key']}"), []).append(item)
    for (prefix, key), items in groups.items():
        if len(items) == 1:
            id_map[items[0]["_uid"]] = f"{prefix}:{key}"
            continue
        for item in items:
            app = app_keys.get(item["app"], item["app"] or "global")
            id_map[item["_uid"]] = f"{prefix}:{key}@{app}"
    return id_map


def _export(item, id_map, multi_repo):
    out = {"id": id_map[item["_uid"]]}
    for k, v in item.items():
        if k.startswith("_") or k == "key":
            continue
        out[k] = v
    out["key"] = item["key"]
    if out.get("target"):
        out["target"] = id_map.get(out["target"])
    if "_location" in item:
        loc = item["_location"]
        out["file"] = loc["file"]
        if loc.get("line"):
            out["line"] = loc["line"]
        if multi_repo and loc.get("repo"):
            out["repo"] = loc["repo"]
    for field, rel in item.get("_rel", {}).items():
        out[field] = []
        for uid, r in sorted(rel.items(), key=lambda kv: id_map.get(kv[0], "")):
            if uid not in id_map:
                continue
            entry = {"id": id_map[uid]}
            if r["operations"]:
                entry["operations"] = sorted(r["operations"], key=OPERATIONS.index)
            if r["tags"]:
                entry["tags"] = sorted(r["tags"])
            entry["evidence"] = _strip_repo(r["evidence"], multi_repo)
            out[field].append(entry)
    out["evidence"] = _strip_repo(out["evidence"], multi_repo)
    return out


def _strip_repo(evidence, multi_repo):
    """リポジトリが 1 つなら `repo` は冗長なので落とす。"""
    if multi_repo:
        return evidence
    return [{k: v for k, v in e.items() if k != "repo"} for e in evidence]


# --- 保存と読み込み -----------------------------------------------------------

def model_dir(out_dir):
    return os.path.join(out_dir, ".source-docs")


def model_path(out_dir):
    return os.path.join(model_dir(out_dir), "project-model.json")


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def load_model(out_dir):
    path = model_path(out_dir)
    if not os.path.isfile(path):
        raise SystemExit(f"error: Project Model がありません。先に analyze を実行する: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    errors = validate_model(data)
    if errors:
        raise SystemExit("error: Project Model が壊れている:\n  " + "\n  ".join(errors[:20]))
    return data


def index(model):
    """ID から実体を引ける辞書を作る。"""
    idx = {}
    for coll in COLLECTIONS + ("entrypoints", "features", "applications"):
        for item in model.get(coll, []):
            idx[item["id"]] = item
    return idx


def validate_model(data):
    """スキーマと参照の整合を確かめる。壊れたモデルから資料を作らないため。"""
    errors = []
    if data.get("schema") != MODEL_SCHEMA:
        errors.append(f"schema が {MODEL_SCHEMA} ではない")
    if data.get("version") != MODEL_VERSION:
        errors.append(f"version が {MODEL_VERSION} ではない: {data.get('version')}")
    required = COLLECTIONS + ("repositories", "applications", "entrypoints", "features",
                              "unsupported")
    for key in required:
        if not isinstance(data.get(key), list):
            errors.append(f"{key} が配列ではない")
    if errors:
        return errors

    ids = set()
    for coll in COLLECTIONS + ("entrypoints", "features", "applications"):
        for item in data[coll]:
            if "id" not in item:
                errors.append(f"{coll} に id の無い要素がある")
                continue
            if item["id"] in ids:
                errors.append(f"ID が重複している: {item['id']}")
            ids.add(item["id"])
            if coll in COLLECTIONS + ("entrypoints",):
                if item.get("status") not in STATUSES:
                    errors.append(f"{item['id']}: status が不正: {item.get('status')}")
                if not isinstance(item.get("evidence"), list):
                    errors.append(f"{item['id']}: evidence が配列ではない")

    for ep in data["entrypoints"]:
        if ep["type"] not in ENTRYPOINT_TYPES:
            errors.append(f"{ep['id']}: 未知の入口種別 {ep['type']}")
        if ep.get("target") and ep["target"] not in ids:
            errors.append(f"{ep['id']}: target が存在しない: {ep['target']}")
    for f in data["features"]:
        for field in ("entrypoints", "screens", "endpoints", "integrations", "jobs", "events"):
            for ref in f.get(field, []):
                if ref not in ids:
                    errors.append(f"{f['id']}: {field} の参照先が存在しない: {ref}")
        for t in f.get("tables", []):
            if t["id"] not in ids:
                errors.append(f"{f['id']}: tables の参照先が存在しない: {t['id']}")
    return errors
