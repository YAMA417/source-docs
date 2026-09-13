# source-docs

既存プロジェクトのソースコードを読み、**技術資料を生成する Claude Code スキル**。

コードの羅列や要約ではなく、資料を読むことで構造と動きが分かる状態を目指す。
**ソースコードから確認できないことは書かず、「未確認」として残す。**

## 3 つのモード

| モード | 単位 | 答える問い | 出力 |
| --- | --- | --- | --- |
| `structure`（既定） | システム全体 | どんな構造か | `overview.md` ほか 5 本 |
| `flow` | 1 機能 | 何を入れると何が出るか | `flow-<機能名>.md` |
| `detail` | 1 機能 | どう動いているか | `detail-<機能名>.md` |

**何を書くかがモードで変わる。** `structure` は外から見えるもの（リクエスト・レスポンス・
テーブルの構造・画面遷移）だけを書き、内部の処理は書かない。`detail` は逆に、
呼び出しの順序・分岐条件・トランザクションの境界まで書く。`flow` はその中間で、
処理の要点を 5 行以内にまとめる。

機密を書かない・推測で埋めないという規則は、**モードによらず変わらない。**

### structure が生成する資料

| ファイル | 含む資料 |
| --- | --- |
| `overview.md` | システム概要・全体構成図・技術スタック |
| `database.md` | スキーマ一覧・テーブル定義・Enum・**ER 図**・ビュー |
| `api.md` | 共通仕様・エンドポイント一覧・エンドポイント定義 |
| `screens.md` | 画面一覧・**画面遷移図**・画面-API-DB 関連 |
| `integrations.md` | 連携先一覧・送信・受信・認証情報の所在 |

図はすべて Mermaid。Markdown が正本で、HTML へも変換できる。

各資料の章の定義は `skills/source-docs/templates/`、書き方の規則は
`skills/source-docs/references/`（共通は `writing.md`、モード別は `structure.md` /
`flow.md` / `detail.md`）。

## インストール

### プラグインとして入れる（推奨）

```
/plugin marketplace add YAMA417/source-docs
/plugin install source-docs@source-docs
```

**起動名は `/source-docs:source-docs`。** プラグインのスキルは
`plugin-name:skill-name` の名前空間を持つため、`:` が入る。
「DB 定義書を作って」「この機能の詳細設計を書いて」のような自然な依頼でも起動する。

更新はこの 2 つ。

```
/plugin marketplace update source-docs
claude plugin update source-docs@source-docs
```

**この方法を推す理由は、更新が届くこと。** 手動でコピーする方法だと、
こちらが直しても入れた人には伝わらない。

### 手動で置く（プラグインを使わない場合）

`skills/source-docs/` の中身をそのままコピーする。起動名は `/source-docs`。

```bash
git clone https://github.com/YAMA417/source-docs.git

# 全プロジェクトで使う
mkdir -p ~/.claude/skills/source-docs
cp -r source-docs/skills/source-docs/. ~/.claude/skills/source-docs/

# 特定のリポジトリだけで使う
mkdir -p <project>/.claude/skills/source-docs
cp -r source-docs/skills/source-docs/. <project>/.claude/skills/source-docs/
```

後者は `.claude/skills/` ごと commit すれば、clone した人が**何もしなくても使える。**
引き換えに、更新は各自がコピーし直すことになる。

## 使い方

```
/source-docs [mode] [--target <資料>] [--feature <機能名>]
             [--format md|html|both] [--out <dir>] [--repo <path>]
```

| 引数 | 既定値 | 取りうる値 |
| --- | --- | --- |
| `mode` | `structure` | `structure` / `flow` / `detail` |
| `--target` | `all` | `all` / `overview` / `db` / `api` / `screens` / `integrations` |
| `--feature` | 対話で選ぶ | 機能名。`flow` / `detail` のみ |
| `--format` | `md` | `md` / `html` / `both` |
| `--out` | `docs/system` | 任意のディレクトリ |
| `--repo` | カレントディレクトリ | 対象リポジトリ。複数リポは繰り返す |

```
/source-docs                                   # 全体の構造を一式
/source-docs db                                # 互換指定。structure --target db
/source-docs flow --feature "スナップ投稿"      # 1 機能の処理概要
/source-docs detail --feature "記事作成"        # 1 機能の詳細設計
/source-docs --format both --out docs/architecture
/source-docs --repo ./frontend --repo ./backend
```

第 1 引数がモード名でも既存の資料名でもない場合、**勝手に解釈せず候補を示して聞き返す。**
機能名は `--feature` に渡す。

## ワークフロー

人間が止める場所は 2 か所だけ。

| # | 段階 | 内容 |
| --- | --- | --- |
| 00 | スタック判定 | manifest から、フロント / バック / ORM / DB を判定する |
| 01 | 列挙 | 入口とスキーマの候補、`flow` / `detail` なら機能の候補を出す |
| — | **ゲート 1** | 判定結果と候補を提示し、人間が対象を確定する |
| 02 | 生成 | 実装を読んで md を書き、識別子の実在を照合する |
| — | **ゲート 2** | 人間が読む。`structure` なら型の確定、1 本だけなら最終承認 |
| 03 | 残り資料 | 同じ手順で残りを生成・照合する |
| 04 | HTML 変換 | `--format` に html があれば実行 |
| 05 | 完了報告 | 生成ファイル一覧と**未確認一覧**を出す |

`structure` で全資料を作るとき、まとめて生成してから見せない。
1 本目で型を決めないと、同じ指摘を資料の本数ぶん受けることになる。

**機能の特定は人間が選ぶ。** `flow` / `detail` の候補は、既存の `screens.md` の
画面-API-DB 関連表があればそこから、無ければ `inventory.py` の画面・ルート候補から出す。
「注文確定」のような業務上の機能名はコードの命名からは決まらないので、機械に決めさせない。
画面を持たない機能（Webhook・定期処理）は候補に出にくい点も、その場で伝える。

## 同梱するスクリプト

| スクリプト | 役割 |
| --- | --- |
| `inventory.py` | スタック判定と、読むべきファイルの候補列挙 |
| `verify.py` | 資料に書いた識別子がソースに実在するか照合（`--mode` で対象が変わる） |
| `secrets.py` | 資料に機密が混ざっていないか検査 |
| `md2html.py` | md を複数ページの HTML に変換 |

すべて単体で実行できる。スキルを使わず CI に組み込むこともできる。

置き場所は入れ方で変わる。以下では `<skill-dir>` と書く。

- プラグイン — `~/.claude/plugins/cache/source-docs/source-docs/<version>/skills/source-docs`
- 手動 — `~/.claude/skills/source-docs` または `<project>/.claude/skills/source-docs`

```bash
python3 <skill-dir>/scripts/inventory.py --repo .
python3 <skill-dir>/scripts/verify.py --doc docs/system/database.md --repo .
python3 <skill-dir>/scripts/verify.py --doc docs/system/detail-記事作成.md --repo . --mode detail
python3 <skill-dir>/scripts/secrets.py --doc docs/system/database.md
python3 <skill-dir>/scripts/md2html.py --src docs/system --out docs/system/html

# 外部スクリプトを読み込みたくない場合（図は描画されずコードのまま残る）
python3 <skill-dir>/scripts/md2html.py --src docs/system --out docs/system/html --no-mermaid

# 機密検出の値を実際に見る（既定は伏せる）
python3 <skill-dir>/scripts/secrets.py --doc docs/system/api.md --reveal
```

`verify.py --mode` で照合する対象が変わる。

| モード | 照合するもの |
| --- | --- |
| `structure` | エンドポイント / errorType / テーブル |
| `flow` | エンドポイント / テーブル |
| `detail` | エンドポイント / テーブル / **実装ファイルのパス** |

Mermaid のコードブロックは照合対象から外す。シーケンス図には外部サービスへの
呼び出しや概念上のパスが出るので、拾えば必ず誤検知になる。

## 依存

**Python 3 の標準ライブラリのみ。** 追加インストールは不要。

## 検証済みのスタック

`inventory.py` の判定と候補列挙を、実際のプロジェクトで確認したもの。

| スタック | 確認した内容 |
| --- | --- |
| Next.js + Strapi（モノレポ・ts/tsx 792 本） | 判定・候補列挙（画面 39/39・ルート 124 件・連携 4 件）・照合 |
| Next.js + Hono + Drizzle + PostgreSQL | 判定・候補列挙・DB 定義書の生成・照合・HTML 変換 |
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
| 認証情報ファイル | `credentials.json` / `service-account.json` / `.npmrc` / `.netrc` |
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
ファイル名も属性としてエスケープする。

**Mermaid は修正済みのバージョンを SRI 付きで読み込む。** 11.17.2 を
`integrity` / `crossorigin` 付きで指定する。v10 系は 10.9.8 未満、v11 系は 11.16.1 未満に
既知の XSS（CVE-2025-54881 ほか）があり、`securityLevel: strict` でも影響を受ける。
**外部スクリプトを一切読み込みたくない場合は `--no-mermaid`** を付ける。

**書き出し先を検証する。** `.git` / `node_modules` を含むパスやホームディレクトリ直下への
出力は拒否する。既存ファイルを上書きする場合はそのパスを標準エラーに出す。

**入力サイズに上限がある。** 1 ファイル 1MB / 走査合計 50MB / 検査対象 5MB / 1 行 4000 文字。
超えた分は切り捨て、切り捨てたことを標準エラーに出す。

### それでも防げないこと

- **機密検出は誤検知も見落としもある。** パターンに無い形式のトークンは検出できない
- **除外リストは名前で判定する。** 変わった名前のファイルに秘密が入っていれば読んでしまう
- **`.env.example` は開く。** 変数名を得るためだが、実値が書かれていれば見えてしまう
- **Mermaid は外部スクリプト。** CDN が侵害されれば SRI で実行は止まるが図は出ない
- **公開前に人間が読む必要がある。** 特に社外へ出す資料は、機密検出を通したうえで目視する

## このスキルの限界

- **漏れゼロは機械で保証できない。** バッチや Webhook は書き方がプロジェクト次第で、
  独自構造なら取りこぼしうる
- **機械照合は作文しか検出できない。** 「実在しないエンドポイントを書いた」は防げるが、
  「分岐条件の説明が実装と違う」は検出できない。**`detail` ほどこの差が効く**
- **機密検出は誤検知が出る。** だから自動で伏せ字にせず、常に人間の判断に回す
- **未検証のスタックでは精度が落ちる。** 上の表にないスタックでは、
  読み取れなかった箇所が未確認として増える

## 未実装

- **差分更新** — Git 差分で変更箇所だけ再生成する。前提条件（各 md 冒頭のコミットハッシュ）は
  満たしてある
- **人間が書いた補足との共存**
- **Notion / Excel への出力** — md をそのまま Notion の Markdown インポートに渡せば
  見出しと表は取り込める（Mermaid は図にならない）。専用の出力形式は未実装

## 開発

```bash
python3 -m unittest discover -s tests -v
```

追加依存なし。`tests/fixtures/` に小さなダミープロジェクトを置いてある。
push と pull request で GitHub Actions が同じテストを走らせる。

## ライセンス

MIT
