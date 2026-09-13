# tools

**スキル本体には同梱していない。** ここに置いてあるのは検討中のもので、
`/source-docs` の手順からは呼ばれない。

## md2notion.py

Markdown のテーブルを Notion 形式（`<table header-row="true">` + `<tr>` / `<td>`）へ変換する。
コードブロック内（```mermaid など）は変換しない。先頭の h1（ページタイトルになる行）は除去する。

```
python3 md2notion.py <入力.md> <出力.md>
```

**保留中。** Notion への出力はスキルの対象外で、SKILL.md にもその手順は無い。
Notion に取り込むなら、生成した md をそのまま Notion の Markdown インポートに渡す。
Mermaid はコードブロックとして残り、図としては描画されない。

このスクリプトを本体へ取り込むかどうかは、Notion 出力を正式に扱うと決めた時点で判断する。
