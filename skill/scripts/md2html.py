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

MERMAID_CDN = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js"

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
RE_FENCE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
RE_UL = re.compile(r"^[-*]\s+(.*)$")
RE_OL = re.compile(r"^\d+\.\s+(.*)$")
RE_QUOTE = re.compile(r"^>\s?(.*)$")
RE_TABLE_DIVIDER = re.compile(r"^\|[\s:|-]+\|$")

RE_INLINE_CODE = re.compile(r"`([^`]+)`")
RE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def render_inline(text):
    """インライン記法を HTML にする。コードの中身も含めてエスケープする。

    先にコードを取り出して退避し、残りをエスケープしてから戻す。
    そうしないとコード内の < > がタグとして扱われる。
    """
    slots = []

    def stash_code(m):
        slots.append(html.escape(m.group(1)))
        return f"\x00{len(slots) - 1}\x00"

    text = RE_INLINE_CODE.sub(stash_code, text)
    text = html.escape(text)
    text = RE_BOLD.sub(r"<strong>\1</strong>", text)

    def link(m):
        label, href = m.group(1), m.group(2)
        if href.endswith(".md"):
            href = href[:-3] + ".html"
        return f'<a href="{href}">{label}</a>'

    text = RE_LINK.sub(link, text)

    for i, code in enumerate(slots):
        text = text.replace(f"\x00{i}\x00", f"<code>{code}</code>")
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
            i += 1
            body = []
            while i < n and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス
            blocks.append(("code", lang, "\n".join(body)))
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
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{html.escape(body)}</pre>')
            else:
                cls = f' class="lang-{lang}"' if lang else ""
                out.append(f"<pre><code{cls}>{html.escape(body)}</code></pre>")

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
<script src="{cdn}"></script>
<script>mermaid.initialize({{ startOnLoad: true, securityLevel: "loose" }});</script>
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
            parts.append(f'<a href="{name}.html">{html.escape(title)}</a>')
    return " ".join(parts)


def write_site(src_dir, out_dir):
    """src_dir の md をすべて変換し、index と style を含めて out_dir に書く。"""
    if not os.path.isdir(src_dir):
        print(f"error: 入力ディレクトリが見つかりません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)

    names = sorted(f[:-3] for f in os.listdir(src_dir) if f.endswith(".md"))
    if not names:
        print(f"error: md ファイルがありません: {src_dir}", file=sys.stderr)
        raise SystemExit(2)

    os.makedirs(out_dir, exist_ok=True)

    parsed = []
    for name in names:
        with open(os.path.join(src_dir, f"{name}.md"), encoding="utf-8") as f:
            title, body = convert(f.read())
        parsed.append((name, title, body))

    pages = [(name, title) for name, title, _ in parsed]
    written = []

    for name, title, body in parsed:
        path = os.path.join(out_dir, f"{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(PAGE.format(
                title=html.escape(title), nav=_nav_html(pages, name),
                body=body, cdn=MERMAID_CDN,
            ))
        written.append(path)

    items = "".join(
        f'<li><a href="{name}.html">{html.escape(title)}</a></li>' for name, title in pages
    )
    index_body = f"<h1>設計資料</h1><ul>{items}</ul>"
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(PAGE.format(
            title="設計資料", nav=_nav_html(pages, None),
            body=index_body, cdn=MERMAID_CDN,
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
    args = ap.parse_args()

    written = write_site(args.src, args.out)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
