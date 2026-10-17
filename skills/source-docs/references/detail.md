# 詳細設計（details）の書き方

1 つの機能を、入口から永続化まで追いかけて**どう動いているか**を示す。
読み手は「その機能を改修する人」。コードを開く前に全体像を掴み、開いたあとは索引として使う。
共通規則は `writing.md`。矛盾したら、機密と推測の禁止を除いてこちらが優先する。

## このモードだけが書いてよいこと

- **内部の処理** — 呼び出しの順序、分岐条件、エラー時の扱い、トランザクションの境界
- **実装ファイルのパス** — どこを開けばよいか

それでも、機密・推測で埋めた仕様・「使われていない」の断定は書かない。

## 追跡の手順

まず `scaffold --feature <機能> --kind detail --write` で雛形を足す。入口・関係するテーブル・外部連携・
関連ファイルは機械が埋めてある。**埋まっていない所は、コードを読んで確かめたことだけで埋める。**

1. **入口を開く** — `show <entrypoint ID>` で定義の場所が分かる
2. **呼び出しをたどる** — 入口 → 中間処理 → 永続化。各段のファイルを `files` に控える
3. **分岐を洗う** — 正常系を追い切ってから、条件分岐とエラー処理に戻る
4. **DB への影響を確かめる** — 機械が見つけた操作（`dataImpact` の雛形）に、変わるカラムと意味を足す
5. **外部への送信を確かめる** — 送る項目・受け取る項目・リトライ・タイムアウト・失敗時

**途中で追えなくなったら、そこで止めて `unverified` に書く。推測でつながない。**
フレームワークの自動処理（ミドルウェア・ライフサイクルフック・DI）は呼び出し元が明示されない。

## 書式

```json
"details": {
  "feature:order-confirm": {
    "summary": "3 行以内", "start": "どこから始まるか", "end": "どこで終わるか",
    "preconditions": [{"kind": "authentication|authorization|role|ownership|business|other",
                       "description": "", "evidence": []}],
    "inputs": [{"name": "", "type": "", "required": true, "constraints": "", "source": "リクエストボディ"}],
    "validations": [{"condition": "", "onInvalid": "", "userVisible": ""}],
    "sequence": {
      "participants": [{"id": "api", "label": "POST /api/orders", "kind": "user|screen|frontend|backend|service|db|cache|queue|worker|external"}],
      "steps": [
        {"from": "screen", "to": "api", "message": "注文を送る"},
        {"alt": [{"condition": "在庫あり", "steps": []}, {"condition": "在庫なし", "steps": []}]},
        {"from": "api", "to": "queue", "message": "領収書", "kind": "call|return|async|note"}
      ]
    },
    "branches": [{"condition": "", "result": "", "status": 409, "userVisible": ""}],
    "dataImpact": [{"table": "table:orders", "operation": "CREATE", "columns": [], "meaning": "", "evidence": []}],
    "transaction": {"status": "implemented|not-implemented|unverified", "boundary": "", "rollback": "",
                    "nested": "", "savepoint": "", "evidence": [], "searched": ""},
    "concurrency": {"status": "", "mechanisms": [{"kind": "optimistic-lock|pessimistic-lock|unique-constraint|duplicate-check|advisory-lock|other",
                                                  "description": "", "evidence": []}], "searched": ""},
    "idempotency": {"status": "", "mechanism": "", "evidence": [], "searched": ""},
    "stateTransition": {"entity": "orders.status", "states": [], "transitions": [{"from": "[*]", "to": "", "trigger": ""}],
                        "evidence": []},
    "externalCalls": [{"integration": "integration:stripe", "request": "", "response": "", "retry": "",
                       "timeout": "", "failure": "", "evidence": []}],
    "sideEffects": [{"kind": "email|notification|event|queue|cache|file|external-api|other", "description": "", "evidence": []}],
    "files": [{"role": "入口", "path": "src/app/api/orders/route.ts"}],
    "unverified": [{"item": "", "searched": ""}]
  }
}
```

## status の 3 値

| 値 | 意味 | 必要なもの |
| --- | --- | --- |
| `implemented` | コードに実装がある | **evidence 必須** |
| `not-implemented` | 読んだ範囲に実装が無いと確かめた | evidence（読んだ箇所） |
| `unverified` | 確かめきれていない | **searched 必須** |

「トランザクションを張っているはず」「冪等なはず」を書かない。コードに無ければ `not-implemented`、
読み切れなければ `unverified`。`concurrency` と `stateTransition` は、該当しないなら省く。

## シーケンス

- 登場者は 6 まで。超えるなら機能を分ける
- 分岐は `alt`。戻りは `kind: "return"`、非同期の投入は `kind: "async"`
- 外部サービスは `kind: "external"` にして、ラベルにサービス名を書く
- **図の中の名前は照合されない。** 実在するパス・テーブルは表（`files` `dataImpact`）にも書く

## 分量

1 本 150 行前後。250 行を超えたら追う範囲が広すぎる。機能を分ける（`features` の `entrypoints` で分割）。
説明は 1 ステップ 1〜2 行。**コードを貼らない。引用ではなく説明を書く。**
