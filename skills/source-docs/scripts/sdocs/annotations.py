"""注釈（annotations.json）の読み込みと検査。

注釈は、コードから機械では決められない意味づけを Claude が書く場所。
機能の業務名・目的・入出力・詳細設計などがここに入る。

**注釈も「根拠のあることしか書かない」を守る。** 検査するのは次のとおり。

- 参照している ID（機能・入口・API・テーブル…）が Project Model に実在する
- 根拠として挙げたファイルが実在し、開いてよいファイルである
- 「未確認」には、調べた範囲（searched）が書いてある
- `implemented` と書いた項目には根拠がある
- 機密の値が混ざっていない
"""

import json
import os

from . import ANNOTATIONS_SCHEMA, ANNOTATIONS_VERSION, security
from .features import apply_annotations
from .model import OPERATIONS, index, model_dir
from .secrets import scan

STATUS = ("implemented", "not-implemented", "unverified")
PRECONDITION_KINDS = ("authentication", "authorization", "role", "ownership", "business", "other")
SIDE_EFFECT_KINDS = ("email", "notification", "event", "queue", "cache", "file", "external-api", "other")
CONCURRENCY_KINDS = ("optimistic-lock", "pessimistic-lock", "unique-constraint", "duplicate-check",
                     "advisory-lock", "other")
STEP_KINDS = ("call", "return", "async", "note")
PARTICIPANT_KINDS = ("user", "screen", "frontend", "backend", "service", "db", "cache", "queue",
                     "worker", "external")


# 配列で書く項目と、各要素に必須のキー
ENTITY_SHAPES = {
    "request": ("name",), "responses": ("status",), "errors": ("status",), "validation": ("rule",),
    "inputs": ("name",), "displays": ("name",), "actions": ("label",), "unverified": ("item",),
}
DETAIL_SHAPES = {
    "preconditions": ("kind",), "inputs": ("name",), "validations": ("condition",),
    "branches": ("condition",), "dataImpact": ("table", "operation"), "externalCalls": ("integration",),
    "sideEffects": ("kind",), "files": ("path",), "unverified": ("item",),
}


def path(out_dir):
    return os.path.join(model_dir(out_dir), "annotations.json")


def empty():
    return {"schema": ANNOTATIONS_SCHEMA, "version": ANNOTATIONS_VERSION, "system": {},
            "applications": {}, "api": {}, "features": [], "entities": {}, "exclusions": [],
            "flows": {}, "details": {}}


def load(out_dir):
    p = path(out_dir)
    if not os.path.isfile(p):
        return empty()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    base = empty()
    base.update(data)
    return base


def normalize_evidence(ev):
    """`"src/a.ts:12"` と `{"file": "src/a.ts", "line": 12}` の両方を受ける。"""
    if isinstance(ev, str):
        file, _, line = ev.partition(":")
        return {"file": file, "line": int(line)} if line.isdigit() else {"file": ev}
    return ev


class Checker:
    def __init__(self, model, annotations, out_dir):
        self.model = model
        self.ann = annotations
        self.out_dir = out_dir
        self.idx = index(model)
        self.errors = []
        self.warnings = []
        self.secret_findings = []
        self.repo_roots = {
            r["key"]: os.path.normpath(os.path.join(out_dir, r["path"]))
            for r in model["repositories"]
        }

    def err(self, where, message):
        self.errors.append(f"{where}: {message}")

    def warn(self, where, message):
        self.warnings.append(f"{where}: {message}")

    # --- 共通 ----------------------------------------------------------------

    def evidence(self, where, items, required=False):
        if items is None:
            items = []
        if not isinstance(items, list):
            self.err(where, "evidence は配列で書く")
            return
        if required and not items:
            self.err(where, "根拠（evidence）が無い。implemented と書くなら、そう判断したファイルを挙げる")
        for raw in items:
            ev = normalize_evidence(raw)
            file = ev.get("file") if isinstance(ev, dict) else None
            if not file:
                self.err(where, f"evidence の形が不正: {raw!r}")
                continue
            if security.is_secret_path(file):
                self.err(where, f"開いてはいけないファイルを根拠にしている: {file}")
                continue
            repo = ev.get("repo")
            roots = [self.repo_roots[repo]] if repo in self.repo_roots else list(self.repo_roots.values())
            ok = False
            for root in roots:
                full = os.path.join(root, *file.split("/"))
                if os.path.isfile(full) and security.is_inside(root, full) \
                        and not security.is_unsafe_to_open(full):
                    ok = True
                    break
            if not ok:
                self.err(where, f"根拠のファイルが存在しない: {file}")

    def unverified(self, where, items):
        for u in items or []:
            if not isinstance(u, dict) or not u.get("item"):
                self.err(where, f"未確認の項目には item（何が分からないか）が要る: {u!r}")
                continue
            if not u.get("searched"):
                self.err(where, f"未確認「{u['item']}」に searched（調べた範囲）が無い")

    def known(self, where, ref_id):
        """注釈の対象（最上位のキー）が Project Model にあるか。

        無い場合はエラーにしない。コードの変更や解析器の改善で候補が消えることは、資料を育てる
        使い方では普通に起きる。そのたびに再生成ができなくなると資料が古いまま止まる。
        警告として「古い注釈」を知らせ、その注釈だけを資料に反映しない。
        """
        if ref_id in self.idx:
            return True
        self.warn(where, f"古い注釈: Project Model に {ref_id} が無い（資料には反映しない。消すか付け直す）")
        return False

    def ref(self, where, ref_id, prefix=None):
        if ref_id not in self.idx:
            self.err(where, f"Project Model に無い ID を参照している: {ref_id}")
            return False
        if prefix and not ref_id.startswith(prefix + ":"):
            self.err(where, f"{prefix} の ID を書く場所に {ref_id} がある")
            return False
        return True

    def status(self, where, block):
        if block is None:
            return
        if not isinstance(block, dict):
            self.err(where, "オブジェクトで書く")
            return
        st = block.get("status")
        if st not in STATUS:
            self.err(where, f"status は {' / '.join(STATUS)} のどれか: {st!r}")
        if st == "implemented":
            self.evidence(where, block.get("evidence"), required=True)
        else:
            self.evidence(where, block.get("evidence"))
        if st == "unverified" and not block.get("searched"):
            self.err(where, "unverified なら searched（調べた範囲）を書く")

    # --- 本体 ----------------------------------------------------------------

    def run(self):
        a = self.ann
        if a.get("schema") != ANNOTATIONS_SCHEMA:
            self.err("annotations", f"schema は {ANNOTATIONS_SCHEMA}")
        if a.get("version") != ANNOTATIONS_VERSION:
            self.err("annotations", f"version は {ANNOTATIONS_VERSION}")

        self.evidence("system", (a.get("system") or {}).get("evidence"))
        self.unverified("system", (a.get("system") or {}).get("unverified"))
        self.evidence("api", (a.get("api") or {}).get("evidence"))
        self.unverified("api", (a.get("api") or {}).get("unverified"))
        for app_id, spec in (a.get("applications") or {}).items():
            if self.ref(f"applications.{app_id}", app_id, "app"):
                self.evidence(f"applications.{app_id}", spec.get("evidence"))

        self._features()
        self._missing()
        for ent_id, spec in (a.get("entities") or {}).items():
            where = f"entities.{ent_id}"
            if not self.known(where, ent_id):
                continue
            self.evidence(where, spec.get("evidence"))
            self.unverified(where, spec.get("unverified"))
            self._shape(where, spec, ENTITY_SHAPES)
            for resp in spec.get("responses") or []:
                if isinstance(resp, dict):
                    self._shape(f"{where}.responses", resp, {"fields": ("name",)})
            if spec.get("columns") is not None and not (
                    isinstance(spec["columns"], dict)
                    and all(isinstance(v, str) for v in spec["columns"].values())):
                self.err(where, 'columns は {"カラム名": "説明"} の形で書く')
            if not all(isinstance(act, dict) for act in spec.get("actions") or []):
                continue  # 形の誤りは _shape が報告済み
            for act in spec.get("actions", []) or []:
                if act.get("api"):
                    self.ref(f"{where}.actions", act["api"], "endpoint")
                if act.get("to"):
                    self.ref(f"{where}.actions", act["to"], "screen")
        for ex in a.get("exclusions") or []:
            if self.known("exclusions", ex.get("id", "")) and not ex.get("reason"):
                self.err("exclusions", f"{ex['id']} に除外の理由（reason）が無い")

        features = {f["id"]: f for f in apply_annotations(self.model, a)}
        for fid, spec in (a.get("flows") or {}).items():
            self._flow(fid, spec, features)
        for fid, spec in (a.get("details") or {}).items():
            self._detail(fid, spec, features)
        self._secrets()
        return self

    def _missing(self):
        """注釈が足りない API・画面・テーブルを数える。資料が「未確認」だらけになるのを防ぐ。"""
        notes = self.ann.get("entities") or {}
        excluded = {e.get("id") for e in self.ann.get("exclusions") or []}
        gaps = {"API": [], "画面": [], "テーブル": []}
        for e in self.model["endpoints"]:
            n = notes.get(e["id"]) or {}
            if e["id"] in excluded:
                continue
            need = ["summary"] if not e.get("spec") else []
            if not e.get("spec"):
                if not (n.get("request") or n.get("noRequest")):
                    need.append("request")
                if not n.get("responses"):
                    need.append("responses")
            if not n.get("summary") and "summary" not in need:
                need.append("summary")
            if need and not all(n.get(k) for k in need):
                gaps["API"].append(e["id"])
        for s in self.model["screens"]:
            n = notes.get(s["id"]) or {}
            if s["id"] not in excluded and not (n.get("name") and n.get("summary")):
                gaps["画面"].append(s["id"])
        for t in self.model["tables"]:
            if t["id"] not in excluded and not (notes.get(t["id"]) or {}).get("summary"):
                gaps["テーブル"].append(t["id"])
        for kind, ids in gaps.items():
            if ids:
                more = f" ほか {len(ids) - 8} 件" if len(ids) > 8 else ""
                self.warn("entities", f"注釈の無い{kind}が {len(ids)} 件: {', '.join(ids[:8])}{more}")

    def _features(self):
        names = set()
        candidates = {f["id"] for f in self.model["features"]}
        for i, spec in enumerate(self.ann.get("features") or []):
            fid = spec.get("id", "")
            where = f"features[{i}] {fid}"
            if not fid.startswith("feature:"):
                self.err(where, "id は feature: で始める")
            if not spec.get("name"):
                self.err(where, "name（業務上の機能名）が無い")
            elif spec["name"] in names:
                self.err(where, f"機能名が重複している: {spec['name']}")
            names.add(spec.get("name"))
            if spec.get("entrypoints"):
                for ep in spec["entrypoints"]:
                    if not ep.startswith("entrypoint:"):
                        self.err(where, f"entrypoints には entrypoint の ID を書く: {ep}")
                    else:
                        self.known(where, ep)
                for r in spec.get("replaces", []):
                    if r not in candidates:
                        self.err(where, f"replaces に候補に無い機能がある: {r}")
            elif fid not in candidates:
                self.warn(where, "古い注釈: 候補に無い ID（資料には反映しない）。"
                                 "新しくまとめるなら entrypoints を書く")
            self.evidence(where, spec.get("evidence"))

    def _flow(self, fid, spec, features):
        where = f"flows.{fid}"
        if fid not in features:
            self.warn(where, "古い注釈: 機能が存在しない（資料には反映しない）")
            return
        self._shape(where, spec, {"inputs": ("name",), "outputs": ("name",), "failures": ("condition",),
                                  "unverified": ("item",)})
        steps = spec.get("steps") or []
        if not all(isinstance(x, str) for x in steps):
            self.err(where, "steps は文字列の配列で書く")
        if len(steps) > 5:
            self.err(where, f"処理の要点は 5 行まで（{len(steps)} 行ある）。それ以上は detail に書く")
        self.evidence(where, spec.get("evidence"))
        self.unverified(where, spec.get("unverified"))

    def _shape(self, where, spec, shapes):
        """配列で書く項目が、オブジェクトの配列になっているか。描画で落ちないよう先に止める。"""
        if not isinstance(spec, dict):
            self.err(where, "オブジェクトで書く")
            return False
        ok = True
        for key, required in shapes.items():
            value = spec.get(key)
            if value is None:
                continue
            if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
                self.err(where, f"{key} はオブジェクトの配列で書く（例: [{{\"{required[0] if required else 'name'}\": \"…\"}}]）")
                ok = False
                continue
            for i, x in enumerate(value):
                missing = [k for k in required if not x.get(k) and x.get(k) != 0]
                if missing:
                    self.err(where, f"{key}[{i}] に {', '.join(missing)} が無い")
                    ok = False
        return ok

    def _detail(self, fid, d, features):
        where = f"details.{fid}"
        if fid not in features:
            self.warn(where, "古い注釈: 機能が存在しない（資料には反映しない）")
            return
        if not self._shape(where, d, DETAIL_SHAPES):
            return
        for i, p in enumerate(d.get("preconditions") or []):
            if p.get("kind") not in PRECONDITION_KINDS:
                self.err(f"{where}.preconditions[{i}]", f"kind は {' / '.join(PRECONDITION_KINDS)}")
            self.evidence(f"{where}.preconditions[{i}]", p.get("evidence"))
        for i, di in enumerate(d.get("dataImpact") or []):
            w = f"{where}.dataImpact[{i}]"
            self.ref(w, di.get("table", ""), "table")
            if di.get("operation") not in OPERATIONS:
                self.err(w, f"operation は {' / '.join(OPERATIONS)}")
            self.evidence(w, di.get("evidence"))
        for key in ("transaction", "idempotency"):
            if d.get(key) is not None:
                self.status(f"{where}.{key}", d[key])
        conc = d.get("concurrency")
        if conc is not None:
            self.status(f"{where}.concurrency", conc)
            for m in conc.get("mechanisms") or []:
                if m.get("kind") not in CONCURRENCY_KINDS:
                    self.err(f"{where}.concurrency", f"kind は {' / '.join(CONCURRENCY_KINDS)}")
        st = d.get("stateTransition")
        if st:
            states = set(st.get("states") or [])
            for t in st.get("transitions") or []:
                for side in ("from", "to"):
                    if t.get(side) not in states and t.get(side) not in ("[*]",):
                        self.err(f"{where}.stateTransition", f"{side} の状態 {t.get(side)!r} が states に無い")
            self.evidence(f"{where}.stateTransition", st.get("evidence"), required=True)
        for i, x in enumerate(d.get("externalCalls") or []):
            self.ref(f"{where}.externalCalls[{i}]", x.get("integration", ""), "integration")
            self.evidence(f"{where}.externalCalls[{i}]", x.get("evidence"))
        for i, s in enumerate(d.get("sideEffects") or []):
            if s.get("kind") not in SIDE_EFFECT_KINDS:
                self.err(f"{where}.sideEffects[{i}]", f"kind は {' / '.join(SIDE_EFFECT_KINDS)}")
            self.evidence(f"{where}.sideEffects[{i}]", s.get("evidence"))
        self.evidence(f"{where}.files", [{"file": f.get("path", "")} for f in d.get("files") or []])
        self._sequence(where, d.get("sequence"))
        self.unverified(where, d.get("unverified"))

    def _sequence(self, where, seq):
        if not seq:
            return
        ids = set()
        for p in seq.get("participants") or []:
            if not p.get("id") or not p.get("label"):
                self.err(f"{where}.sequence", "participant には id と label が要る")
            if p.get("kind") and p["kind"] not in PARTICIPANT_KINDS:
                self.err(f"{where}.sequence", f"participant の kind は {' / '.join(PARTICIPANT_KINDS)}")
            ids.add(p.get("id"))
        if len(ids) > 8:
            self.warn(f"{where}.sequence", f"登場者が {len(ids)} ある。6 を超えたら機能を分けることを考える")

        def walk(steps, w):
            for s in steps or []:
                if "alt" in s:
                    for branch in s["alt"]:
                        walk(branch.get("steps"), w)
                    continue
                if s.get("kind", "call") not in STEP_KINDS:
                    self.err(w, f"step の kind は {' / '.join(STEP_KINDS)}")
                for side in ("from", "to"):
                    if s.get(side) and s[side] not in ids:
                        self.err(w, f"participants に無い登場者: {s[side]}")
        walk(seq.get("steps"), f"{where}.sequence")

    def _secrets(self):
        text = json.dumps(self.ann, ensure_ascii=False, indent=1)
        self.secret_findings = scan(text, [])


def check(model, annotations, out_dir):
    return Checker(model, annotations, out_dir).run()
