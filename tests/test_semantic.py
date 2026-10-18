"""意味の回帰テスト。`tests/fixtures/semantic/<名前>/expect.json` に書いた期待を満たすか。

文字列の完全一致（golden）ではなく、「どの実体があり、どの関係がどの状態で結ばれ、
何が未確定に送られるか」を確かめる。資料の見た目を直しても壊れず、意味が変わったときだけ落ちる。

期待値の書き方（すべて任意）

    entities          {コレクション: [ID…]}            その種類の実体がちょうどこれだけある
    entrypoints       [ID…]                             入口がちょうどこれだけある
    relations         [{from, field, to, status, operations?}]   確定した関係（本文に出る）
    absentRelations   [{from, field, to}]               本文に出てはいけない関係
    pending           [{entity, field, value, status, candidates?}]  未確定に送られるもの
    envVars           {連携 ID: {変数名: 状態}}           本文に出る環境変数
    absentEnvVars     {連携 ID: [変数名…]}               本文に出てはいけない環境変数
    tables            {テーブル ID: {physicalSource, columns, absentColumns, indexes, physicalForeignKeys,
                                     droppedForeignKeys, ormRelations, renamedFrom}}
    requestSchema     {API の ID: [{name, required, type?, constraints?}]}
    webhooks          [API の ID…]
    diagnostics       [{code, severity}]                少なくともこれらの診断がある
    absentDiagnostics [code…]                           この診断は出ない
    docs              {資料: {bodyContains, bodyNotContains, pendingContains}}
    docsNeverContain  [文字列…]                          書き出したどのファイル（md / json / html）にも無い
"""

import json
import os
import shutil
import tempfile
import unittest

from helpers import FIXTURES

from sdocs.core.analyzer import analyze
from sdocs.core.resolve import resolve
from sdocs.renderers import render_docs, write_evidence
from sdocs.renderers.html import write_site

SEMANTIC = os.path.join(FIXTURES, "semantic")
COLLECTIONS = ("screens", "endpoints", "tables", "integrations", "jobs", "events")


def _subset(expected, actual):
    return all(actual.get(k) == v for k, v in expected.items())


class SemanticCase:
    name = None

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(SEMANTIC, cls.name, "expect.json"), encoding="utf-8") as f:
            cls.expect = json.load(f)
        cls.out = tempfile.mkdtemp(prefix=f"sd-sem-{cls.name}-")
        cls.model = analyze([os.path.join(SEMANTIC, cls.name)], cls.out)
        cls.view = resolve(cls.model, {})
        cls.idx = {i["id"]: i for c in COLLECTIONS + ("entrypoints",) for i in cls.view[c]}
        cls.written = render_docs(resolve(cls.model, {}), cls.out)
        cls.written.append(write_evidence(resolve(cls.model, {}), cls.out))
        cls.written += write_site(cls.out, os.path.join(cls.out, "html"), mermaid=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_entities(self):
        for coll, ids in self.expect.get("entities", {}).items():
            self.assertEqual(sorted(i["id"] for i in self.view[coll]), sorted(ids), coll)
        if "entrypoints" in self.expect:
            self.assertEqual(sorted(e["id"] for e in self.view["entrypoints"]), sorted(self.expect["entrypoints"]))

    def test_relations(self):
        for r in self.expect.get("relations", []):
            rels = {x["id"]: x for x in self.idx[r["from"]].get(r["field"], [])}
            self.assertIn(r["to"], rels, r)
            self.assertEqual(rels[r["to"]]["status"], r["status"], r)
            if "operations" in r:
                self.assertEqual(rels[r["to"]]["operations"], r["operations"], r)
        for r in self.expect.get("absentRelations", []):
            self.assertNotIn(r["to"], [x["id"] for x in self.idx[r["from"]].get(r["field"], [])], r)

    def test_pending(self):
        for p in self.expect.get("pending", []):
            found = [x for x in self.idx[p["entity"]].get("pending", [])
                     if x["field"] == p["field"] and x["value"] == p["value"]]
            self.assertTrue(found, p)
            self.assertEqual(found[0]["status"], p["status"], p)
            if "candidates" in p:
                self.assertEqual(found[0].get("candidates"), p["candidates"], p)

    def test_env_vars(self):
        for integ, env in self.expect.get("envVars", {}).items():
            got = {e["name"]: e["status"] for e in self.idx[integ]["envVarDetails"]}
            self.assertEqual(got, env, integ)
        for integ, names in self.expect.get("absentEnvVars", {}).items():
            for n in names:
                self.assertNotIn(n, self.idx[integ]["envVars"], integ)

    def test_tables(self):
        for tid, spec in self.expect.get("tables", {}).items():
            t = self.idx[tid]
            if "physicalSource" in spec:
                self.assertEqual(t["physicalSource"], spec["physicalSource"], tid)
            cols = {c["name"]: c for c in t["columns"]}
            for name, attrs in spec.get("columns", {}).items():
                self.assertIn(name, cols, tid)
                self.assertTrue(_subset(attrs, cols[name]), (tid, name, cols[name]))
            for name in spec.get("absentColumns", []):
                self.assertNotIn(name, cols, tid)
            for key in ("indexes", "physicalForeignKeys", "ormRelations"):
                for want in spec.get(key, []):
                    self.assertTrue(any(_subset({k: v for k, v in want.items() if k != "references"}, x)
                                        and _subset(want.get("references", {}), x.get("references", {}))
                                        for x in t[key]), (tid, key, want, t[key]))
            for key in ("droppedForeignKeys", "renamedFrom"):
                if key in spec:
                    self.assertEqual(t.get(key), spec[key], tid)

    def test_request_schema(self):
        for eid, fields in self.expect.get("requestSchema", {}).items():
            got = {f["name"]: f for f in self.idx[eid]["requestSchema"]["fields"]}
            self.assertEqual(sorted(got), sorted(f["name"] for f in fields), eid)
            for f in fields:
                self.assertTrue(_subset(f, got[f["name"]]), (eid, f, got[f["name"]]))

    def test_webhooks(self):
        for eid in self.expect.get("webhooks", []):
            self.assertTrue(self.idx[eid].get("webhook"), eid)

    def test_diagnostics(self):
        codes = [(d["code"], d["severity"]) for d in self.view["diagnostics"]]
        for d in self.expect.get("diagnostics", []):
            self.assertIn((d["code"], d["severity"]), codes)
        for code in self.expect.get("absentDiagnostics", []):
            self.assertNotIn(code, [c for c, _ in codes])

    def test_docs(self):
        for doc, spec in self.expect.get("docs", {}).items():
            with open(os.path.join(self.out, doc), encoding="utf-8") as f:
                text = f.read()
            body, _, pending = text.partition("\n## 未確定\n")
            pending = pending.split("\n## 未確認\n")[0]
            for s in spec.get("bodyContains", []):
                self.assertIn(s, body, doc)
            for s in spec.get("bodyNotContains", []):
                self.assertNotIn(s, body, doc)
            for s in spec.get("pendingContains", []):
                self.assertIn(s, pending, doc)

    def test_docs_never_contain(self):
        for path in self.written:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for s in self.expect.get("docsNeverContain", []):
                self.assertNotIn(s, text, path)


def _make(name):
    cls_name = "Semantic_" + name.replace("-", "_")
    return type(cls_name, (SemanticCase, unittest.TestCase), {"name": name})


for _name in sorted(os.listdir(SEMANTIC)):
    if os.path.isfile(os.path.join(SEMANTIC, _name, "expect.json")):
        globals()["Semantic_" + _name.replace("-", "_")] = _make(_name)


class DeterminismTest(unittest.TestCase):
    """同じ入力からは、バイト単位で同じ Project Model と資料ができる。"""

    def test_same_input_same_output(self):
        outs = []
        try:
            for _ in range(2):
                out = tempfile.mkdtemp(prefix="sd-det-")
                outs.append(out)
                for name in sorted(os.listdir(SEMANTIC)):
                    root = os.path.join(SEMANTIC, name)
                    sub = os.path.join(out, name)
                    model = analyze([root], sub)
                    model["repositories"] = [dict(r, path="<repo>") for r in model["repositories"]]
                    os.makedirs(sub, exist_ok=True)
                    with open(os.path.join(sub, "model.json"), "w", encoding="utf-8") as f:
                        json.dump(model, f, ensure_ascii=False, indent=2)
                    render_docs(resolve(model, {}), sub)
                    write_evidence(resolve(model, {}), sub)
            self.assertEqual(_tree(outs[0]), _tree(outs[1]))
        finally:
            for out in outs:
                shutil.rmtree(out, ignore_errors=True)


def _tree(root):
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out


if __name__ == "__main__":
    unittest.main()
