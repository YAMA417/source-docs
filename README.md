# source-docs

**既存のソースコードから、システムの設計資料を作る Claude Code プラグイン。**
全体の構成・機能一覧・DB・API・画面・外部連携から、1 機能の詳細設計まで辿れる資料を、根拠のソースつきで作る。

**いちばん大事にしていること — 誤った情報を、確定した情報として出さない。**
資料の本文に書くのはコードで確かめたことだけ。決めきれないものは資料末尾の「未確定」に、
分からないものは「未確認」に、理由と調べた範囲を付けて分けて置く。

## こんなときに使う

| 場面 | 使い方の例 |
| --- | --- |
| 引き継いだ・初めて触るプロジェクトの全体像を掴みたい | 「このプロジェクトの設計書を作って」 |
| 改修の前に、1 つの機能がどう動くかを知りたい | 「投稿の編集の詳細設計を書いて」 |
| DB 定義書・API 仕様書を求められたが、無い | 「DB 定義書を出して」「API 仕様書を作って」 |
| 資料はあるが、コードと合っているか分からない | 「資料の漏れを確認して」 |
| 資料が古くなったら CI で気付きたい | `check --strict` を CI に入れる |

## いちばん短い使い方

```
/source-docs
```

全体資料（概要・機能一覧・DB・API・画面・外部連携）を `docs/system/` に作る。
途中で 2 回、内容の確認を求められる（① 解析結果を見て対象を決める ② できた資料を読んで直す点を伝える）。

1 つの機能を掘り下げるとき。

```
/source-docs detail --feature "投稿の編集"
```

機能の名前が分からなければ、候補の一覧が示されるのでそこから選ぶ。

プラグインとして入れた場合、起動名は `/source-docs:source-docs`。自然な依頼（「このプロジェクトの設計書を作って」）でも起動する。

## できあがるもの

```
docs/system/
  overview.md        システム概要。全体構成図・各アプリの責務・資料の健全性（Documentation Health）
  features.md        機能一覧と、機能ごとの画面・API・DB・外部連携のつながり
  database.md        DB 定義・ER 図
  api.md             API 仕様
  screens.md         画面一覧・画面遷移図・画面項目
  integrations.md    外部連携（送受信の方向・認証情報の所在）
  flow/<機能>.md     1 機能の処理概要
  detail/<機能>.md   1 機能の詳細設計（シーケンス図・分岐・データへの影響・認証・権限・注意点 など）
  html/              上の資料の HTML 版
  .source-docs/      元データ（Project Model・注釈・根拠の索引・網羅率）
```

図はすべて Mermaid。Markdown が正本で、HTML へも変換できる。

## 資料の読み方

本文の記述には、確からしさの段階がある。

| 段階 | 意味 | どこに出るか |
| --- | --- | --- |
| 確定 | コードの宣言や、入口のコードで直接確かめた | 本文 |
| 推定（根拠あり） | import をたどった先や、フレームワークの規約から根拠付きで判断した | 本文（ER 図の点線・「規約から推定」など） |
| 未解決 | 候補はあるが決められない（呼び先が複数のアプリにある、名前が似ているだけ…） | 末尾の「未確定」 |
| 矛盾 | 根拠どうしが食い違う | 末尾の「未確定」 |
| 解析対象外 | 解析器が読めない書き方 | 末尾の「未確認」 |

末尾の「未確認」には、分からなかった理由の種類も付く（ライブラリ側で決まる・実行時に決まる・フレームワークの既定・見つからない…）。
「調べ足りない」のか「リポジトリの外の話」なのかが区別できる。

`overview.md` の「資料の健全性」に、資料全体の状態（Overall Status）とその理由、事実の状態ごとの件数（Fact Status）、
品質ゲートの診断の件数（Quality Gate Diagnostics）、検出した数・説明の付いた数・確定できた関係の割合が出る。
資料をどこまで信用してよいかの目安になる。資料の状態と品質ゲートの診断は別のもので、
「資料の状態は WARNING、品質ゲートの WARNING は 0 件」（未解決の事実があるだけ）は矛盾ではない。

資料の状態は、件数から次の順に当てはめて決める（同じモデルと診断からは必ず同じ結果になる）。

1. 品質ゲートの ERROR か、根拠どうしの矛盾（conflict）が 1 件以上 → FAILED
2. 未解決の事実（unresolved）か、解析の対象外（unsupported）が 1 件以上 → WARNING
3. どれも 0 件 → SAFE

`check` はこれに、資料の古さ（WARNING）と、書き出した資料の機密検査・Project Model の検査（ERROR なら FAILED）を重ねる。

| Overall Status（Documentation Status） | 意味 |
| --- | --- |
| SAFE | 矛盾も未確定も無い |
| WARNING | 矛盾は無い。確定できなかった事実があり、末尾の「未確定」に分けてある |
| FAILED | 根拠どうしの矛盾か、品質ゲートの誤りがある（矛盾した事実は本文から外してある） |

**本文と末尾の一覧は矛盾しない。** 本文で確定したことを「未確認」でも扱っている注釈や、共通仕様が個別の API より
強く断定している注釈があると、資料を書き出す前に品質ゲートが止める（「本文に誤りを書いて、未確認欄で訂正する」資料にならない）。

## インストール

### プラグインとして入れる（推奨）

```
/plugin marketplace add YAMA417/source-docs
/plugin install source-docs@source-docs
```

更新は `/plugin marketplace update source-docs` のあと `claude plugin update source-docs@source-docs`。
更新したら Claude Code を起動し直す。

0.4 以前に作った資料を作り直すと、品質ゲートで止まることがある（本文で確定したことを注釈の「未確認」でも挙げている、
共通仕様が個別の API より強く断定している など）。止まったときのメッセージに直し方が出るので、それに従って注釈を直す。

### 手動で置く

`skills/source-docs/` の中身を `~/.claude/skills/source-docs/`（全プロジェクト）か
`<project>/.claude/skills/source-docs/`（そのリポジトリだけ）にコピーする。起動名は `/source-docs`。

動作に必要なのは Python 3.9 以上だけ（標準ライブラリのみ）。Windows・macOS・Linux で動く。

## 依頼の一覧

```
/source-docs [対象] [--feature <機能>] [--format md|html|both] [--out <dir>] [--repo <path>]
```

| 依頼の例 | 対象 | 作るもの |
| --- | --- | --- |
| 「このプロジェクトの設計書を作って」 | `structure`（既定） | 全体資料 6 本 |
| 「機能一覧を出して」 | `features` | `features.md` |
| 「DB 定義書を出して」 / 「API 仕様書を作って」 | `db` / `api` | その 1 本 |
| 「画面遷移図を作って」 / 「外部連携を整理して」 | `screens` / `integrations` | その 1 本 |
| 「投稿の編集の処理概要を出して」 | `flow --feature 投稿の編集` | `flow/<機能>.md` |
| 「投稿の編集の詳細設計を書いて」 | `detail --feature 投稿の編集` | `detail/<機能>.md` |
| 「資料の漏れを確認して」 | `coverage` | 網羅率の報告 |

## 資料が古くなっていないか確かめる（CI）

資料を作ったあとでコードが変わると、資料は古くなる。`check` はそれを見つける。

```bash
python3 skills/source-docs/scripts/source_docs.py check --out docs/system --strict \
  --min-api-coverage 90 --min-screen-coverage 90
```

| 調べること | 重大度 |
| --- | --- |
| Project Model・注釈が壊れている | ERROR |
| 資料・注釈に認証情報らしき値がある（Markdown / JSON / HTML） | ERROR |
| 根拠どうしが矛盾している事実がある | ERROR |
| 品質ゲートの誤り（本文と未確認の矛盾・共通仕様の言い過ぎ・本文に未確定の事実が混入） | ERROR |
| 資料を作ったあとにコミットが進んだ・API や画面が増えた / 消えた・関係が変わった | WARNING（`--strict` で ERROR） |
| 網羅率が閾値を下回った | WARNING（`--strict` で ERROR） |
| ORM と migration の食い違い・行き先の決まらない関係 | WARNING |

ERROR が 1 件でもあれば終了コード 1。再解析して差分を見るので、出力は例えば次のようになる。

```
Quality Gate

  Confirmed:     124
  Inferred:       19
  Unresolved:      5
  Conflict:        0
  Unsupported:     0

  WARNING stale: 新しいAPI 1 件: endpoint:POST /api/refunds
  WARNING stale: 無くなった画面 1 件: screen:/products
Result: WARNING
CHECK errors=0 warnings=2 info=0 RESULT=OK
```

直すには資料を作り直す（`/source-docs` をもう一度実行する）。Claude が書いた意味づけ（注釈）は保たれる。

## セキュリティ

このプラグインは他人のリポジトリを読む。そのための取り決め。

**同梱スクリプトはネットワークに送信しない。** ただし **Claude が読んだソースコードは、通常の Claude Code と
同じく Anthropic に送られる。** 社内のコードに使うときは、各社の Claude Code の利用規程に従うこと。

**認証情報の入りうるファイルを開かない。** 解析器は名前と場所で判定し、最初から除外する。

| 除外するもの | 例 |
| --- | --- |
| 環境変数の実ファイル | `.env` / `.env.*`（`.env.local` / `.env.production` …） |
| 鍵そのもの | `*.pem` / `*.key` / `*.p12` / `*.jks` / `id_rsa` / `*.p8` |
| 認証情報ファイル | `credentials.json` / `service-account.json` / `.npmrc` / `.netrc` / `.git-credentials` / `.pgpass` / `*.tfvars` |
| ディレクトリごと | `.ssh/` / `.aws/` / `.gnupg/` / `.kube/` / `credentials/` / `secrets/` |
| シンボリックリンク | ファイルもディレクトリも辿らない |

`.env.example` などのテンプレートからは**変数名だけ**を取り出す。値は資料にも元データにも出ない
（テストで、上の全ファイルに置いた値が Markdown・JSON・HTML のどこにも出ないことを確かめている）。

**機密を資料に出さない。** 注釈に認証情報らしき値があれば、資料を書き出さずに止まる。
書き出した Markdown・JSON・HTML はすべて機密検出にかける。検出しても自動で伏せ字にしない（誤検知があるため人間が判断する）。

**生成した HTML は安全側に倒す。** リンクは許可一覧のものだけ。Mermaid は 11.17.2 を SRI 付きで読み込み、
`securityLevel: strict` で描く。外部スクリプトを読み込みたくなければ `--no-mermaid`。

### それでも防げないこと

- 除外は名前で判定する。変わった名前のファイルに秘密が入っていれば読んでしまう
- 機密検出は誤検知も見落としもある。社外に出す資料は、検出を通したうえで人間が目視する
- `.env` を開かないという規則を Claude が守るかは SKILL.md の約束による。確実に止めたいなら
  各自の settings の `permissions.deny` で読み取りを禁止する

## 対応スタック

`tests/fixtures/` の小さな再現プロジェクトで、回帰テストとして確かめているもの。

| スタック | 確かめていること |
| --- | --- |
| Next.js（App / Pages Router・Server Actions） | 画面・route handler・動的セグメント・ルートグループ・`use server`・zod の入力検証 |
| Next.js + Prisma + migration | migration を順に適用した最終状態（RENAME・DROP・型変更・NULL 可否・既定値・一意インデックス・FK の張り直し）と Prisma の定義の突き合わせ |
| Next.js + Webhook + NextAuth | 署名検証のある受け口・環境変数を初期化と呼び出しの引数から外部連携に結び付ける |
| Next.js + Strapi（モノレポ） | 2 アプリの判定・コアルータ・独自ルート・ライフサイクル・cron |
| Next.js + Hono + Drizzle + PostgreSQL | `app.route()` のマウントと `basePath`・別名 import・テーブル定義・FK・CHECK |
| Expo Router + Supabase | ファイルベースの画面・Edge Function・`supabase.from()`・SQL マイグレーション・トリガ・pg_cron |
| Express / Fastify | ルート登録・`Router()` / `route()` のマウント・`register(prefix)` |
| Prisma / Drizzle / SQL / SQLAlchemy | テーブル・カラム・PK / FK / UK / CHECK / Index / Enum / View |
| BullMQ / amqplib / node-cron / Nest Schedule / Vercel Cron / Celery | キュー処理・定期実行 |
| FastAPI / Gin | ルート（`APIRouter(prefix)` / `Group`） |
| GraphQL SDL / tRPC / socket.io | 入口として検出 |

実在のプロジェクトでも確かめている（1.0.0 時点）。資料の記述を、画面・API・テーブルの数と根拠の行でソースと突き合わせた。

| プロジェクト | 構成 | 確かめたこと |
| --- | --- | --- |
| [shadcn-ui/taxonomy](https://github.com/shadcn-ui/taxonomy) | Next.js App Router + Prisma（MySQL）+ Stripe + NextAuth | 全体資料と詳細設計（投稿の編集）。画面・API・テーブルの数が実物と一致。画面 → API → テーブルの結び付き・ORM の関係と DB の外部キーの区別・外部連携の環境変数 |
| [strapi/foodadvisor](https://github.com/strapi/foodadvisor) | Next.js + Strapi v4 のモノレポ | 全体資料。2 アプリの判定・コアルータ・URL 組み立て関数経由の API 呼び出し |
| [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | FastAPI + SQLModel + React（TanStack Router） | 全体資料。SQLModel のテーブル。解析器が読めない TanStack Router の画面 8 件を Claude が `additions` で足し、ソースの定義と一致 |
| [vercel/nextjs-subscription-payments](https://github.com/vercel/nextjs-subscription-payments) | Next.js + Supabase + Stripe | 全体資料。`auth.users` への外部キー（スキーマ付きの参照） |
| [gothinkster/node-express-realworld-example-app](https://github.com/gothinkster/node-express-realworld-example-app) | Express + Prisma | 全体資料。API の数がルート登録と一致 |

解析器が知らない書き方は「未確認」（解析対象外）に出る。そのときは Claude がコードを読んで `additions` で足す（下記）。

## このプラグインの限界

- **機械で確かめられる範囲は書き方に依存する。** 解析器が知らない書き方（ORM・ルーター・独自のルート定義）は、Claude がコードを読んで `additions` で足す。足したものは根拠の行に名前があるかを機械で照合し、「推定（根拠あり）」として出す。足せるのはテーブル・API・画面・外部連携で、それらの間の関係は注釈に書いたぶんだけ出る
- **関係は import を 3 段まで辿った範囲。** DI コンテナ・動的な呼び出し・変数で組み立てた URL は辿れない
- **業務上の名前と意味は機械では決まらない。** 機能を業務の単位にまとめるのは、根拠がある場合に注釈で行う
- **機械照合は作文しか検出できない。** 「分岐条件の説明が実装と違う」は人間が読んで見つける

## 未実装

- 変更箇所だけの再生成（全体の再解析・再生成で対応する。注釈は保たれる）
- Notion / Excel への出力
- Laravel / Rails / Django / Spring Boot / NestJS のルート検出

## 互換性

1.x の間は、次のものを変えない。変えるときは 2.0 にする。

- 依頼の書き方（`/source-docs` の対象と引数）
- `source_docs.py` のコマンドと引数、`check` の終了コード（ERROR が 1 件でもあれば 1）
- 注釈（`.source-docs/annotations.json`、`schema: source-docs/annotations`・`version: 1`）の書き方。1.x で書いた注釈は 1.x の間そのまま使える
- 資料の置き場所とファイル名（`docs/system/` 以下）

1.x の間にも変わるもの。

- 検出できる書き方が増え、資料に載る件数や中身が変わる
- 文面・表の列・見た目
- 品質ゲートの検査が増える。新しい検査は WARNING として足す。資料を書き出さずに止める ERROR にするのは 2.0 から

---

## 仕組み（開発者向け）

### 処理の流れ

```
Source Code
  ↓ Detector（detectors/）            フレームワークごとの検出。読んだ情報源を付けて記録する
Raw Facts → Project Model（core/model.py・dbstate.py・relations.py）
  ↓ Evidence Resolution（core/resolve.py）   注釈を実体に結び付ける（notes / narrative）
  ↓ Conflict Resolution（core/resolve.py）   異議（disputes）・注釈の主張と解析結果の突き合わせ・
                                             リクエストの採否・確定と未確定の振り分け・機能の組み立て
  ↓ Quality Gate（core/quality.py）          Fact 単位の検査。ERROR があれば描画しない
Resolved Project Model
  ↓ Renderer（renderers/）            どう表示するかだけを決める。何が正しいかは判断しない
Markdown → HTML / evidence.json / facts.json / coverage.json
```

- **Raw Fact** は解析器が見つけたままの事実（Project Model `project-model.json`）。状態と根拠は付いているが、
  注釈との突き合わせも、本文に出してよいかの判断もしていない
- **Resolved Fact** は品質ゲートを通った事実（`facts.json`）。`place` が `body`（本文）/ `pending`（未確定）/
  `unverified`（注釈の未確認）のどれかに決まっていて、描画されるのは `body` だけ
- 描画は Resolved Project Model だけを読む（`render_docs(resolved, out_dir)`）。注釈ファイルも解析の生の結果も読まない。
  HTML は Markdown から作るので、Markdown と HTML で確定情報が食い違わない
- `check` も同じ解決と品質ゲートを通す

### Fact

何について（subject）、何を主張していて（predicate / object）、その主張がどの状態か（status）を持つ。

```json
{"subject": "integration:github-oauth", "predicate": "uses_env", "object": "SMTP_FROM",
 "status": "conflict", "place": "pending", "reason": "EmailProvider の引数で、GitHub のものではない"}
{"subject": "column:users.email", "predicate": "unique", "object": true, "status": "confirmed", "place": "body"}
{"subject": "endpoint:PATCH /api/posts/:postId", "predicate": "unauthenticated_status", "object": null,
 "status": "unresolved", "place": "pending"}
```

連携先の全体が confirmed でも、環境変数の 1 つだけが conflict、という状態を表せる。

### 品質ゲート（core/quality.py）

| 検査 | 重大度 | 描画 |
| --- | --- | --- |
| 本文の事実に unresolved / conflict / unsupported が混ざっている | ERROR | 止める |
| 同じ事実に、本文で違う値が 2 つある | ERROR | 止める |
| 本文で確定した事実を、未確定・注釈の未確認でも扱っている | ERROR | 止める |
| 共通仕様が個別の API より強く断定している | ERROR | 止める |
| API の注釈が、コードで確かめられない未ログイン時の応答を断定している | ERROR | 止める |
| DB の最終状態が一意に決まっていない | ERROR | 止める |
| 注釈に認証情報らしき値がある | ERROR | 止める |
| 根拠どうしの矛盾（conflict）がある | ERROR | 本文から外して書く |
| 根拠の無い推定（inferred） | WARNING | 本文から外して未確定へ |
| 行き先の決まらない関係・ORM と migration の食い違い | WARNING | 書く |

### API の共通仕様

共通仕様の「未ログイン時の応答」は、個別の API の事実を集めて機械が書く。注釈では断定しない。

- 未ログイン時の応答は、**コードに未ログインの分岐（`if (!session) return …403`）があるときだけ**確定する
- ログイン状態を取り出していても分岐が無い（値を条件に渡しているだけ）API は unresolved
- 例: 「ログイン状態を確かめる API 6 件のうち、403（コードの分岐で確認: 4 件）。確定できない: `PATCH /api/posts/:postId`, `DELETE /api/posts/:postId`」

**Markdown は描画結果であって、データベースではない。** 資料はすべて同じ Resolved Project Model から描くので、
API 一覧・機能一覧・画面一覧で数や名前が食い違わない。

### 事実の状態

| 状態 | 付く条件 |
| --- | --- |
| `confirmed` | 宣言そのもの / 入口のコード片（深さ 0）で直接使っている / 初期化・呼び出しの引数 |
| `inferred` | import を辿った先（深さ 1 以上）/ 規約（Strapi のコア API・自動カラム）/ パス名から結び付けた Webhook の送り手 / 変数を経由した環境変数 |
| `unresolved` | 候補が複数 / 行き先が検出結果に無い / 同じファイルにあるだけ・名前が似ているだけの環境変数 / migration に無い ORM のカラム |
| `conflict` | 同じ優先度の情報源どうしの食い違い / 注釈の異議 |
| `unsupported` | 解析器が読めない書き方 |

### 情報源の優先順位（core/precedence.py）

一律の順位にはしない。情報の種類ごとに、信頼できる情報源が違うため。

| 情報 | 優先順位（左ほど強い） |
| --- | --- |
| DB の物理状態 | migration > ORM のスキーマ（Prisma / Drizzle / SQLAlchemy）> フレームワークのスキーマ（Strapi） |
| DB の関係 | ORM の関係（`ormRelations`）。物理 FK（`physicalForeignKeys`）とは別に持つ |
| API の入力 | 実行時の検証（zod）> ハンドラの検証 > 型定義 > 注釈 |
| API の未ログイン時の応答 | コードの分岐 > 注釈（コードの分岐と違えば注釈を採らない。分岐が無ければ未確定） |
| 画面遷移 | 遷移のコード（Link / a / router.push / redirect）> 部品に渡した href（inferred）> 動的ルートでの受け止め（inferred）。命名だけからは推測しない |
| 外部連携の環境変数 | 初期化の引数 > 呼び出しの引数 > 変数経由（inferred）> 同じ関数の中（unresolved）> 同じファイル（unresolved）> 名前の一致（unresolved） |

### DB の解析

- migration を順に適用して最終状態を作る。`CREATE TABLE` / `ALTER TABLE`（複数カラムの ADD・DROP COLUMN・
  RENAME COLUMN・RENAME TO・型変更・SET / DROP NOT NULL・SET / DROP DEFAULT・ADD / DROP CONSTRAINT・FK の追加と削除）/
  `CREATE [UNIQUE] INDEX` / `DROP INDEX` / 複合 PK・複合 UNIQUE に対応する
- migration があるテーブルは migration の最終状態が物理状態（confirmed）。ORM の定義と食い違えば WARNING、
  ORM にだけあるカラムは本文に出さず未確定に送る
- migration が無いテーブルは ORM の定義が物理状態。同じ優先度の定義どうしが食い違えば conflict（ERROR）
- Prisma の `@relation` は ORM の関係として持ち、DB の外部キーとは分ける。関係ごとに「DB の外部キー」が
  あり / 無い（migration で削除した・最終状態に無い）/ 確かめられない（migration が無い）を別の事実として持つ。
  ORM の関係があって外部キーが無いのは矛盾ではない
- 1 列だけの一意インデックス・一意制約は、そのカラムの UNIQUE として最終状態に入れる（描画では判断しない）
- ER 図は、DB の外部キーを実線（`Physical FK`）、ORM の関係を点線（`ORM relation (Prisma)` など）で描く。
  カラムの `FK` は DB に外部キーがあるものにだけ付ける（migration で削除した外部キーには付けない）

### 外部連携と環境変数

環境変数は「環境変数 → クライアントの初期化 → SDK → 呼び出し箇所」の順に追う。
`GitHubProvider({ clientId: process.env.GITHUB_CLIENT_ID })` なら GitHub に結び付けるが、
同じファイルの `EmailProvider({ from: process.env.SMTP_FROM })` は GitHub に付けない。
入れ子の呼び出し（EmailProvider の設定の中で Postmark を呼ぶ）では、いちばん内側の呼び出しのものにする。
追えないものは確定させず、未確定に候補として残す。

### Coverage

`coverage.json` は 3 つに分けて数える。

| 区分 | 内容 |
| --- | --- |
| detection | API・画面・テーブル・外部連携・入口・ジョブ・イベント・機能のうち、資料の本文に載っている割合 |
| relationships | 画面→API・API→テーブル・機能→入口・機能→API・機能→外部連携・外部連携→環境変数のうち、確定できた割合 |
| quality | 事実の状態ごとの件数 |

### Project Model

`schema: "source-docs/project-model"`、`version: 2`。各実体は一意な `id`（`endpoint:POST /api/orders`・`table:orders` など）、
`status`、`evidence`（根拠のファイル・行・規則）を持つ。関係（`tables` `integrations` `calls` `links` `publishes` `enqueues`）
と環境変数にもそれぞれ状態と根拠が付く。確定できなかったものは実体の `pending` に、理由・候補・根拠つきで入る。
テーブルは `physicalSource`（物理状態の根拠）・`physicalForeignKeys`・`ormRelations` を持つ。

### モジュール

フレームワーク固有の知識は `detectors/` だけが持つ。`core/` はフレームワーク名を知らない。

| 場所 | 役割 |
| --- | --- |
| `scripts/sdocs/detectors/` | フレームワーク・言語ごとの検出器 |
| `scripts/sdocs/core/` | モデル・状態・優先順位・DB の最終状態・関係の解決・機能・解決（resolve）・Fact（facts）・品質ゲート（quality） |
| `scripts/sdocs/renderers/` | Markdown・Mermaid・HTML |
| `scripts/sdocs/validators/` | 注釈の検査・網羅率・健全性（check）・資料の照合・機密の検出 |

### スタックを追加する

1. `scripts/sdocs/detectors/` に `base.Adapter` を継承したクラスを作る（`applies` / `collect` / `usages` / `imports`）
2. `detectors/__init__.py` の `all_adapters()` に足す
3. `tests/fixtures/semantic/<名前>/` に小さな再現プロジェクトと `expect.json`（期待する実体・関係・状態）を置く

### 開発

```bash
python3 -m unittest discover -s tests -v
```

追加依存なし。`tests/test_quality.py` が、実際の生成結果で起きた誤り（GitHub OAuth に SMTP_FROM が載る・
UNIQUE / FK で本文と未確認が食い違う・共通仕様で未ログイン時 403 と断定する）の再発を確かめる。
`tests/test_generated_docs.py` は同じ形の小さなプロジェクトを CLI で解析・描画し、生成された Markdown と HTML そのものを検査する
（GitHub OAuth の環境変数・API の共通仕様・ER 図の `FK` と `ORM relation` / `Physical FK`）。`tests/test_semantic.py` が `tests/fixtures/semantic/*/expect.json` の意味の期待（実体・関係とその状態・
未確定に送るもの・環境変数・DB の最終状態・資料に出てはいけない文字列）を確かめ、同じ入力から同じ出力になることも確かめる。
push と pull request で GitHub Actions が Python 3.9 と 3.12 で同じテストを走らせる。

## ライセンス

MIT
