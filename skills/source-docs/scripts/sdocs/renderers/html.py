"""source-docs が出力した Markdown を、ページ間を行き来できる HTML にする。

Markdown の全仕様は実装しない。このツールが出力する書式だけを対象にする。
見出し／段落／箇条書き／番号付き／表／コードブロック／Mermaid／引用／インラインコード／太字／リンク。

直下の資料に加え、`flow/` と `detail/` の下の資料も変換する。
Mermaid は CDN の mermaid.js でブラウザ描画する（SRI 付き）。`mermaid=False` なら読み込まない。
"""

import html
import os
import re
import sys

from ..core import security

# 既知の XSS（CVE-2025-54881 ほか）が修正されたバージョン。v10 系は 10.9.8 未満、
# v11 系は 11.16.1 未満が影響を受ける。SRI を付けて、CDN で差し替えられたら実行されないようにする。
MERMAID_VERSION = "11.17.2"
MERMAID_CDN = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
MERMAID_SRI = "sha384-EOXBFmc3gx5mb+vn0vPvvGqACToJD24hhacX5Yx+8NUUQrHIle/Qi5Bg9o3zKwW2"

SUBDIRS = ("flow", "detail")
SECTION_TITLE = {"": "全体", "flow": "処理概要", "detail": "詳細設計"}
TOP_ORDER = ["overview", "features", "database", "api", "screens", "integrations"]

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
RE_FENCE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
RE_UL = re.compile(r"^[-*]\s+(.*)$")
RE_OL = re.compile(r"^\d+\.\s+(.*)$")
RE_QUOTE = re.compile(r"^>\s?(.*)$")
# 区切り行。ハイフンを 1 つ以上含むことを条件にする（`|  |  |` を区切りと誤認しない）
RE_TABLE_DIVIDER = re.compile(r"^\|[\s:|-]*-[\s:|-]*\|$")

# 生成した HTML はブラウザで開かれる。資料の元は他人のリポジトリの文字列なので、
# リンク先は禁止一覧ではなく**許可一覧**で判定する
SAFE_SCHEMES = ("http://", "https://", "mailto:")
RE_CONTROL = re.compile(r"[\x00-\x20\x7f]")
RE_INLINE_CODE = re.compile(r"``\s(.+?)\s``|`([^`]+)`")
RE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# 出力してはいけない場所。壊すと復旧できない
FORBIDDEN_OUT_PARTS = frozenset({".git", ".svn", "node_modules"})


def safe_href(href):
    """許可した形のリンクだけを通し、それ以外は `#` にする。

    1. `http://` `https://` `mailto:` で始まる絶対リンク
    2. `#` で始まるページ内リンク
    3. スキームを持たない相対リンク（`db.md` `../a/b.html`）
    """
    h = href.strip()
    if RE_CONTROL.search(h):
        return "#"
    if h.endswith(".md"):
        h = h[:-3] + ".html"
    elif ".md#" in h:
        h = h.replace(".md#", ".html#", 1)
    lower = h.lower()
    if lower.startswith(SAFE_SCHEMES) or h.startswith("#"):
        return html.escape(h, quote=True)
    scheme_end = h.find(":")
    if scheme_end != -1 and "/" not in h[:scheme_end]:
        return "#"
    return html.escape(h, quote=True)


def render_inline(text):
    """インライン記法を HTML にする。コードは先に退避し、残りをエスケープしてから戻す。"""
    slots = []

    def stash(rendered):
        slots.append(rendered)
        return f"\x00{len(slots) - 1}\x00"

    def stash_code(m):
        return stash(f"<code>{html.escape(m.group(1) or m.group(2))}</code>")

    def stash_link(m):
        # href はエスケープ前の原文から取る（`&` の二重エスケープを避ける）
        return stash(f'<a href="{safe_href(m.group(2))}">{render_inline_plain(m.group(1))}</a>')

    text = RE_INLINE_CODE.sub(stash_code, text)
    text = RE_LINK.sub(stash_link, text)
    text = html.escape(text)
    text = RE_BOLD.sub(r"<strong>\1</strong>", text)
    for i, rendered in enumerate(slots):
        text = text.replace(f"\x00{i}\x00", rendered)
    return text


def render_inline_plain(text):
    text = html.escape(text)
    return RE_BOLD.sub(r"<strong>\1</strong>", text)


def split_row(raw):
    """表の行をセルに分ける。`\\|` はセルの区切りではない。"""
    body = raw.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells, buf, i = [], [], 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if c == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _starts_block(line):
    return bool(RE_HEADING.match(line) or RE_FENCE.match(line) or RE_UL.match(line)
                or RE_OL.match(line) or RE_QUOTE.match(line) or line.strip().startswith("|"))


def parse_blocks(md):
    blocks, lines, i = [], md.splitlines(), 0
    n = len(lines)
    while i < n:
        line = lines[i]
        fence = RE_FENCE.match(line)
        if fence:
            close = next((j for j in range(i + 1, n) if lines[j].startswith("```")), -1)
            if close == -1:
                # 閉じフェンスが無い。残りを全部コードにすると見出しも表も消えるので本文扱い
                blocks.append(("para", line))
                i += 1
                continue
            blocks.append(("code", fence.group(1), "\n".join(lines[i + 1:close])))
            i = close + 1
            continue
        heading = RE_HEADING.match(line)
        if heading:
            blocks.append(("heading", len(heading.group(1)), heading.group(2).strip()))
            i += 1
            continue
        if line.strip().startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                raw = lines[i].strip()
                if not RE_TABLE_DIVIDER.match(raw):
                    rows.append(split_row(raw))
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue
        for rx, kind in ((RE_UL, "ul"), (RE_OL, "ol")):
            if rx.match(line):
                items = []
                while i < n and rx.match(lines[i]):
                    items.append(rx.match(lines[i]).group(1))
                    i += 1
                blocks.append((kind, items))
                break
        else:
            if RE_QUOTE.match(line):
                parts = []
                while i < n and RE_QUOTE.match(lines[i]):
                    parts.append(RE_QUOTE.match(lines[i]).group(1))
                    i += 1
                blocks.append(("quote", " ".join(parts)))
                continue
            if not line.strip():
                i += 1
                continue
            parts = []
            while i < n and lines[i].strip() and not _starts_block(lines[i]):
                parts.append(lines[i].strip())
                i += 1
            if parts:
                blocks.append(("para", " ".join(parts)))
    return blocks


def render_blocks(blocks):
    out = []
    for block in blocks:
        kind = block[0]
        if kind == "heading":
            level = min(block[1], 6)
            out.append(f"<h{level}>{render_inline(block[2])}</h{level}>")
        elif kind == "para":
            out.append(f"<p>{render_inline(block[1])}</p>")
        elif kind in ("ul", "ol"):
            items = "".join(f"<li>{render_inline(x)}</li>" for x in block[1])
            out.append(f"<{kind}>{items}</{kind}>")
        elif kind == "quote":
            out.append(f"<blockquote>{render_inline(block[1])}</blockquote>")
        elif kind == "code":
            lang, body = block[1], block[2]
            # pre の中では引用符をエスケープしない。&quot; になると Mermaid がラベルを読めない
            escaped = html.escape(body, quote=False)
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{escaped}</pre>')
            else:
                cls = f' class="lang-{html.escape(lang, quote=True)}"' if lang else ""
                out.append(f"<pre><code{cls}>{escaped}</code></pre>")
        elif kind == "table":
            rows = block[1]
            head = "".join(f"<th>{render_inline(c)}</th>" for c in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in row) + "</tr>"
                           for row in rows[1:])
            out.append(f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                       f"<tbody>{body}</tbody></table></div>")
    return "\n".join(out)


def convert(md):
    blocks = parse_blocks(md)
    title = next((b[2] for b in blocks if b[0] == "heading" and b[1] == 1), "無題")
    return title, render_blocks(blocks)


STYLE = """\
:root { color-scheme: light dark; --ink:#14181a; --ground:#f4f6f5; --line:#d7ddda;
  --accent:#1c6a57; --surface:#fff; --ink-2:#495356; }
@media (prefers-color-scheme: dark) { :root { --ink:#e3e8e7; --ground:#101416;
  --line:#29312f; --accent:#4fbf9f; --surface:#171c1f; --ink-2:#a5b0b0; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--ground); color:var(--ink); line-height:1.85;
  font-family: "Hiragino Sans","Noto Sans JP",system-ui,sans-serif; }
.wrap { max-width: 1000px; margin:0 auto; padding: 32px 20px 80px; }
nav.top { border-bottom:1px solid var(--line); padding-bottom:12px; margin-bottom:32px;
  display:flex; gap:6px 16px; flex-wrap:wrap; font-size:14px; }
nav.top a { color:var(--accent); text-decoration:none; }
nav.top span { color:var(--ink-2); }
nav.top .sep { color:var(--line); }
h1 { font-size:28px; line-height:1.3; margin:0 0 24px; }
h2 { font-size:21px; margin:40px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
h3 { font-size:17px; margin:28px 0 8px; }
p, li { font-size:15px; }
ul, ol { padding-left:22px; }
a { color:var(--accent); }
code { font-family: ui-monospace,"SFMono-Regular",Consolas,monospace; font-size:.88em;
  background:var(--surface); padding:1px 5px; border-radius:2px; overflow-wrap:anywhere; }
pre { background:var(--surface); border:1px solid var(--line); border-radius:3px;
  padding:16px; overflow-x:auto; }
pre code { background:none; padding:0; }
pre.mermaid { text-align:center; }
blockquote { margin:0 0 16px; padding:12px 16px; border-left:2px solid var(--accent);
  background:var(--surface); color:var(--ink-2); font-size:14px; }
.scroll { overflow-x:auto; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:13.5px; }
th { text-align:left; font-size:12px; color:var(--ink-2); border-bottom:1px solid var(--line);
  padding:8px 10px 8px 0; white-space:nowrap; }
td { border-bottom:1px solid var(--line); padding:9px 10px 9px 0; vertical-align:top; color:var(--ink-2);
  word-break: keep-all; overflow-wrap: break-word; }
td code { white-space: nowrap; overflow-wrap: normal; }
td:first-child { color:var(--ink); }
"""

MERMAID_INIT = (
    '<script src="{cdn}" integrity="{sri}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>\n'
    '<script>mermaid.initialize({{ startOnLoad: true, securityLevel: "strict" }});</script>'
)

PAGE = """\
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{root}style.css">
</head>
<body>
<div class="wrap">
<nav class="top">{nav}</nav>
{body}
</div>
{mermaid_init}
</body>
</html>
"""


def _page_names(src_dir):
    """変換対象の md（`flow/x` のような相対名、拡張子なし）。リンクと除外対象は開かない。"""
    names = []
    for sub in ("",) + SUBDIRS:
        d = os.path.join(src_dir, sub) if sub else src_dir
        if not os.path.isdir(d) or (sub and os.path.islink(d)):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".md"):
                continue
            if security.is_unsafe_to_open(os.path.join(d, f)):
                print(f"warning: 読み飛ばした（リンクまたは除外対象）: {f}", file=sys.stderr)
                continue
            names.append(f"{sub}/{f[:-3]}" if sub else f[:-3])
    top = [n for n in names if "/" not in n]
    top.sort(key=lambda n: (TOP_ORDER.index(n) if n in TOP_ORDER else len(TOP_ORDER), n))
    return top + [n for n in names if "/" in n]


def _nav_html(pages, current):
    """ページ間のリンク。現在のページはリンクにしない。下位ディレクトリからは `../` を付ける。"""
    root = "../" if current and "/" in current else ""
    parts = [f'<a href="{root}index.html">目次</a>']
    for name, title in pages:
        if "/" in name:
            continue
        if name == current:
            parts.append(f"<span>{html.escape(title)}</span>")
        else:
            href = html.escape(f"{root}{name}.html", quote=True)
            parts.append(f'<a href="{href}">{html.escape(title)}</a>')
    return '<span class="sep">|</span>'.join(parts)


def _check_out_dir(out_dir):
    """出力先が壊してはいけない場所でないか確かめる。リンクは拒否する。"""
    if os.path.islink(out_dir.rstrip("/\\")):
        print(f"error: 出力先がシンボリックリンク: {out_dir}", file=sys.stderr)
        raise SystemExit(2)
    absolute = os.path.realpath(out_dir)
    parts = absolute.replace(os.sep, "/").split("/")
    bad = FORBIDDEN_OUT_PARTS.intersection(p for p in parts if p)
    if bad:
        print(f"error: 出力先に {'/'.join(sorted(bad))} が含まれている: {absolute}", file=sys.stderr)
        raise SystemExit(2)
    drive_root = os.path.splitdrive(absolute)[0] + os.sep
    if absolute in ("/", drive_root, os.path.expanduser("~")):
        print(f"error: 出力先が広すぎる: {absolute}", file=sys.stderr)
        raise SystemExit(2)


def write_site(src_dir, out_dir, mermaid=True):
    """src_dir の md をすべて変換し、目次とスタイルを含めて out_dir に書く。"""
    if not os.path.isdir(src_dir):
        print(f"error: 入力ディレクトリが見つかりません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)
    names = _page_names(src_dir)
    if not names:
        print(f"error: md ファイルがありません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)
    _check_out_dir(out_dir)

    parsed = []
    for name in names:
        with open(os.path.join(src_dir, *name.split("/")) + ".md", encoding="utf-8") as f:
            title, body = convert(f.read())
        parsed.append((name, title, body))
    pages = [(n, t) for n, t, _ in parsed]
    init = MERMAID_INIT.format(cdn=MERMAID_CDN, sri=MERMAID_SRI) if mermaid else ""

    written = []
    for name, title, body in parsed:
        path = os.path.join(out_dir, *name.split("/")) + ".html"
        if os.path.exists(path):
            print(f"warning: 上書きする: {path}", file=sys.stderr)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        root = "../" if "/" in name else ""
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(PAGE.format(title=html.escape(title), nav=_nav_html(pages, name), body=body,
                                mermaid_init=init, root=root))
        written.append(path)

    # 目次は、変換した md（detail/ などに生成済みの資料）から作る。詳細設計の節は 0 件でも出す
    sections = []
    for sub in ("",) + SUBDIRS:
        items = [(n, t) for n, t in pages if (n.split("/")[0] if "/" in n else "") == sub]
        if not items:
            if sub == "detail":
                sections.append(f"<h2>{SECTION_TITLE[sub]}</h2><p>詳細設計はまだ生成されていません。</p>")
            continue
        lis = "".join(f'<li><a href="{html.escape(n + ".html", quote=True)}">{html.escape(t)}</a></li>'
                      for n, t in items)
        sections.append(f"<h2>{SECTION_TITLE[sub]}</h2><ul>{lis}</ul>")
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(PAGE.format(title="設計資料", nav=_nav_html(pages, None),
                            body="<h1>設計資料</h1>" + "".join(sections), mermaid_init="", root=""))
    written.append(index_path)
    style_path = os.path.join(out_dir, "style.css")
    with open(style_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(STYLE)
    written.append(style_path)
    return written
