# 注釈（annotations.json）の書き方

置き場所は `<out>/.source-docs/annotations.json`。資料の意味づけはすべてここに書く。
`lint` が書式・参照・根拠・機密を検査し、`render` がここと Project Model から資料を描く。

## 共通の約束

- **ID は Project Model のものをそのまま使う。** `features` / `show` で確かめる。自分で作らない
  （新しい機能の `id` だけは自分で付けてよい。`feature:` で始める）
- **根拠（evidence）を付ける。** `"src/api/orders.ts"` か `"src/api/orders.ts:42"` か
  `{"file": "...", "line": 42}`。リポジトリ直下からの相対パス。開いてはいけないファイルは根拠にできない
- **分からないことは書かない。** 代わりに `unverified` に送る。`searched` は必須

```json
"unverified": [
  {"item": "タイムアウト", "searched": "src/lib/stripe.ts の生成箇所と呼び出し 3 か所に指定が無い",
   "kind": "library-owned"}
]
```

`kind` は分からなかった理由の種類。資料の「未確認」に種類として出る。

**未確認は、本文の誤りを訂正する場所ではない。** 本文で確定している事実を未確認に書くと、品質ゲートが
矛盾（`gate.body-vs-unverified`）として止める。本文が誤りなら `disputes` に書く。
何についての未確認かは、文から読み取る（未ログイン時の応答・カラムの一意 / NULL / 型 / 外部キー・環境変数名）。
はっきりさせたいときは `about` を付ける。

```json
{"item": "未ログインのときの応答", "searched": "route.ts に分岐が無い", "kind": "library-owned",
 "about": {"predicate": "unauthenticated_status"}}
```

| kind | 使うとき |
| --- | --- |
| `unresolved`（既定） | 候補はあるが決められない |
| `library-owned` | ライブラリの内部で決まる（リポジトリに無い） |
| `runtime-dependent` | 実行時の値・環境・設定で決まる |
| `framework-default` | フレームワークの既定値で決まる（明示が無い） |
| `not-found` | 探したが見つからなかった |
| `unsupported` | 書き方が特殊で読めなかった |
| `conflict` | 根拠どうしが食い違う |

- 該当なしと分からないを書き分ける。**コードを読んで「無い」と確かめたなら** `not-implemented`
  や「認証なし」と書き、根拠を付ける。読んでいないなら `unverified`
- 値・トークン・接続文字列・私有 IP・内部ホスト名・実在する個人の連絡先を書かない

## 全体

```json
{
  "schema": "source-docs/annotations",
  "version": 1,
  "system": {"name": "…", "summary": "何のためのシステムか（1〜3 文）", "users": ["購入者"],
             "evidence": [], "unverified": []},
  "applications": {"app:shop": {"responsibility": "1 行", "evidence": []}},
  "api": {"baseUrl": "", "auth": "", "authorization": "", "pagination": "", "errorFormat": "",
          "versioning": "", "headers": [{"name": "", "description": ""}], "evidence": [], "unverified": []},
  "features": {},
  "capabilities": {},
  "entities": {},
  "exclusions": [],
  "disputes": [],
  "flows": {},
  "details": {}
}
```

使わない項目は省いてよい。省いた項目は資料で「未確認」になる。

## features — 機能の名前と束ね方

候補（`feature:screen-checkout` など）は入口 1 つずつから機械的に作られている。名前はコードの識別子のまま。

```json
"features": {
  "feature:webhook-post-api-webhooks-stripe": {"name": "決済結果の受信", "summary": "…"},
  "feature:order-confirm": {"name": "注文確定", "summary": "…",
    "entrypoints": ["screen:/checkout", "http:POST /api/orders"],
    "replaces": ["feature:screen-checkout"], "evidence": []}
}
```

配列（`[{"id": "feature:…", …}]`）でも書ける。入口は `entrypoint:screen:/checkout` の完全な ID でも、
`screen:/checkout`（実体の ID）・`http:POST /api/orders`（入口の種類:キー）の省略形でもよい。

- 候補の `id` を書けば、その候補に名前を付ける（関係は機械のまま）
- `entrypoints` を書けば、その入口で機能を組み直す。入口がすべて含まれる候補は自動で消える。
  1 つの画面に機能が 2 つあるなら、入口を分けて 2 つ書く
- **名前は利用者・業務の言葉で付ける。** 画面の文言・README・docs に根拠があるときだけ。無ければ候補のまま残す
- 機能を業務の単位で自動にはまとめない。まとめるのは注釈だけ

## capabilities — 業務上の機能のまとまり（任意）

```json
"capabilities": {"capability:sales": {"name": "販売", "features": ["feature:order-confirm"]}}
```

機能（Feature）より大きい、業務の言葉での単位。根拠（README・docs・画面の構成）があるときだけ書く。

## disputes — 解析結果への異議

コードを読んで、解析器の結果が誤っていると確かめたときに書く。その事実は本文から外れ、
資料末尾の「未確定」に状態つきで出る。`check` は矛盾（conflict）を ERROR として数える。

```json
"disputes": [
  {"entity": "integration:github-oauth", "field": "envVars", "value": "SMTP_FROM",
   "reason": "EmailProvider の引数で、GitHub のものではない", "evidence": ["lib/auth.ts:30"]}
]
```

- `field` は `tables` `integrations` `calls` `links` `publishes` `enqueues` `envVars` `columns`
  `physicalForeignKeys` `ormRelations` のどれか。`value` は関係なら相手の ID、それ以外は名前
- `status` は既定で `conflict`。決められないだけなら `unresolved`
- `reason` と `evidence` は必須。**本文を直す代わりに未確認欄で訂正する書き方はしない**

## entities — 実体ごとの意味

キーは実体の ID。全実体に共通で `summary` `evidence` `unverified` を持てる。種類ごとの追加項目は次のとおり。

| 実体 | 項目 |
| --- | --- |
| `endpoint:` | `auth` / `request`（`in` `name` `type` `required` `constraints` `description`。zod などの実行時の検証が見つかった API は、その項目が優先され、食い違う項目は「未確定」に出る）/ `noRequest: true` / `responses`（`status` `description` `fields`）/ `errors`（`status` `condition`）/ `validation`（`rule` `onInvalid`） |
| `screen:` | `name`（画面上の呼び名）/ `auth` / `inputs`（`name` `type` `required` `constraints`）/ `displays`（`name` `source`）/ `actions`（`label` `result` `api`: endpoint ID / `to`: screen ID） |
| `table:` | `columns`: `{"カラム名": "説明（単位・用途）"}` |
| `integration:` | `purpose` / `protocol` / `auth`（方式だけ）/ `dataSent` / `dataReceived` / `retry` / `timeout` / `failureHandling` |
| `job:` `event:` `entrypoint:` | `summary` だけ |

## exclusions — 意図して資料から外すもの

```json
"exclusions": [{"id": "endpoint:GET /api/health", "reason": "死活監視用で、利用者向けではない"}]
```

coverage の分母から外れ、理由つきで報告される。**「使われていなさそう」で外さない。**

## flows / details

書式は `references/flow.md` と `references/detail.md`。雛形は `scaffold --feature <機能> --kind <種類> --write`。
