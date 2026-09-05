# Expo Router

## 画面の規約が Next.js と違う

Expo Router は **`app/` 配下の `.tsx` がそのまま画面**になる。`page.tsx` という
規約は無い。`app/settings.tsx` はそのまま `/settings` 画面。

`inventory.py` はスタック判定で Expo / React Native を検出したときだけ、
このパターンを適用する。

## 画面ではないファイル

- `_layout.tsx` — レイアウト。タブやスタックの構成を定義する。**画面一覧に載せない**
- `+not-found.tsx` `+html.tsx` — 特殊ルート

ただし `_layout.tsx` は**遷移の構造を読むために必ず開く。** どの画面がタブに属し、
どれがモーダルで開くかはここにしか書かれていない。

## グループルート

`app/(tabs)/index.tsx` の `(tabs)` は URL に現れない。**Route 欄には括弧を除いたパスを書く。**
`app/(tabs)/calendar.tsx` → Route は `/calendar`。

括弧付きディレクトリは「レイアウトを共有するグループ」を意味する。同じグループの画面は
同じ `_layout.tsx` の下にあり、タブやスタックを共有する。画面遷移図ではこの構造を反映する。

## 遷移の辿り方

`router.push` / `router.replace` / `router.back` / `<Link href>` / `router.navigate`。
引数は文字列リテラルのことが多いが、`{ pathname, params }` の形も使われる。

`router.replace` は履歴を置き換える。戻れなくなるので、認証後の遷移などで使われる。
**push と replace を区別して図に反映する。**

## URL を持たない画面はない

Expo Router はファイルベースなので全画面が Route を持つ。
逆に、React Navigation を直接使っているプロジェクトでは Route が画面名になる。
`app/` ディレクトリが無ければ React Navigation を疑い、Navigator の定義を探す。
