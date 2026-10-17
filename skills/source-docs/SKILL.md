---
name: source-docs
description: 既存プロジェクトのソースコードを解析し、システム全体（概要・機能一覧・DB・API・画面・外部連携）から、1 機能の処理概要・詳細設計まで辿れる資料を生成する。機能の入口は画面に限らず、Webhook・定期実行・キュー・イベント処理も機能として扱う。各記述がどのソースを根拠にしているかを追跡でき、コードにあるのに資料に無いもの（漏れ）を coverage で示す。「このプロジェクトの設計書を作って」「機能一覧を出して」「DB 定義書を出して」「API 仕様書を作って」「画面遷移図を作って」「外部連携を整理して」「〇〇機能の処理概要を出して」「〇〇機能の詳細設計を書いて」「資料の漏れを確認して」「/source-docs」で起動。Markdown を正本に出力し、HTML へも変換できる。ソースから確認できないことは書かず「未確認」として残す。既存プロジェクトの資料化・構造把握・設計書作成の話が出たら必ずこのスキルを使う。
---

# source-docs

対象プロジェクトのソースコードから、**システム全体 → 機能 → 入口 → API / DB / 外部連携 → 実装**
まで辿れる資料を作る。

**最重要要件 — ソースコードから確認できないことを事実として書かない。**
分からなかったことは「未確認」とし、**調べた範囲**を残す。

## 仕組み

```
ソースコード ─ analyze ─▶ project-model.json（機械で確かめた事実・根拠つき）
                                   │
あなた（Claude）がコードを読む ─▶ annotations.json（意味づけ・根拠つき）
                                   │
                         check ─▶ render ─▶ Markdown / HTML / evidence.json / coverage.json
```

- **画面・API・テーブル・外部連携・ジョブ・イベント・機能の候補は、解析器が決める。** 自分で数え直さない
- **あなたが書くのは `annotations.json` だけ。** 機能の業務名、目的、入出力、詳細設計など、
  コードを読まないと分からない意味をここに書く。**Markdown を直接書かない・直さない**
  （直すと次の再生成で消え、資料どうしの整合も崩れる）
- 資料はすべて同じモデルから描かれるので、資料間で数や名前が食い違わない

## コマンド

```
/source-docs [対象] [--feature <機能>] [--format md|html|both] [--out <dir>] [--repo <path>]
```

| 対象 | 作るもの |
| --- | --- |
| `structure`（既定） | 全体資料 6 本（overview / features / database / api / screens / integrations） |
| `features` / `db` / `api` / `screens` / `integrations` / `overview` | その 1 本 |
| `flow --feature <機能>` | 1 機能の処理概要 `flow/<機能>.md` |
| `detail --feature <機能>` | 1 機能の詳細設計 `detail/<機能>.md` |
| `coverage` | 資料の漏れの確認だけ（生成しない） |

既定値: `--format md` / `--out docs/system` / `--repo` はカレントディレクトリ（複数可）。
**第 1 引数が上のどれでもなければ、勝手に解釈せず、候補を示して聞き返す。**
自然な依頼は対応表で読み替える（「機能一覧を出して」→ `features`、「注文確定の詳細設計」→ `detail --feature 注文確定`）。

プラグインなら起動名は `/source-docs:source-docs`、手動で置いたなら `/source-docs`。

## スクリプトの呼び方

すべて 1 本のコマンドで行う。**このファイル（SKILL.md）があるディレクトリを `<skill-dir>` とする。**

```bash
python3 <skill-dir>/scripts/source_docs.py <サブコマンド> [--out <dir>]
```

- 利用者の作業ディレクトリからの相対パス（`python3 scripts/...`）で呼ばない
- **Windows では `python3` が無いことがある。** 動かなければ `python`、それも無ければ `py`
- **1 回に 1 コマンドを実行する。** `&&` や `;` でつなぐと、利用者に毎回許可を求めることになる

| サブコマンド | 役割 |
| --- | --- |
| `analyze --repo <path>` | 解析して Project Model を書く。機能の候補も出す |
| `features` / `show <ID>` | 機能の一覧 / 1 つの実体の中身・関係・根拠 |
| `scaffold --feature <機能> --kind detail\|flow --write` | 詳細設計・処理概要の注釈の雛形を足す |
| `check` | 注釈を検査する |
| `render [--only a,b] [--feature X --kind detail] [--format both]` | 資料を書く。機密検出と coverage も走る |
| `coverage` / `verify` | コード → 資料の漏れ / 資料 → コードの実在 |
| `secrets --doc <path>` | 機密の検出 |

## 開かないファイル

**このスキルは他人のリポジトリを読む。認証情報が入りうるファイルは開かない。**
解析器はこれらを最初から除外する。**Read や Grep で自分で開くときも同じ規則を守る。**

| 対象 | 例 |
| --- | --- |
| 環境変数の実ファイル | `.env` / `.env.local` / `.env.production` |
| 鍵そのもの | `*.pem` / `*.key` / `*.p12` / `*.jks` / `id_rsa` |
| 認証情報ファイル | `credentials.json` / `service-account.json` / `.npmrc` / `.netrc` |
| ディレクトリごと | `.ssh/` / `.aws/` / `.gnupg/` / `.kube/` / `credentials/` / `secrets/` |
| シンボリックリンク | 名前が `schema.ts` でもリンク先は分からない |

例外は `.env.example` などのテンプレート。**変数名だけを見る。値は読まない・書かない。**
値らしきものが書かれていたら、その事実を人間に伝える。

## 手順

### 00 解析

```bash
python3 <skill-dir>/scripts/source_docs.py analyze --repo <path> [--repo <path>...] --out <out>
```

既に `<out>/.source-docs/annotations.json` があれば**消さない。** 再解析は注釈を保ったまま事実だけ更新する。

### 人間ゲート ①

`analyze` の出力から次を提示し、**対象を人間に確定してもらう。**

- スタックの判定結果。**「判定できず」の区分と、UNSUPPORTED（読み取れなかった箇所）は必ず伝える**
- 件数（画面・API・テーブル・外部連携・ジョブ・入口）
- 機能の候補一覧。**画面を持たない機能（Webhook・定期実行・キュー・イベント処理）も候補にある**ことを示す
- `flow` / `detail` なら、どの候補が依頼の機能か。名前で見つからなければ候補から選んでもらう。
  **機能の業務名はコードの命名からは決まらない。** 推測で当てはめない

### 01 注釈を書く

`references/annotations.md`（書式）と `references/writing.md`（共通規則）を読む。
加えて対象ごとの規則を、**その対象のものだけ**読む。

| 対象 | 読む規則 | 書く場所 |
| --- | --- | --- |
| 全体資料 | `references/structure.md` | `system` `applications` `api` `features` `entities` `exclusions` |
| 処理概要 | `references/flow.md` | `flows` |
| 詳細設計 | `references/detail.md` | `details`（`scaffold --write` で雛形を足してから埋める） |

- `show <ID>` で、実体の定義場所と関係の根拠（ファイルと行）を得てから、そのファイルを**実際に開いて読む**
- 書いたことには `evidence`（根拠のファイル）を付ける。**分からなければ書かずに `unverified` に送り、`searched`（調べた範囲）を書く**
- 全体資料を作るときは、**まとめて全部書かない。** `database` の分から始め、ゲート ② で型を決める

### 02 検査と生成

```bash
python3 <skill-dir>/scripts/source_docs.py check --out <out>
python3 <skill-dir>/scripts/source_docs.py render --out <out> [--only database] [--format both]
```

- `check` の ERROR は全部直してから `render` する。`render` は誤りがあると書き出さない
- WARN の「古い注釈」は、コードの変更や再解析で対象の ID が無くなったもの。資料には反映されない。
  `features` で今の候補を確かめ、付け直すか消す。完了報告でも件数を伝える
- **機密を検出したら、リトライせずその場で人間に見せる。** 伏せ字にする判断を機械にさせない
  （注釈に認証情報らしき値があると、`render` は書き出さずに止まる）

### 人間ゲート ②

生成した md のパスと内容を提示する。人間が見るのは**章構成・粒度・読みやすさ・名前の妥当性**。
指摘は注釈に反映して再生成する。全体資料なら、ここで決めた書き方を残りにも効かせる。

### 03 照合と完了報告

```bash
python3 <skill-dir>/scripts/source_docs.py verify --out <out>
python3 <skill-dir>/scripts/source_docs.py coverage --out <out>
```

`verify` の不一致は注釈を直して再生成する（1 回まで）。2 回目も残れば未確認として理由を書く。

完了報告で必ず出すもの。

1. 生成したファイルの一覧
2. **未確認一覧** — 各資料末尾のものを集約する
3. 機密検出の結果 — 0 件だったことも明示する
4. coverage — 種別ごとの documented / detected と、undocumented・unresolved・unsupported・excluded
5. 名前未確定のまま残った機能の数

---

## このスキルの限界（人間に伝えるべきこと）

- **検出は書き方に依存する。** 解析器が知らない書き方の API・バッチは候補に出ない。
  出なかったものは UNSUPPORTED と coverage の unresolved に現れる範囲でしか分からない
- **関係は import を 3 段まで辿った範囲。** DI コンテナや動的な呼び出しは辿れない
- **機械照合は作文しか検出できない。** 「分岐条件の説明が実装と違う」は検出できない。`detail` ほど効く
- **機密検出は誤検知も見落としもある。** だから自動で伏せ字にせず、常に人間の判断に回す

## 未実装

差分だけの再生成は無い（`analyze` → `render` の全体再生成で対応する。注釈は保たれる）。
Notion・Excel への出力は含まない。**頼まれても勝手に実装しない。**
