#!/usr/bin/env python3
"""source-docs が出力した Markdown を複数ページの HTML に変換する。

Markdown の全仕様は実装しない。このスキルが出力する書式だけを対象にする。
見出し（# 〜 ###）／段落／箇条書き／番号付き／表／コードブロック／
Mermaid ブロック／引用／インラインコード／太字／リンク。

Mermaid は CDN の mermaid.js でブラウザ描画する。ネットに繋がらない環境では
図が出ないが、テキストは読める。

使い方:
    python3 md2html.py --src <md のあるディレクトリ> --out <出力先>

終了コード:
    0  正常
    2  引数・入出力の誤り
"""

import argparse
import html
import os
import re
import sys

import _secure

# Mermaid は既知の XSS（CVE-2025-54881 ほか）が修正されたバージョンを使う。
# v10 系は 10.9.8 未満、v11 系は 11.16.1 未満が影響を受ける。
# SRI を付けて、CDN 側で差し替えられた場合に実行されないようにする。
MERMAID_VERSION = "11.17.2"
MERMAID_CDN = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
MERMAID_SRI = "sha384-EOXBFmc3gx5mb+vn0vPvvGqACToJD24hhacX5Yx+8NUUQrHIle/Qi5Bg9o3zKwW2"

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
RE_FENCE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
RE_UL = re.compile(r"^[-*]\s+(.*)$")
RE_OL = re.compile(r"^\d+\.\s+(.*)$")
RE_QUOTE = re.compile(r"^>\s?(.*)$")
# 区切り行。ハイフンを 1 つ以上含むことを条件にする。
# `|  |  |` のような空セル行を区切りと誤認しないため
RE_TABLE_DIVIDER = re.compile(r"^\|[\s:|-]*-[\s:|-]*\|$")

# 生成した HTML はブラウザで開かれる。資料の元になるのは他人のリポジトリの
# 文字列なので、リンク先を無検査で href に入れない。
#
# 禁止一覧ではなく**許可一覧**で判定する。禁止一覧は制御文字・大文字・
# 実体参照・全角文字などで回避されうる。
SAFE_SCHEMES = ("http://", "https://", "mailto:")
RE_CONTROL = re.compile(r"[\x00-\x20\x7f]")

RE_INLINE_CODE = re.compile(r"`([^`]+)`")
RE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def safe_href(href):
    """リンク先を href に入れられる形にする。

    許可した形のリンクだけを通し、それ以外は `#` にする。
    通すものは 3 つだけ。

    1. `http://` `https://` `mailto:` で始まる絶対リンク
    2. `#` で始まるページ内リンク
    3. スキームを持たない相対リンク（`db.md` `../a/b.html`）

    どれにも当てはまらないものは、スキームが何であれ落とす。
    """
    h = href.strip()
    # 制御文字を挟んでスキームを偽装する手口があるので、含むものは通さない
    if RE_CONTROL.search(h):
        return "#"

    lower = h.lower()
    if h.endswith(".md"):
        h = h[:-3] + ".html"
        lower = h.lower()

    if lower.startswith(SAFE_SCHEMES) or h.startswith("#"):
        return html.escape(h, quote=True)

    # スキームらしきものを持つなら通さない。`a:b` の形は相対リンクではない
    scheme_end = h.find(":")
    if scheme_end != -1 and "/" not in h[:scheme_end]:
        return "#"

    return html.escape(h, quote=True)


def render_inline(text):
    """インライン記法を HTML にする。コードの中身も含めてエスケープする。

    先にコードを取り出して退避し、残りをエスケープしてから戻す。
    そうしないとコード内の < > がタグとして扱われる。
    """
    slots = []

    def stash(rendered):
        slots.append(rendered)
        return f"\x00{len(slots) - 1}\x00"

    def stash_code(m):
        return stash(f"<code>{html.escape(m.group(1))}</code>")

    def stash_link(m):
        # href は**エスケープ前**の原文から取る。escape 済みの文字列から取ると
        # `&` が `&amp;` になった状態で再エスケープされ、`&amp;amp;` になる
        label = html.escape(m.group(1))
        return stash(f'<a href="{safe_href(m.group(2))}">{label}</a>')

    text = RE_INLINE_CODE.sub(stash_code, text)
    text = RE_LINK.sub(stash_link, text)
    text = html.escape(text)
    text = RE_BOLD.sub(r"<strong>\1</strong>", text)

    for i, rendered in enumerate(slots):
        text = text.replace(f"\x00{i}\x00", rendered)
    return text


def _starts_block(line):
    return bool(
        RE_HEADING.match(line)
        or RE_FENCE.match(line)
        or RE_UL.match(line)
        or RE_OL.match(line)
        or RE_QUOTE.match(line)
        or line.strip().startswith("|")
    )


def parse_blocks(md):
    """Markdown をブロックの列に分解する。"""
    blocks = []
    lines = md.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        fence = RE_FENCE.match(line)
        if fence:
            lang = fence.group(1)
            close = -1
            for j in range(i + 1, n):
                if lines[j].startswith("```"):
                    close = j
                    break
            if close == -1:
                # 閉じフェンスが無い。ここから先を全部コードにすると
                # 残りの見出しも表も消える。開きフェンスを本文として扱う
                blocks.append(("para", line))
                i += 1
                continue
            blocks.append(("code", lang, "\n".join(lines[i + 1:close])))
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
                    rows.append([c.strip() for c in raw.strip("|").split("|")])
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue

        if RE_UL.match(line):
            items = []
            while i < n and RE_UL.match(lines[i]):
                items.append(RE_UL.match(lines[i]).group(1))
                i += 1
            blocks.append(("ul", items))
            continue

        if RE_OL.match(line):
            items = []
            while i < n and RE_OL.match(lines[i]):
                items.append(RE_OL.match(lines[i]).group(1))
                i += 1
            blocks.append(("ol", items))
            continue

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
    """ブロックの列を HTML にする。"""
    out = []
    for block in blocks:
        kind = block[0]

        if kind == "heading":
            level = min(block[1], 6)
            out.append(f"<h{level}>{render_inline(block[2])}</h{level}>")

        elif kind == "para":
            out.append(f"<p>{render_inline(block[1])}</p>")

        elif kind == "ul":
            items = "".join(f"<li>{render_inline(x)}</li>" for x in block[1])
            out.append(f"<ul>{items}</ul>")

        elif kind == "ol":
            items = "".join(f"<li>{render_inline(x)}</li>" for x in block[1])
            out.append(f"<ol>{items}</ol>")

        elif kind == "quote":
            out.append(f"<blockquote>{render_inline(block[1])}</blockquote>")

        elif kind == "code":
            lang, body = block[1], block[2]
            # pre の中では引用符をエスケープしない。&quot; になると
            # Mermaid がラベルを読めなくなる。< > & だけ潰せば十分
            escaped = html.escape(body, quote=False)
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{escaped}</pre>')
            else:
                cls = f' class="lang-{lang}"' if lang else ""
                out.append(f"<pre><code{cls}>{escaped}</code></pre>")

        elif kind == "table":
            rows = block[1]
            head = "".join(f"<th>{render_inline(c)}</th>" for c in rows[0])
            body = "".join(
                "<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in row) + "</tr>"
                for row in rows[1:]
            )
            out.append(
                f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>"
            )

    return "\n".join(out)


def convert(md):
    """Markdown を (タイトル, 本文 HTML) にする。タイトルは最初の h1。"""
    blocks = parse_blocks(md)
    title = "無題"
    for block in blocks:
        if block[0] == "heading" and block[1] == 1:
            title = block[2]
            break
    return title, render_blocks(blocks)


STYLE = """\
:root { color-scheme: light dark; --ink:#14181a; --ground:#f4f6f5; --line:#d7ddda;
  --accent:#1c6a57; --surface:#fff; --ink-2:#495356; }
@media (prefers-color-scheme: dark) { :root { --ink:#e3e8e7; --ground:#101416;
  --line:#29312f; --accent:#4fbf9f; --surface:#171c1f; --ink-2:#a5b0b0; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--ground); color:var(--ink); line-height:1.85;
  font-family: "Hiragino Sans","Noto Sans JP",system-ui,sans-serif; }
.wrap { max-width: 900px; margin:0 auto; padding: 40px 24px 80px; }
nav.top { border-bottom:1px solid var(--line); padding-bottom:12px; margin-bottom:32px;
  display:flex; gap:16px; flex-wrap:wrap; font-size:14px; }
nav.top a { color:var(--accent); text-decoration:none; }
nav.top span { color:var(--ink-2); }
h1 { font-size:30px; line-height:1.3; margin:0 0 24px; }
h2 { font-size:22px; margin:40px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
h3 { font-size:17px; margin:28px 0 8px; }
p, li { font-size:15px; }
ul, ol { padding-left:22px; }
code { font-family: ui-monospace,"SFMono-Regular",Consolas,monospace; font-size:.88em;
  background:var(--surface); padding:1px 5px; border-radius:2px; }
pre { background:var(--surface); border:1px solid var(--line); border-radius:3px;
  padding:16px; overflow-x:auto; }
pre code { background:none; padding:0; }
pre.mermaid { text-align:center; }
blockquote { margin:0 0 16px; padding:12px 16px; border-left:2px solid var(--accent);
  background:var(--surface); }
.scroll { overflow-x:auto; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:13.5px; min-width:520px; }
th { text-align:left; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--ink-2); border-bottom:1px solid var(--line); padding:8px 10px 8px 0; }
td { border-bottom:1px solid var(--line); padding:9px 10px 9px 0; vertical-align:top;
  color:var(--ink-2); }
td:first-child { color:var(--ink); }
"""

MERMAID_INIT = (
    '<script src="{cdn}" integrity="{sri}" crossorigin="anonymous" '
    'referrerpolicy="no-referrer"></script>\n'
    '<script>mermaid.initialize({{ startOnLoad: true, securityLevel: "strict" }});</script>'
)

PAGE = """\
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="style.css">
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


def _nav_html(pages, current):
    """ページ間のリンク。現在のページはリンクにしない。"""
    parts = ['<a href="index.html">目次</a>']
    for name, title in pages:
        if name == current:
            parts.append(f"<span>{html.escape(title)}</span>")
        else:
            # ファイル名は対象リポジトリ由来なので、属性としてエスケープする。
            # `x" onmouseover="...` のような名前で属性を抜けられる
            href = html.escape(f"{name}.html", quote=True)
            parts.append(f'<a href="{href}">{html.escape(title)}</a>')
    return " ".join(parts)


# 出力してはいけない場所。壊すと復旧できない
FORBIDDEN_OUT_PARTS = frozenset({".git", ".svn", "node_modules"})


def _page_names(src_dir):
    """変換対象の md を列挙する。

    シンボリックリンクは開かない。`attack.md -> ~/.ssh/id_rsa` のような
    リンクを置かれると、その中身が HTML に埋め込まれる。
    """
    names = []
    for f in os.listdir(src_dir):
        if not f.endswith(".md"):
            continue
        if _secure.is_unsafe_to_open(os.path.join(src_dir, f)):
            print(f"warning: 読み飛ばした（リンクまたは除外対象）: {f}", file=sys.stderr)
            continue
        names.append(f[:-3])
    return sorted(names)


def existing_targets(src_dir, out_dir):
    """書き込むと上書きになる既存ファイルの一覧を返す。"""
    if not os.path.isdir(out_dir):
        return []
    targets = [f"{n}.html" for n in _page_names(src_dir)] + ["index.html", "style.css"]
    return [
        os.path.join(out_dir, t) for t in targets
        if os.path.isfile(os.path.join(out_dir, t))
    ]


def _check_out_dir(out_dir):
    """出力先が壊してはいけない場所でないか確かめる。

    シンボリックリンクは拒否する。`abspath` はリンクを解決しないので、
    リンク越しに検査をすり抜けて別の場所を上書きできてしまう。
    """
    if os.path.islink(out_dir.rstrip(os.sep)):
        print(f"error: 出力先がシンボリックリンク: {out_dir}", file=sys.stderr)
        raise SystemExit(2)

    absolute = os.path.realpath(out_dir)
    parts = absolute.replace(os.sep, "/").split("/")
    bad = FORBIDDEN_OUT_PARTS.intersection(p for p in parts if p)
    if bad:
        print(
            f"error: 出力先に {'/'.join(sorted(bad))} が含まれている: {absolute}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if absolute in ("/", os.path.expanduser("~")):
        print(f"error: 出力先が広すぎる: {absolute}", file=sys.stderr)
        raise SystemExit(2)


def write_site(src_dir, out_dir, mermaid=True):
    """src_dir の md をすべて変換し、index と style を含めて out_dir に書く。

    既存ファイルを上書きする場合は、そのパスを標準エラーに出す。
    黙って上書きしない。

    `mermaid=False` にすると CDN のスクリプトを一切読み込まない。
    図は描画されずコードのまま残るが、外部スクリプトを実行しなくて済む。
    """
    if not os.path.isdir(src_dir):
        print(f"error: 入力ディレクトリが見つかりません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)

    names = _page_names(src_dir)
    if not names:
        print(f"error: md ファイルがありません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)

    _check_out_dir(out_dir)

    for path in existing_targets(src_dir, out_dir):
        print(f"warning: 上書きする: {path}", file=sys.stderr)

    os.makedirs(out_dir, exist_ok=True)

    parsed = []
    for name in names:
        with open(os.path.join(src_dir, f"{name}.md"), encoding="utf-8") as f:
            title, body = convert(f.read())
        parsed.append((name, title, body))

    pages = [(name, title) for name, title, _ in parsed]
    written = []

    init = MERMAID_INIT.format(cdn=MERMAID_CDN, sri=MERMAID_SRI) if mermaid else ""

    for name, title, body in parsed:
        path = os.path.join(out_dir, f"{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(PAGE.format(
                title=html.escape(title), nav=_nav_html(pages, name),
                body=body, mermaid_init=init,
            ))
        written.append(path)

    items = "".join(
        f'<li><a href="{html.escape(name + ".html", quote=True)}">'
        f"{html.escape(title)}</a></li>"
        for name, title in pages
    )
    index_body = f"<h1>設計資料</h1><ul>{items}</ul>"
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(PAGE.format(
            title="設計資料", nav=_nav_html(pages, None),
            body=index_body, mermaid_init=init,
        ))
    written.append(index_path)

    style_path = os.path.join(out_dir, "style.css")
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(STYLE)
    written.append(style_path)

    return written


def main():
    ap = argparse.ArgumentParser(description="md を複数ページ HTML に変換する")
    ap.add_argument("--src", required=True, help="md のあるディレクトリ")
    ap.add_argument("--out", required=True, help="出力先ディレクトリ")
    ap.add_argument(
        "--no-mermaid",
        action="store_true",
        help="Mermaid の CDN スクリプトを読み込まない。図は描画されずコードのまま残る",
    )
    args = ap.parse_args()

    written = write_site(args.src, args.out, mermaid=not args.no_mermaid)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
