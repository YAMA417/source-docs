"""Mermaid の図を組み立てる。ラベルは必ずここを通してエスケープする。

生成 HTML では Mermaid を `securityLevel: strict` で描くが、ラベルに `"` や `<` が
混ざると図そのものが壊れる。資料の元は他人のリポジトリの文字列なので、ここで潰す。
"""

import re

MAX_NODES = 12


def label(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return (text.replace("#", "#35;").replace('"', "#quot;").replace("<", "#lt;")
            .replace(">", "#gt;").replace(";", "#59;"))


def ident(text, prefix="n"):
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(text))
    if not s or not s[0].isalpha():
        s = prefix + "_" + s
    return s


def block(lines):
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def chunks(items, size=MAX_NODES):
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def components(nodes, edges):
    """無向グラフの連結成分。図を分けるときに、つながりを切らないために使う。"""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    groups = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def pack(groups, size=MAX_NODES):
    """連結成分を、1 枚あたり size 個までの図に詰める。大きな成分はそのまま切る。"""
    out, current = [], []
    for g in groups:
        if len(g) > size:
            if current:
                out.append(current)
                current = []
            out.extend(chunks(g, size))
            continue
        if len(current) + len(g) > size:
            out.append(current)
            current = []
        current.extend(g)
    if current:
        out.append(current)
    return out


def er_type(text):
    # `dynamiczone(blocks.a,blocks.b)` のような長い型は、括弧の前だけにする
    base = str(text or "unknown").split("(")[0].split("（")[0]
    t = re.sub(r"[^A-Za-z0-9_]", "_", base).strip("_")
    return t or "unknown"


def sequence(participants, steps):
    lines = ["sequenceDiagram"]
    for p in participants:
        kind = p.get("kind")
        word = "actor" if kind == "user" else ("database" if kind == "db" else "participant")
        if word == "database":
            word = "participant"
        lines.append(f"    {word} {ident(p['id'])} as {label(p['label'])}")

    def emit(items, depth):
        pad = "    " * depth
        for s in items or []:
            if "alt" in s:
                for i, branch in enumerate(s["alt"]):
                    head = "alt" if i == 0 else "else"
                    lines.append(f"{pad}{head} {label(branch.get('condition') or '')}")
                    emit(branch.get("steps"), depth + 1)
                lines.append(f"{pad}end")
                continue
            kind = s.get("kind", "call")
            if kind == "note":
                target = ident(s.get("from") or s.get("to"))
                lines.append(f"{pad}Note over {target}: {label(s.get('message'))}")
                continue
            arrow = {"call": "->>", "return": "-->>", "async": "-)"}[kind]
            lines.append(f"{pad}{ident(s['from'])}{arrow}{ident(s['to'])}: {label(s.get('message'))}")

    emit(steps, 1)
    return block(lines)


def state(states, transitions):
    lines = ["stateDiagram-v2"]
    for s in states:
        lines.append(f"    state \"{label(s)}\" as {ident(s, 's')}")
    for t in transitions:
        a = "[*]" if t["from"] == "[*]" else ident(t["from"], "s")
        b = "[*]" if t["to"] == "[*]" else ident(t["to"], "s")
        trig = f" : {label(t.get('trigger'))}" if t.get("trigger") else ""
        lines.append(f"    {a} --> {b}{trig}")
    return block(lines)
