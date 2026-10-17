# Strapi

## DB スキーマは content-types が正

テーブル定義は SQL ではなく `src/api/<name>/content-types/<name>/schema.json` にある。

```
src/api/book/content-types/book/schema.json
```

読むもの。

| JSON のキー | 資料に書くもの |
| --- | --- |
| `collectionName` | テーブル名。無ければ API 名の複数形が使われる |
| `attributes.<name>.type` | カラムの型 |
| `attributes.<name>.required` | NULL 可否 |
| `attributes.<name>.unique` | UNIQUE 制約 |
| `attributes.<name>.default` | Default |
| `attributes.<name>.relation` | リレーション。`oneToMany` などの向きも記録する |

**Strapi が自動で作るカラムがある。** `id` `created_at` `updated_at` `published_at`
`created_by_id` `updated_by_id` は schema.json に現れないが、実テーブルには存在する。
資料には「Strapi が自動生成」と注記して載せる。

## リレーション

`relation` の種類（`oneToOne` / `oneToMany` / `manyToMany`）で中間テーブルの有無が決まる。
`manyToMany` は中間テーブルが自動生成される。**ER 図では中間テーブルも描く。**

## API は自動生成される

`src/api/<name>/routes/` に定義が無くても、Strapi が CRUD エンドポイントを自動で作る。

```
GET    /api/<複数形>
GET    /api/<複数形>/:id
POST   /api/<複数形>
PUT    /api/<複数形>/:id
DELETE /api/<複数形>/:id
```

解析器は `src/api/<name>/routes/` の `createCoreRouter('api::<name>.<name>')` を見て、上の 5 本を
規約どおりに作る（`only` / `except` も反映する。根拠の rule に `convention` が付く）。
**ルートファイルが無い content-type は API を作らず、UNSUPPORTED として報告する。**
公開されているかをコードから確定できないため。そのときは人間に確認を取る。

`src/api/<name>/routes/*.js` にカスタムルート（`routes: [{ method, path, handler }]`）があれば、
それは追加のエンドポイント。パスには `/api` が前に付く。

## 認可

`Content-Type Builder` の設定ではなく、`Users & Permissions` プラグインのロール設定で決まる。
**この設定は DB に入っており、コードからは読めない。** 認可欄は「未確認（管理画面で設定）」と書く。
