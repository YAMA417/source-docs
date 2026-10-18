"""Strapi のコア API（find / findOne / create / update / delete）の入出力を、content-type の
schema.json と Strapi の規約から組み立てる。

コアルータの API はコードにハンドラが無いので、注釈を書く Claude が読む場所も無い。
一方で、入力項目とレスポンスの項目は schema.json に書いてあり、包み方（v4 の
`data.attributes`、v5 の平たい `data`）は Strapi のメジャーバージョンで決まる。
ここではその 2 つだけから作る。認証・認可は管理画面の設定（DB）で決まりコードに無いので作らない。
"""

import re

SOURCE = "schema.json と Strapi {major} の規約から機械で作成（注釈で上書きできる）"

# レスポンスに既定では含まれない種類。populate を指定したときだけ返る
POPULATED_ONLY = ("relation", "media", "component", "dynamiczone")


def strapi_major(version):
    m = re.search(r"(\d+)", version or "")
    return int(m.group(1)) if m else None


def _type(spec):
    t = spec.get("type")
    if t == "enumeration":
        return "enumeration"
    if t == "relation":
        return f"relation（{spec.get('relation')}）"
    if t == "media":
        return "media（複数）" if spec.get("multiple") else "media"
    if t == "component":
        return "component（繰り返し）" if spec.get("repeatable") else "component"
    return t or "不明"


def _constraints(spec):
    out = []
    if spec.get("type") == "enumeration":
        out.append("値は " + " / ".join(spec.get("enum") or []))
    if spec.get("unique"):
        out.append("一意")
    for key, label in (("minLength", "最小文字数"), ("maxLength", "最大文字数"), ("min", "最小値"),
                       ("max", "最大値")):
        if spec.get(key) is not None:
            out.append(f"{label} {spec[key]}")
    if spec.get("type") == "uid" and spec.get("targetField"):
        out.append(f"省略時は {spec['targetField']} から生成")
    return "、".join(out)


def core_spec(action, attributes, info, major):
    """1 本のコア API の {source, request, responses}。作れなければ None。"""
    if major not in (4, 5):
        return None
    single = info.get("kind") == "singleType"
    localized = bool(((info.get("pluginOptions") or {}).get("i18n") or {}).get("localized"))
    draft = bool((info.get("options") or {}).get("draftAndPublish"))
    request = []
    if action in ("findOne", "update", "delete") and not single:
        request.append({"in": "Path", "name": "id", "type": "string" if major == 5 else "integer",
                        "required": True, "description": "documentId" if major == 5 else "id"})
    if action in ("find", "findOne"):
        if action == "find" and not single:
            request += [
                {"in": "Query", "name": "filters", "type": "object", "required": False, "description": "絞り込み条件"},
                {"in": "Query", "name": "sort", "type": "string", "required": False, "description": "並び順"},
                {"in": "Query", "name": "pagination[page] / pagination[pageSize]", "type": "integer",
                 "required": False, "description": "ページ指定（start / limit でも指定できる）"},
            ]
        request += [
            {"in": "Query", "name": "fields", "type": "string[]", "required": False, "description": "返す項目を絞る"},
            {"in": "Query", "name": "populate", "type": "string | object", "required": False,
             "description": "リレーション・メディア・コンポーネントを含める"},
        ]
        if localized:
            request.append({"in": "Query", "name": "locale", "type": "string", "required": False,
                            "description": "言語"})
        if draft and major == 4:
            request.append({"in": "Query", "name": "publicationState", "type": "live | preview",
                            "required": False, "description": "下書きを含めるか"})
    if action in ("create", "update"):
        for name, spec in attributes.items():
            if not isinstance(spec, dict):
                continue
            request.append({"in": "Body", "name": f"data.{name}", "type": _type(spec),
                            "required": bool(spec.get("required")) and action == "create",
                            "constraints": _constraints(spec), "description": ""})

    base = "data[]" if action == "find" and not single else "data"
    fields = [{"name": f"{base}.documentId" if major == 5 else f"{base}.id",
               "type": "string" if major == 5 else "integer", "description": "識別子"}]
    prefix = f"{base}." if major == 5 else f"{base}.attributes."
    for name, spec in attributes.items():
        if not isinstance(spec, dict) or spec.get("private"):
            continue
        note = "populate を指定したときだけ含まれる" if spec.get("type") in POPULATED_ONLY else ""
        fields.append({"name": prefix + name, "type": _type(spec), "description": note})
    for name in ("createdAt", "updatedAt") + (("publishedAt",) if draft else ()):
        fields.append({"name": prefix + name, "type": "datetime", "description": "Strapi が自動で付ける"})
    if action == "find" and not single:
        fields.append({"name": "meta.pagination", "type": "object",
                       "description": "page / pageSize / pageCount / total"})
    status_desc = {"find": "一覧を返す", "findOne": "1 件を返す", "create": "作成した 1 件を返す",
                   "update": "更新後の 1 件を返す", "delete": "削除した 1 件を返す"}[action]
    if major == 5 and action == "delete":
        responses = [{"status": 204, "description": "本文なし"}]
    else:
        responses = [{"status": 200, "description": status_desc, "fields": fields}]
    return {"source": SOURCE.format(major=major), "request": request, "responses": responses}


ACTION_LABEL = {"find": "一覧を取得する", "findOne": "1 件を取得する", "create": "作成する",
                "update": "更新する", "delete": "削除する"}
