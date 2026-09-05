# source-docs

既存プロジェクトのソースコードを読み、**初見の開発者がシステム構造を理解するための技術資料**を
生成する Claude Code スキル。

コードの羅列や要約ではなく、資料を読むことで構造が分かる状態を目指す。
**ソースコードから確認できないことは書かず、「未確認」として残す。**

## 生成される資料

| ファイル | 含む資料 |
| --- | --- |
| `overview.md` | システム概要・全体構成図・技術スタック・主要な業務フロー |
| `database.md` | スキーマ一覧・テーブル定義・Enum・**ER 図**・ビュー |
| `api.md` | 共通仕様・エンドポイント一覧・エンドポイント詳細 |
| `screens.md` | 画面一覧・**画面遷移図**・画面詳細・**画面-API-DB 関連** |
| `integrations.md` | 連携先一覧・送信・受信・認証情報の所在・障害時の挙動 |

図はすべて Mermaid。Markdown が正本で、HTML へも変換できる。

書式のサンプルは `docs/output-sample.html` をブラウザで開く。

## インストール

`skill/` の中身を `~/.claude/skills/source-docs/` へ置く。

```bash
git clone https://github.com/<user>/source-docs.git
mkdir -p ~/.claude/skills/source-docs
cp -r source-docs/skill/. ~/.claude/skills/source-docs/
```

配置後はこうなる。

```
~/.claude/skills/source-docs/
├── SKILL.md
├── references/
├── templates/
└── scripts/
```

## 使い方

```
/source-docs [target] [--format md|html|both] [--out <dir>] [--repo <path>]
```

| 引数 | 既定値 | 取りうる値 |
| --- | --- | --- |
| `target` | `all` | `all` / `overview` / `db` / `api` / `screens` / `integrations` |
| `--format` | `md` | `md` / `html` / `both` |
| `--out` | `docs/system` | 任意のディレクトリ |
| `--repo` | カレントディレクトリ | 対象リポジトリ。複数リポは繰り返す |

```
/source-docs
/source-docs db
/source-docs api --format html
/source-docs --format both --out docs/architecture
/source-docs --repo ./frontend --repo ./backend
```

## ワークフロー

人間が止める場所は 2 か所だけ。

| # | 段階 | 内容 |
| --- | --- | --- |
| 00 | スタック判定 | manifest と設定ファイルから、フロント / バック / ORM / DB を判定する |
| 01 | 列挙 | 入口とスキーマの候補、それぞれの件数を出す |
| — | **ゲート 1** | 判定結果と件数を提示し、人間が対象範囲を確定する |
| 02 | 1 資料目 | 実装を読んで md を書き、識別子の実在を照合する |
| — | **ゲート 2** | 1 本目を人間が読む。指摘は規則として記録し、以降に効かせる |
| 03 | 残り資料 | 同じ手順で残りを生成・照合する |
| 04 | HTML 変換 | `--format` に html があれば実行 |
| 05 | 完了報告 | 生成ファイル一覧と**未確認一覧**を出す |

全資料をまとめて生成してから見せない。1 本目で型を決めないと、
同じ指摘を資料の本数ぶん受けることになる。

## 同梱するスクリプト

| スクリプト | 役割 |
| --- | --- |
| `inventory.py` | スタック判定と、読むべきファイルの候補列挙 |
| `verify.py` | 資料に書いた識別子（エンドポイント / テーブル / errorType）がソースに実在するか照合 |
| `secrets.py` | 資料に機密が混ざっていないか検査 |
| `md2html.py` | md を複数ページの HTML に変換 |

すべて単体で実行できる。

```bash
python3 ~/.claude/skills/source-docs/scripts/inventory.py --repo .
python3 ~/.claude/skills/source-docs/scripts/verify.py --doc docs/system/database.md --repo .
python3 ~/.claude/skills/source-docs/scripts/secrets.py --doc docs/system/database.md
python3 ~/.claude/skills/source-docs/scripts/md2html.py --src docs/system --out docs/system/html
```

## 依存

**Python 3 の標準ライブラリのみ。** 追加インストールは不要。

## 検証済みのスタック

`inventory.py` の判定と候補列挙を、実際のプロジェクトで確認したもの。

| スタック | 確認した内容 |
| --- | --- |
| Next.js + Hono + Drizzle + PostgreSQL | 判定・候補列挙・DB 定義書の生成・照合・HTML 変換 |
| Next.js + Strapi + PostgreSQL | 判定・候補列挙（`content-types/*/schema.json` の検出） |
| Expo Router + Supabase | 判定・候補列挙（`app/` 配下の `.tsx` と Edge Function の検出） |

これ以外のスタックでも汎用手順で動くが、**フレームワーク固有の書き方は
`references/stacks/` に無ければ読み取れない。** 精度は落ちる。

## このスキルの限界

- **漏れゼロは機械で保証できない。** API・画面ルートは確実に列挙できるが、
  バッチや Webhook は書き方がプロジェクト次第で、独自構造なら取りこぼしうる
- **機械照合は作文しか検出できない。** 「実在しないエンドポイントを書いた」は防げるが、
  「分岐条件の説明が実装と違う」「書かれていない分岐がある」は検出できない
- **機密検出は誤検知が出る。** だから自動で伏せ字にせず、常に人間の判断に回す
- **未検証のスタックでは精度が落ちる。** 上の表にないスタックでは、
  読み取れなかった箇所が未確認として増える

## 未実装

- **差分更新** — Git 差分で変更箇所だけ再生成する。前提条件（各 md 冒頭のコミットハッシュ、
  行ごとの実装ファイル欄）は満たしてある
- **`feature` モード** — 1 機能を指定して画面 → API → Service → DB を追跡する。
  前提条件（画面-API-DB 関連表）は満たしてある
- **人間が書いた補足との共存**
- **Notion への反映** — md を変換する別スキルの担当

## 設計

- `docs/design.md` — 設計の正本
- `docs/design-overview.html` — アーキテクチャ図・ワークフロー図・設計の 12 項目
- `docs/output-sample.html` — 生成される資料の書式サンプル
- `docs/implementation-plan.md` — 実装計画

## 開発

```bash
python3 -m unittest discover -s tests -v
```

追加依存なし。`tests/fixtures/` に小さなダミープロジェクトを置いてある。

## ライセンス

MIT
