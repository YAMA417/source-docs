"""JavaScript / TypeScript に共通する読み取り。フレームワークには依存しない。

- import の解決（相対パス・`@/` `~/`・tsconfig の paths）
- API の呼び出し（fetch / axios などのクライアント）
- 画面遷移（router.push / Link href / redirect …）
"""

import json
import re

from .base import Adapter, dirname, join, strip_comments

JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
RESOLVE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")

RE_IMPORT = re.compile(
    r"""\bimport\s+(?:type\s+)?([\w*{}\s,$]+?)\s+from\s+['"]([^'"]+)['"]""")
RE_IMPORT_BARE = re.compile(r"""\bimport\s+['"]([^'"]+)['"]""")
RE_REQUIRE = re.compile(
    r"""\b(?:const|let|var)\s+([\w{}\s,:$]+?)\s*=\s*require\(\s*['"]([^'"]+)['"]\s*\)""")
RE_DYNAMIC_IMPORT = re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""")
RE_EXPORT_FROM = re.compile(r"""\bexport\s+(?:\*|\{[^}]*\})\s+from\s+['"]([^'"]+)['"]""")


def is_js(rel):
    return rel.endswith(JS_EXT)


def parse_imports(text):
    """[(ローカル名の列, モジュール指定子)]。名前の無い import は空の列。"""
    out = []
    for m in RE_IMPORT.finditer(text):
        out.append((_names(m.group(1)), m.group(2)))
    for m in RE_REQUIRE.finditer(text):
        out.append((_names(m.group(1)), m.group(2)))
    for rx in (RE_IMPORT_BARE, RE_DYNAMIC_IMPORT, RE_EXPORT_FROM):
        for m in rx.finditer(text):
            out.append(([], m.group(1)))
    return out


def _names(clause):
    names = []
    clause = clause.strip()
    brace = re.search(r"\{([^}]*)\}", clause)
    if brace:
        for part in brace.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            part = re.sub(r"^type\s+", "", part)
            local = re.split(r"\s+as\s+|:", part)[-1].strip()
            if local:
                names.append(local)
        clause = clause[:brace.start()] + clause[brace.end():]
    for part in clause.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"\*\s+as\s+([\w$]+)", part)
        names.append(m.group(1) if m else part)
    return [n for n in names if re.fullmatch(r"[\w$]+", n)]


def package_name(spec):
    """`@scope/pkg/sub` → `@scope/pkg`、`pkg/sub` → `pkg`。相対指定なら None。"""
    if spec.startswith((".", "/")) or spec.startswith(("@/", "~/", "#")):
        return None
    parts = spec.split("/")
    if spec.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


class JsLanguage(Adapter):
    name = "javascript"

    def __init__(self):
        self._alias_cache = {}

    def applies(self, app):
        return any(is_js(f) for f in app.files)

    # --- import --------------------------------------------------------------

    def _aliases(self, app):
        """tsconfig / jsconfig の paths と、慣習の `@/` `~/`。"""
        if app.id in self._alias_cache:
            return self._alias_cache[app.id]
        root = app.root_rel
        aliases = []
        for name in ("tsconfig.json", "jsconfig.json"):
            rel = join(root, name)
            if not rel or not app.repo.exists(rel):
                continue
            try:
                data = json.loads(_strip_json_comments(app.read(rel)))
            except ValueError:
                continue
            opts = data.get("compilerOptions") or {}
            base = join(root, opts.get("baseUrl") or ".") or ""
            for pattern, targets in (opts.get("paths") or {}).items():
                if not isinstance(targets, list) or not targets:
                    continue
                prefix = pattern.rstrip("*")
                for t in targets:
                    aliases.append((prefix, join(base, t.rstrip("*")) or ""))
        for prefix in ("@/", "~/"):
            aliases.append((prefix, join(root, "src") or "src"))
            aliases.append((prefix, root))
        self._alias_cache[app.id] = aliases
        return aliases

    def resolve(self, app, rel, spec):
        base_dir = dirname(rel)
        if spec.startswith("."):
            bases = [join(base_dir, spec)]
        else:
            bases = []
            for prefix, target in self._aliases(app):
                if spec.startswith(prefix):
                    bases.append(join(target, spec[len(prefix):]))
            if not bases:
                return None
        for base in bases:
            if base is None:
                continue
            candidates = [base]
            stripped = re.sub(r"\.(m|c)?js$", "", base)
            for b in {base, stripped}:
                candidates += [b + ext for ext in RESOLVE_EXT]
                candidates += [b + "/index" + ext for ext in RESOLVE_EXT]
            for c in candidates:
                if app.repo.exists(c):
                    return c
        return None

    def imports(self, app, rel, text):
        if not is_js(rel):
            return {}
        out = {}
        for i, (names, spec) in enumerate(parse_imports(text)):
            target = self.resolve(app, rel, spec)
            if not target:
                continue
            if not names:
                out[f"*{i}"] = target
            for n in names:
                out[n] = target
        return out

    def aliases(self, app, rel, text):
        if not is_js(rel):
            return {}
        out = {}
        for m in RE_IMPORT.finditer(text):
            brace = re.search(r"\{([^}]*)\}", m.group(1))
            if not brace:
                continue
            for part in brace.group(1).split(","):
                pair = re.split(r"\s+as\s+", re.sub(r"^\s*type\s+", "", part.strip()))
                if len(pair) == 2 and pair[0].strip() and pair[1].strip():
                    out[pair[1].strip()] = pair[0].strip()
        return out

    # --- 使っているもの --------------------------------------------------------

    def usages(self, app, rel, text):
        if not is_js(rel):
            return []
        text = strip_comments(text)
        return list(_calls(text)) + list(_navigations(text))


def _strip_json_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:\"])//[^\n]*", r"\1", text)
    return re.sub(r",(\s*[}\]])", r"\1", text)


# fetch('/api/x') / fetch(`${BASE}/api/x/${id}`, { method: 'POST' })
RE_FETCH = re.compile(r"""\b(?:fetch|\$fetch|ofetch|useFetch|useSWR|useQuery)\(\s*(['"`])(.*?)\1""", re.S)
# axios.post('/api/x') / api.get(`/x`) / client.delete('/x')
RE_CLIENT = re.compile(
    r"""\b(axios|api|apiClient|client|http|httpClient|ky|request|\w+Client|\w+Api)\s*\.\s*"""
    r"""(get|post|put|patch|delete)\s*(?:<[^>()]*>)?\(\s*(['"`])(.*?)\3""", re.S)
RE_METHOD_OPTION = re.compile(r"""method\s*:\s*['"](\w+)['"]""")


def _path_of(raw):
    """呼び出し先の文字列から、パスの部分だけを取り出す。外部 URL なら None。"""
    raw = raw.strip()
    if re.match(r"https?://", raw):
        return None
    # `${BASE_URL}/api/x` の先頭の式を落とす
    raw = re.sub(r"^\$\{[^}]*\}", "", raw)
    if not raw.startswith("/"):
        return None
    raw = re.sub(r"\$\{[^}]*\}", ":param", raw)
    return raw.split("?", 1)[0]


def _calls(text):
    for m in RE_FETCH.finditer(text):
        path = _path_of(m.group(2))
        if not path:
            continue
        window = text[m.end():m.end() + 300]
        close = window.find(")")
        method_m = RE_METHOD_OPTION.search(window[:close if close != -1 else 300])
        method = method_m.group(1).upper() if method_m else None
        yield {"kind": "call", "method": method or "GET", "path": path,
               "methodSource": "option" if method_m else "default", "offset": m.start()}
    for m in RE_CLIENT.finditer(text):
        path = _path_of(m.group(4))
        if not path:
            continue
        yield {"kind": "call", "method": m.group(2).upper(), "path": path,
               "methodSource": "client", "offset": m.start()}


RE_NAV = [
    (re.compile(r"""\brouter\s*\.\s*(push|replace|navigate)\(\s*(['"`])(/[^'"`]*)\2"""), None),
    (re.compile(r"""\brouter\s*\.\s*(push|replace|navigate)\(\s*\{\s*pathname\s*:\s*(['"`])(/[^'"`]*)\2"""), None),
    (re.compile(r"""\b(redirect|permanentRedirect)\(\s*(['"`])(/[^'"`]*)\2"""), "redirect"),
    (re.compile(r"""\b(navigate)\(\s*(['"`])(/[^'"`]*)\2"""), "navigate"),
    (re.compile(r"""\b(href)\s*=\s*\{?\s*(['"`])(/[^'"`]*)\2"""), "link"),
]


def _navigations(text):
    for rx, how in RE_NAV:
        for m in rx.finditer(text):
            route = re.sub(r"\$\{[^}]*\}", ":param", m.group(3)).split("?", 1)[0].split("#", 1)[0]
            yield {"kind": "navigate", "route": route or "/", "how": how or m.group(1),
                   "offset": m.start()}
