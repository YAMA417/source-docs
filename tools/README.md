# tools

## md2notion.py

Markdown のテーブルを Notion 形式（`<table header-row="true">` + `<tr>` / `<td>`）へ変換する。
コードブロック内（```mermaid など）は変換しない。先頭の h1（ページタイトルになる行）は除去する。

```
python3 md2notion.py <入力.md> <出力.md>
```

SKILL.md の 09（Notion へ反映）で使う前提。現状スキル本体には同梱されていないので、
再検討時に `skill/scripts/` へ移すか、09 の手順に組み込むかを決める。
