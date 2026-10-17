"""テストの共通部品。"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "skills", "source-docs", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
CLI = os.path.join(SCRIPTS, "source_docs.py")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from sdocs.analyzer import analyze  # noqa: E402
from sdocs.model import index  # noqa: E402

_CACHE = {}


def fixture(name):
    return os.path.join(FIXTURES, name)


def model_of(name):
    """fixture を解析したモデル。同じ fixture は 1 回だけ解析する。"""
    if name not in _CACHE:
        _CACHE[name] = analyze([fixture(name)], os.path.join(tempfile.gettempdir(), "sd-unused-out"))
    return _CACHE[name]


def ids(model, coll):
    return sorted(e["id"] for e in model[coll])


def by_id(model):
    return index(model)


def write_project(files):
    """{相対パス: 内容} から一時プロジェクトを作り、そのパスを返す。呼び出し側で消す。"""
    root = tempfile.mkdtemp(prefix="sd-proj-")
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return root


def analyze_files(files):
    root = write_project(files)
    try:
        return analyze([root], os.path.join(root, "docs", "system"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ShopDocs:
    """shop fixture を注釈つきで解析・描画した一時ディレクトリ。"""

    def __init__(self, annotations=True, fmt="md"):
        from sdocs.model import model_path, write_json
        self.out = tempfile.mkdtemp(prefix="sd-docs-")
        self.model = analyze([fixture("shop")], self.out)
        write_json(model_path(self.out), self.model)
        if annotations:
            shutil.copy(fixture("shop-annotations.json"),
                        os.path.join(self.out, ".source-docs", "annotations.json"))
        self.fmt = fmt

    def render(self, **kwargs):
        from sdocs import annotations as ann
        from sdocs.render import render_docs
        return render_docs(self.model, ann.load(self.out), self.out, **kwargs)

    def read(self, rel):
        with open(os.path.join(self.out, *rel.split("/")), encoding="utf-8") as f:
            return f.read()

    def annotations(self):
        from sdocs import annotations as ann
        return ann.load(self.out)

    def write_annotations(self, data):
        with open(os.path.join(self.out, ".source-docs", "annotations.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def cleanup(self):
        shutil.rmtree(self.out, ignore_errors=True)


def try_symlink(testcase, target, link):
    """シンボリックリンクを作る。作れない環境（Windows の既定権限）ではテストを飛ばす。"""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        testcase.skipTest("シンボリックリンクを作れない環境")
