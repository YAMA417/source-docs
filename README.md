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

### プラグインとして入れる（推奨）

```
/plugin marketplace add YAMA417/source-docs
/plugin install source-docs@source-docs
```

**起動名は `/source-docs:source-docs`。** プラグインのスキルは
`plugin-name:skill-name` の名前空間を持つため、`:` が入る。
「DB 定義書を作って」のような自然な依頼でも起動する。

更新はこの 2 つ。

```
/plugin marketplace update source-docs
claude plugin update source-docs@source-docs
```

**この方法を推す理由は、更新が届くこと。** 手動でコピーする方法だと、
こちらが直しても入れた人には伝わらない。

### 手動で置く（プラグインを使わない場合）

`skills/source-docs/` の中身をそのままコピーする。起動名は `/source-docs`。

**全プロジェクトで使う場合:**

```bash
git clone https://github.com/YAMA417/source-docs.git
mkdir -p ~/.claude/skills/source-docs
cp -r source-docs/skills/source-docs/. ~/.claude/skills/source-docs/
```

**特定のリポジトリだけで使う場合:**

```bash
mkdir -p <project>/.claude/skills/source-docs
cp -r source-docs/skills/source-docs/. <project>/.claude/skills/source-docs/
```

こちらは `.claude/skills/` ごと commit すれば、clone した人が
**何もしなくても使える。** インストール手順を伝えずに済むのが利点。
引き換えに、更新は各自がコピーし直すことになる。

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

起動名は入れ方で変わる。プラグインなら `/source-docs:source-docs`、
手動で置いたなら `/source-docs`。どちらでも「DB 定義書を作って」のような
自然な依頼で起動する。

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

すべて単体で実行できる。スキルを使わず CI に組み込むこともできる。

置き場所は入れ方で変わる。以下では `<skill-dir>` と書く。

- プラグイン — `~/.claude/plugins/cache/source-docs/source-docs/<version>/skills/source-docs`
- 手動 — `~/.claude/skills/source-docs` または `<project>/.claude/skills/source-docs`

```bash
python3 <skill-dir>/scripts/inventory.py --repo .
python3 <skill-dir>/scripts/verify.py --doc docs/system/database.md --repo .
python3 <skill-dir>/scripts/secrets.py --doc docs/system/database.md
python3 <skill-dir>/scripts/md2html.py --src docs/system --out docs/system/html

# 外部スクリプトを読み込みたくない場合（図は描画されずコードのまま残る）
python3 <skill-dir>/scripts/md2html.py --src docs/system --out docs/system/html --no-mermaid

# 機密検出の値を実際に見る（既定は伏せる）
python3 <skill-dir>/scripts/secrets.py --doc docs/system/api.md --reveal
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

## セキュリティ

このスキルは他人のリポジトリを読む。そのための取り決め。

**外部へ送信しない。** 対象リポジトリの内容をネットワークへ出す処理は同梱スクリプトに無い。
依存も Python 標準ライブラリだけなので、外部パッケージ経由の送信経路も無い。

**認証情報の入りうるファイルを開かない。** `scripts/_secure.py` が判定し、
`inventory.py` と `verify.py` が最初から除外する。SKILL.md にも同じ規則を書いてあるので、
Claude が自分で開くこともしない。

| 除外するもの | 例 |
| --- | --- |
| 環境変数の実ファイル | `.env` / `.env.local` / `.env.production` |
| 鍵そのもの | `*.pem` / `*.key` / `*.p12` / `*.jks` / `id_rsa` |
| 認証情報ファイル | `credentials.json` / `service-account.json` / `.npmrc` / `.netrc` / `terraform.tfvars` |
| ディレクトリごと | `.ssh/` / `.aws/` / `.gnupg/` / `.kube/` / `credentials/` / `secrets/` |
| その他 | `.envrc` / `*.tfvars` / `*.p8` / `.git-credentials` / `.pgpass` |
| **シンボリックリンク** | 名前が `schema.ts` でもリンク先は分からない |

例外は `.env.example` などのテンプレートのみ。外部連携の変数名を知るために読む。
ただし**開けば値も見える**ので、「値を読まない」は仕組みではなく約束である点に注意。

**資料 1 本ごとに機密検出を通す。** `secrets.py` はクラウドサービスのトークン（GitHub /
Slack / Stripe / OpenAI / Anthropic / Google / SendGrid / Twilio / npm / AWS）、秘密鍵、
JWT、DB 接続文字列、メールアドレス、電話番号、私有 IP、内部ホスト名を検出する。
**検出しても自動で伏せ字にしない。** 誤検知が出るので、判断は必ず人間に回す。

**生成した HTML は安全側に倒す。** リンクは禁止一覧ではなく**許可一覧**で判定する
（`http` / `https` / `mailto` / ページ内 / 相対リンクのみ）。制御文字を含むものは通さない。
ファイル名も属性としてエスケープする。資料の元は他人のリポジトリの文字列なので、
無検査で HTML に入れない。

**Mermaid は修正済みのバージョンを SRI 付きで読み込む。** 11.17.2 を
`integrity` / `crossorigin` 付きで指定する。v10 系は 10.9.8 未満、v11 系は 11.16.1 未満に
既知の XSS（CVE-2025-54881 ほか）があり、`securityLevel: strict` でも影響を受ける。
**外部スクリプトを一切読み込みたくない場合は `--no-mermaid`** を付ける。

**書き出し先を検証する。** `.git` / `node_modules` を含むパスやホームディレクトリ直下への
出力は拒否する。既存ファイルを上書きする場合はそのパスを標準エラーに出す。黙って上書きしない。

**入力サイズに上限がある。** 1 ファイル 1MB / 走査合計 50MB / 検査対象 5MB / 1 行 4000 文字。
超えた分は切り捨て、切り捨てたことを標準エラーに出す。巨大な入力でメモリや CPU を
食い潰されないため。

### それでも防げないこと

- **機密検出は誤検知も見落としもある。** パターンに無い形式のトークンは検出できない
- **除外リストは名前で判定する。** 変わった名前のファイルに秘密が入っていれば読んでしまう
- **`.env.example` は開く。** 変数名を得るためだが、実値が書かれていれば見えてしまう
- **Mermaid は外部スクリプト。** CDN が侵害されれば SRI で実行は止まるが図は出ない。
  信用しないなら `--no-mermaid`
- **公開前に人間が読む必要がある。** 特に社外へ出す資料は、機密検出を通したうえで目視する

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
