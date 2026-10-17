#!/usr/bin/env python3
"""source-docs のコマンドライン。

    analyze   ソースを解析して Project Model を作る
    features  機能の候補（と確定済みの機能）を一覧する
    show      1 つの実体の中身・関係・根拠を見る
    scaffold  詳細設計・処理概要の注釈の雛形を作る
    check     注釈（annotations.json）を検査する
    render    資料（Markdown / HTML）と evidence.json・coverage.json を書く
    coverage  コードにあるもののうち、資料に載っている割合（source → docs）
    verify    資料に書いた識別子がコードに実在するか（docs → source）
    secrets   資料に機密が混ざっていないか
    html      Markdown を HTML にする

終了コード: 0 正常 / 1 検出あり（人間の判断が必要）/ 2 引数・入出力の誤り
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sdocs import annotations as ann_mod  # noqa: E402
from sdocs import coverage as cov_mod  # noqa: E402
from sdocs import secrets as sec_mod  # noqa: E402
from sdocs.analyzer import analyze  # noqa: E402
from sdocs.features import TYPE_LABEL, apply_annotations, feature_slug  # noqa: E402
from sdocs.model import index, load_model, model_dir, model_path, write_json  # noqa: E402
from sdocs.render import find_feature, render_docs, write_evidence  # noqa: E402
from sdocs.render.html import write_site  # noqa: E402
from sdocs.verify import verify as verify_docs  # noqa: E402

DEFAULT_OUT = os.path.join("docs", "system")


def _out(args):
    return args.out or DEFAULT_OUT


# --- analyze ---------------------------------------------------------------------

def cmd_analyze(args):
    for r in args.repo:
        if not os.path.isdir(r):
            print(f"error: リポジトリが見つかりません: {r}", file=sys.stderr)
            return 2
    model = analyze(args.repo, _out(args))
    path = model_path(_out(args))
    write_json(path, model)
    if args.json:
        print(json.dumps(model, ensure_ascii=False, indent=2))
        return 0
    _print_summary(model)
    print(f"\nWROTE {path}")
    return 0


def _print_summary(model):
    for r in model["repositories"]:
        print(f"REPO {r['key']}  files={r['files']}  commit={(r.get('commit') or '不明')[:12]}")
    for app in model["applications"]:
        s = app["stack"]
        print(f"APP  {app['name'] or app['key']}  path={app['path']}  kind={app['kind']}")
        for key in ("frontend", "backend", "orm", "db"):
            print(f"       {key:<9}{', '.join(s[key]) or '判定できず'}")
        print(f"       adapters {', '.join(app['adapters'])}")
    counts = {k: len(model[k]) for k in ("screens", "endpoints", "tables", "integrations", "jobs",
                                         "events", "commands", "entrypoints")}
    print("COUNTS " + " / ".join(f"{k} {v}" for k, v in counts.items()))
    _print_features(model, model["features"])
    if model["unsupported"]:
        print(f"UNSUPPORTED {len(model['unsupported'])}")
        for u in model["unsupported"]:
            print(f"  {u['file']}: {u['reason']}")


def _print_features(model, features):
    idx = index(model)
    no_screen = sum(1 for f in features if not f["screens"])
    print(f"FEATURES {len(features)}（画面を持たないもの {no_screen}）")
    for f in features:
        tables = ", ".join(f"{idx[t['id']]['key']}({''.join(o[0] for o in t['operations'])})"
                           for t in f["tables"] if t["id"] in idx)
        integ = ", ".join(idx[i]["name"] for i in f["integrations"] if i in idx)
        apis = ", ".join(idx[e]["key"] for e in f["endpoints"] if e in idx)
        status = "確定" if f["status"] == "confirmed" else "候補"
        print(f"  {f['id']}  [{TYPE_LABEL.get(f['type'], f['type'])}/{status}] {f['name']}")
        detail = " | ".join(x for x in (apis, tables, integ) if x)
        if detail:
            print(f"      {detail}")


# --- features / show / scaffold ---------------------------------------------------

def cmd_features(args):
    model = load_model(_out(args))
    annotations = ann_mod.load(_out(args))
    features = apply_annotations(model, annotations)
    if args.json:
        print(json.dumps(features, ensure_ascii=False, indent=2))
    else:
        _print_features(model, features)
    return 0


def cmd_show(args):
    model = load_model(_out(args))
    annotations = ann_mod.load(_out(args))
    idx = index(model)
    features = apply_annotations(model, annotations)
    item = idx.get(args.id) or find_feature(features, args.id)
    if item is None:
        print(f"error: 見つからない: {args.id}", file=sys.stderr)
        return 2
    out = {"entity": item}
    note = (annotations.get("entities") or {}).get(item["id"])
    if note:
        out["annotation"] = note
    out["features"] = [f["id"] for f in features
                       if item["id"] in f["entrypoints"] + f["screens"] + f["endpoints"]
                       + f["integrations"] + f["jobs"] + f["events"]
                       or any(t["id"] == item["id"] for t in f["tables"])]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def scaffold(model, feature, kind):
    """詳細設計・処理概要の注釈の雛形。モデルから分かることだけを埋める。"""
    idx = index(model)
    if kind == "flow":
        return {"summary": "", "inputs": [], "steps": [], "outputs": [], "failures": [],
                "evidence": [], "unverified": []}
    participants = [{"id": "user", "label": "利用者", "kind": "user"}] if feature["screens"] else []
    if feature["screens"]:
        participants.append({"id": "screen", "label": "画面", "kind": "screen"})
    participants.append({"id": "backend", "label": "バックエンド", "kind": "backend"})
    if feature["tables"]:
        participants.append({"id": "db", "label": "DB", "kind": "db"})
    for i in feature["integrations"]:
        participants.append({"id": feature_slug(i).replace("-", "_").replace(".", "_"),
                             "label": idx[i]["name"], "kind": "external"})
    impact = []
    for t in feature["tables"]:
        for op in t["operations"]:
            impact.append({"table": t["id"], "operation": op, "columns": [], "meaning": ""})
    return {
        "summary": "", "start": "", "end": "", "preconditions": [], "inputs": [], "validations": [],
        "sequence": {"participants": participants, "steps": []}, "branches": [],
        "dataImpact": impact,
        "transaction": {"status": "unverified", "searched": "", "evidence": []},
        "idempotency": {"status": "unverified", "searched": "", "evidence": []},
        "concurrency": None,
        "externalCalls": [{"integration": i, "request": "", "response": "", "retry": "", "timeout": "",
                           "failure": "", "evidence": []} for i in feature["integrations"]],
        "sideEffects": [], "files": [{"role": "", "path": f} for f in feature["implementationFiles"]],
        "unverified": [],
    }


def cmd_scaffold(args):
    out_dir = _out(args)
    model = load_model(out_dir)
    annotations = ann_mod.load(out_dir)
    feature = find_feature(apply_annotations(model, annotations), args.feature)
    if feature is None:
        print(f"error: 機能が見つからない: {args.feature}（`features` で候補を確認する）", file=sys.stderr)
        return 2
    spec = scaffold(model, feature, args.kind)
    if args.write:
        key = "details" if args.kind == "detail" else "flows"
        if feature["id"] in (annotations.get(key) or {}):
            print(f"error: {key} に {feature['id']} は既にある。上書きしない", file=sys.stderr)
            return 2
        annotations.setdefault(key, {})[feature["id"]] = spec
        write_json(ann_mod.path(out_dir), annotations)
        print(f"WROTE {ann_mod.path(out_dir)} の {key}.{feature['id']}")
        return 0
    print(json.dumps({feature["id"]: spec}, ensure_ascii=False, indent=2))
    return 0


# --- check / render ----------------------------------------------------------------

def _check(out_dir, quiet=False):
    model = load_model(out_dir)
    annotations = ann_mod.load(out_dir)
    result = ann_mod.check(model, annotations, out_dir)
    if not quiet or result.errors:
        for e in result.errors:
            print(f"  ERROR {e}")
        for w in result.warnings:
            print(f"  WARN  {w}")
    return model, annotations, result


def cmd_check(args):
    model, annotations, result = _check(_out(args))
    blocking = sec_mod.blocking(result.secret_findings)
    for f in sec_mod.masked(result.secret_findings):
        print(f"  SECRET L{f['line']} [{f['label']}] {f['value']}{' (ダミーの可能性)' if f['looksDummy'] else ''}")
    ok = not result.errors and not result.secret_findings
    print(f"CHECK errors={len(result.errors)} warnings={len(result.warnings)} "
          f"secrets={len(result.secret_findings)} RESULT={'OK' if ok else 'REVIEW_REQUIRED'}")
    return 0 if ok and not blocking else 1


def cmd_render(args):
    out_dir = _out(args)
    model, annotations, result = _check(out_dir)
    if result.errors:
        print(f"error: 注釈に誤りが {len(result.errors)} 件ある。直してから再実行する", file=sys.stderr)
        return 1
    blocking = sec_mod.blocking(result.secret_findings)
    if blocking:
        for f in sec_mod.masked(blocking):
            print(f"  SECRET L{f['line']} [{f['label']}] {f['value']}")
        print("error: 注釈に認証情報らしき値がある。資料に書き出さずに止めた。人間が確認する", file=sys.stderr)
        return 1

    only = None
    if args.only is not None:
        only = [x.strip() for x in args.only.split(",") if x.strip()]
    kinds = (args.kind,) if args.kind else ("flow", "detail")
    written = render_docs(model, annotations, out_dir, only=only, feature_keys=args.feature or (),
                          kinds=kinds)
    written.append(write_evidence(model, annotations, out_dir))
    cov = cov_mod.compute(model, annotations, out_dir)
    cov_path = os.path.join(model_dir(out_dir), "coverage.json")
    write_json(cov_path, cov)
    written.append(cov_path)
    if args.format in ("html", "both"):
        written += write_site(out_dir, os.path.join(out_dir, "html"), mermaid=not args.no_mermaid)
    for p in written:
        print(f"WROTE {p}")

    findings = sec_mod.scan_paths([p for p in written if p.endswith(".md")])
    total = sum(len(v) for v in findings.values())
    for path, items in findings.items():
        for f in sec_mod.masked(items):
            print(f"  SECRET {path} L{f['line']} [{f['label']}] {f['value']}"
                  f"{' (ダミーの可能性)' if f['looksDummy'] else ''}")
    print(f"SECRETS={total} RESULT={'OK' if not total else 'REVIEW_REQUIRED'}")
    print(cov_mod.format_text(cov))
    return 0 if not total else 1


# --- coverage / verify / secrets / html -----------------------------------------------

def cmd_coverage(args):
    out_dir = _out(args)
    model = load_model(out_dir)
    cov = cov_mod.compute(model, ann_mod.load(out_dir), out_dir)
    write_json(os.path.join(model_dir(out_dir), "coverage.json"), cov)
    if args.json:
        print(json.dumps(cov, ensure_ascii=False, indent=2))
    else:
        print(cov_mod.format_text(cov))
    if args.fail_under is not None:
        low = [k for k, r in cov["kinds"].items()
               if r["coverage"] is not None and r["coverage"] * 100 < args.fail_under]
        if low:
            print(f"FAIL coverage < {args.fail_under}%: {', '.join(low)}")
            return 1
    return 0


def cmd_verify(args):
    out_dir = _out(args)
    model = load_model(out_dir)
    if args.doc:
        with open(args.doc, encoding="utf-8") as f:
            docs = {os.path.relpath(args.doc, out_dir).replace(os.sep, "/"): f.read()}
    else:
        docs = cov_mod.read_docs(out_dir)
    result = verify_docs(model, out_dir, docs, mode=args.mode)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        counts = " ".join(f"{k}={v}" for k, v in result["checked"].items())
        print(f"VERIFY docs={len(docs)} CHECKED {counts} RESULT={'OK' if result['ok'] else 'MISMATCH'}")
        for f in result["findings"]:
            print(f"  {f['doc']} [{f['kind']}] {f['value']} — {f['reason']}")
    return 0 if result["ok"] else 1


def cmd_secrets(args):
    terms = []
    if args.terms:
        with open(args.terms, encoding="utf-8") as f:
            terms = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not os.path.exists(args.doc):
        print(f"error: 見つからない: {args.doc}", file=sys.stderr)
        return 2
    found = sec_mod.scan_paths([args.doc], terms)
    total = sum(len(v) for v in found.values())
    if args.json:
        shown = {p: (v if args.reveal else sec_mod.masked(v)) for p, v in found.items()}
        print(json.dumps({"ok": not total, "findings": shown,
                          "note": "検出は誤検知を含む。伏せ字にするかどうかは人間が判断する。"},
                         ensure_ascii=False, indent=2))
    else:
        print(f"SECRETS={total} RESULT={'OK' if not total else 'REVIEW_REQUIRED'}")
        for path, items in found.items():
            for f in (items if args.reveal else sec_mod.masked(items)):
                print(f"  {path} L{f['line']} [{f['label']}] {f['value']}"
                      f"{' (ダミーの可能性)' if f['looksDummy'] else ''}")
                if args.reveal:
                    print(f"      {f['context']}")
        if total and not args.reveal:
            print("  値と文脈は伏せた。中身を見るなら --reveal を付けるか、資料の該当行を直接開く")
    return 0 if not total else 1


def cmd_html(args):
    for p in write_site(args.src, args.out, mermaid=not args.no_mermaid):
        print(p)
    return 0


# --- 引数 -------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(prog="source_docs.py", description="既存コードから資料を作る")
    sub = ap.add_subparsers(dest="command", required=True)

    def with_out(p):
        p.add_argument("--out", help=f"資料の出力先（既定: {DEFAULT_OUT}）")
        return p

    p = with_out(sub.add_parser("analyze", help="ソースを解析して Project Model を作る"))
    p.add_argument("--repo", action="append", required=True, help="対象リポジトリ（複数可）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = with_out(sub.add_parser("features", help="機能を一覧する"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_features)

    p = with_out(sub.add_parser("show", help="実体の中身・関係・根拠を見る"))
    p.add_argument("id", help="実体の ID（endpoint:POST /api/orders など）か機能名")
    p.set_defaults(func=cmd_show)

    p = with_out(sub.add_parser("scaffold", help="注釈の雛形を作る"))
    p.add_argument("--feature", required=True, help="機能の ID・スラッグ・名前")
    p.add_argument("--kind", choices=("detail", "flow"), default="detail")
    p.add_argument("--write", action="store_true", help="annotations.json に書き込む（既存は上書きしない）")
    p.set_defaults(func=cmd_scaffold)

    p = with_out(sub.add_parser("check", help="注釈を検査する"))
    p.set_defaults(func=cmd_check)

    p = with_out(sub.add_parser("render", help="資料を書く"))
    p.add_argument("--only", help="書く全体資料（overview,features,database,api,screens,integrations）。"
                                  "空文字なら全体資料は書かない")
    p.add_argument("--feature", action="append", help="flow / detail を書く機能（複数可）")
    p.add_argument("--kind", choices=("flow", "detail"), help="--feature で書く資料の種類")
    p.add_argument("--format", choices=("md", "html", "both"), default="md")
    p.add_argument("--no-mermaid", action="store_true", help="HTML で Mermaid の CDN を読み込まない")
    p.set_defaults(func=cmd_render)

    p = with_out(sub.add_parser("coverage", help="コード → 資料の網羅率"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--fail-under", type=float, help="この % を下回る種別があれば終了コード 1")
    p.set_defaults(func=cmd_coverage)

    p = with_out(sub.add_parser("verify", help="資料 → コードの実在確認"))
    p.add_argument("--doc", help="1 本だけ照合する")
    p.add_argument("--mode", choices=("structure", "flow", "detail"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("secrets", help="機密の検出")
    p.add_argument("--doc", required=True, help="ファイルかディレクトリ")
    p.add_argument("--terms", help="取引先名などの語彙ファイル（1 行 1 語）")
    p.add_argument("--reveal", action="store_true", help="検出した値をそのまま表示する")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_secrets)

    p = sub.add_parser("html", help="Markdown を HTML にする")
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--no-mermaid", action="store_true")
    p.set_defaults(func=cmd_html)
    return ap


def main(argv=None):
    # Windows はパイプへの出力が既定で cp932 になり、`—` などを書いた時点で落ちる。
    # 読み手（Claude Code）は UTF-8 で受け取るので、OS によらず UTF-8 に固定する
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):  # 呼び出し側が差し替えた出力先には触らない
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
