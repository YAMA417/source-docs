"""DB の最終状態を組み立てる。フレームワークには依存しない。

検出器は、カラム・インデックス・一意制約・主キー・外部キーを「どの情報源から読んだか」を付けて
記録するだけにしている。ここで `precedence.RULES["db.physical"]` に従って 1 つにまとめる。

- migration があるテーブルは、migration の最終状態を物理状態とする（confirmed）
  - ORM の定義と食い違う箇所は、物理状態を変えずに診断（WARNING）として残す
  - ORM にだけあるカラムは、本文に出さず「未確定」に送る（unresolved）
- migration が無いテーブルは、ORM・フレームワークのスキーマ定義を物理状態とする
  - 同じ優先度の情報源どうしが食い違えば conflict（本文に出さない。ERROR）
- 同じアプリに migration があるのに、migration に無いテーブルは unresolved
- ORM の関係（Prisma の @relation など）は物理 FK と別の `ormRelations` として残す
"""

from .precedence import DB_SOURCE_KIND, rank
from .status import diagnostic


def _kind(source):
    return DB_SOURCE_KIND.get(source, "orm-schema")


def merge(b):
    apps_with_migrations = {t["app"] for t in b.items("tables")
                            if any(c["source"] == "migration" for c in t["columns"])
                            or any(e.get("rule") == "sql" for e in t["evidence"])}
    # 参照先の名前（ORM のモデル名・変数名）を先にテーブル名へそろえる。物理 FK と ORM の関係を突き合わせるため
    _resolve_reference_names(b)
    for t in b.items("tables"):
        _merge_table(b, t, t["app"] in apps_with_migrations)


def _merge_table(b, t, app_has_migrations):
    cols = t["columns"]
    sources = sorted({c["source"] for c in cols} | {i.get("source") for i in t["indexes"] if i.get("source")})
    has_migration = any(c["source"] == "migration" for c in cols) or any(
        e.get("rule") == "sql" for e in t["evidence"])
    t["sources"] = sources
    key = t["key"]

    if has_migration:
        t["physicalSource"] = "migration"
        final = [c for c in cols if c["source"] == "migration"]
        by_name = {c["name"]: c for c in final}
        for o in (c for c in cols if c["source"] != "migration"):
            m = by_name.get(o["name"])
            if m is None:
                b.add_pending(t, "columns", o["name"], "unresolved",
                              f"{o['source']} の定義にあるが migration の最終状態に無い（未適用の migration がある可能性）")
                b.add_diagnostic(diagnostic(
                    "WARNING", "db.orm-only-column",
                    f"{key}.{o['name']}: {o['source']} の定義にあるが migration に無い", entity=key))
                continue
            diffs = []
            if o.get("nullable") is not None and m.get("nullable") is not None and o["nullable"] != m["nullable"]:
                diffs.append("NULL 可否")
            if o.get("unique") and not (m.get("unique") or _unique_in_migration(t, o["name"])):
                diffs.append("一意制約")
            if diffs:
                b.add_diagnostic(diagnostic(
                    "WARNING", "db.orm-drift",
                    f"{key}.{o['name']}: {o['source']} の定義と migration の最終状態で {'・'.join(diffs)} が違う。"
                    "migration を採用した", entity=key))
        t["indexes"] = [i for i in t["indexes"] if i.get("source") == "migration"] + \
            _drift_only(b, t, "indexes", key)
        t["uniques"] = [u for u in t["uniques"] if u.get("source") == "migration"] + \
            _drift_only(b, t, "uniques", key)
        t["checks"] = [c for c in t["checks"] if c.get("source") in ("migration", None)]
        t["physicalForeignKeys"] = [dict(f, status="confirmed") for f in t["physicalForeignKeys"]
                                    if f.get("source") == "migration"]
        pk = t.get("_pk", {}).get("migration") or [c["name"] for c in final if c["primaryKey"]]
    else:
        # 同じ優先度の情報源が複数あれば、カラムごとに突き合わせる
        best = min((c["source"] for c in cols), key=lambda s: rank("db.physical", _kind(s)), default=None)
        t["physicalSource"] = best
        final, seen = [], {}
        for c in sorted(cols, key=lambda c: rank("db.physical", _kind(c["source"]))):
            if c["name"] not in seen:
                seen[c["name"]] = c
                final.append(c)
                continue
            first = seen[c["name"]]
            same_rank = rank("db.physical", _kind(first["source"])) == rank("db.physical", _kind(c["source"]))
            if same_rank and c.get("nullable") is not None and first.get("nullable") is not None \
                    and c["nullable"] != first["nullable"]:
                first["status"] = "conflict"
                b.add_diagnostic(diagnostic(
                    "ERROR", "db.conflict",
                    f"{key}.{c['name']}: {first['source']} と {c['source']} で NULL 可否が矛盾している", entity=key))
        for c in [c for c in final if c["status"] == "conflict"]:
            b.add_pending(t, "columns", c["name"], "conflict", "同じ優先度の定義どうしで矛盾している")
        final = [c for c in final if c["status"] != "conflict"]
        t["indexes"] = _dedupe(t["indexes"], lambda i: (tuple(i["columns"]), i["unique"]))
        t["uniques"] = _dedupe(t["uniques"], lambda u: tuple(u["columns"]))
        t["physicalForeignKeys"] = [dict(f, status="inferred") for f in
                                    _dedupe(t["physicalForeignKeys"],
                                            lambda f: (tuple(f["columns"]), f["references"]["table"]))]
        pks = t.get("_pk", {})
        pk = next((pks[s] for s in sorted(pks, key=lambda s: rank("db.physical", _kind(s)))), None) \
            or [c["name"] for c in final if c["primaryKey"]]
        if app_has_migrations and t["kind"] == "table":
            b.mark_unresolved(t, "同じアプリに migration があるのに、このテーブルを作る migration が無い"
                                 "（ORM の定義だけにある）")
            b.add_diagnostic(diagnostic("WARNING", "db.orm-only-table",
                                        f"{key}: ORM の定義にあるが migration に無い", entity=key))

    # 1 列だけの一意インデックス・一意制約は、そのカラムの一意として確定させる（描画側で判断させない）
    single_unique = {i["columns"][0] for i in t["indexes"] if i["unique"] and len(i["columns"]) == 1}
    single_unique |= {u["columns"][0] for u in t["uniques"] if len(u["columns"]) == 1}
    for c in final:
        c["primaryKey"] = c["name"] in pk
        if c["primaryKey"]:
            c["nullable"] = False
        if c["name"] in single_unique:
            c["unique"] = True
    t["columns"] = final
    t["primaryKey"] = list(pk)
    t["ormRelations"] = [dict(r, status="confirmed") for r in
                         _dedupe(t["ormRelations"], lambda r: (r.get("name"), tuple(r["columns"]),
                                                               r["references"]["table"]))]
    _physical_fk_state(t, has_migration)
    t.pop("_pk", None)
    t.pop("_droppedForeignKeyDetails", None)


def _physical_fk_state(t, has_migration):
    """ORM の関係ごとに、DB に外部キーがあるかを別の事実として持つ。

    physicalForeignKey: True（ある）/ False（無いと確定。migration の最終状態に無い）/ None（確かめられない）
    ORM の関係があって外部キーが無いのは矛盾ではない（Prisma の relationMode = "prisma" など）。
    """
    physical = {(tuple(f["columns"]), f["references"]["table"]) for f in t["physicalForeignKeys"]}
    dropped = {(tuple(f["columns"]), f["references"]["table"]): f.get("name")
               for f in t.get("_droppedForeignKeyDetails", [])}
    for r in t["ormRelations"]:
        key = (tuple(r["columns"]), r["references"]["table"])
        if not r["columns"]:
            r["physicalForeignKey"] = None
            r["physicalForeignKeyReason"] = "関係のカラムが定義に無い（フレームワークが中間テーブルなどで持つ）"
        elif key in physical:
            r["physicalForeignKey"] = True
        elif has_migration:
            r["physicalForeignKey"] = False
            r["physicalForeignKeyReason"] = (f"migration で {dropped[key]} を削除した" if key in dropped
                                             else "migration の最終状態に外部キーが無い")
        else:
            r["physicalForeignKey"] = None
            r["physicalForeignKeyReason"] = "migration が無く、DB に外部キーがあるかを確かめられない"


def _unique_in_migration(t, name):
    return any(i.get("source") == "migration" and i["unique"] and i["columns"] == [name] for i in t["indexes"]) \
        or any(u.get("source") == "migration" and u["columns"] == [name] for u in t["uniques"])


def _drift_only(b, t, group, key):
    """ORM にだけある一意制約・インデックスは物理状態に入れず、診断だけ残す。"""
    # 一意制約は、migration では UNIQUE 制約でも一意インデックスでも書ける。どちらでも同じものとみなす
    mig = {(tuple(x["columns"]), x.get("unique", True)) for g in ("indexes", "uniques") for x in t[g]
           if x.get("source") == "migration"}
    for x in t[group]:
        if x.get("source") != "migration" and (tuple(x["columns"]), x.get("unique", True)) not in mig:
            b.add_diagnostic(diagnostic(
                "WARNING", "db.orm-drift",
                f"{key}: {x.get('source')} の定義にある{'一意制約' if group == 'uniques' else 'インデックス'}"
                f"（{', '.join(x['columns'])}）が migration に無い", entity=key))
    return []


def _dedupe(items, key):
    seen, out = set(), []
    for item in items:
        k = key(item)
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


def _resolve_reference_names(b):
    """参照先を、ORM の変数名・モデル名からテーブル名へ置き換える。"""
    for t in b.items("tables"):
        for fk in t["physicalForeignKeys"] + t["ormRelations"] + t.get("_droppedForeignKeyDetails", []):
            fk["references"]["table"] = _table_name(b, fk["references"]["table"])
        for c in t["columns"]:
            if c.get("references"):
                c["references"]["table"] = _table_name(b, c["references"]["table"])


def _table_name(b, alias):
    uids = b.table_aliases.get(alias, set())
    if len(uids) == 1:
        item = b.get(next(iter(uids)))
        if item:
            return item["key"]
    return alias
