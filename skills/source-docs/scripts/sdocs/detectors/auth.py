"""ログイン状態の確認と、未ログインのときの分岐（JavaScript / TypeScript）。

拾うもの（入口からたどったコード片の中から）。

- session         ログイン状態を取り出す呼び出し（`getServerSession()` / `auth()` / `getCurrentUser()` …）
- unauth-branch   取り出した値が無いときに返す・投げる分岐（`if (!session) return new Response(.., { status: 403 })`）

**分岐がコードに書かれているときだけ、未ログイン時の応答を確定させる。** セッションを取り出していても
分岐が無ければ（`where: { authorId: session?.user.id }` に渡しているだけなど）、応答は確定させない。
"""

import re

from .base import Adapter, strip_comments
from .javascript import is_js

RE_SESSION = re.compile(
    r"\b(?:getServerSession|getSession|unstable_getServerSession|getCurrentUser|currentUser|getToken|"
    r"getAuth|getUserSession|requireUser|requireSession|validateRequest)\s*\(|"
    r"(?<![\w.])auth\s*\(\s*\)|\bsupabase\s*\.\s*auth\s*\.\s*getUser\s*\(|\breq\s*\.\s*(?:user|session)\b")

# `if (!session)` / `if (!session?.user)` / `if (!user || ...)` に続く return / throw
RE_BRANCH = re.compile(
    r"\bif\s*\(\s*!\s*\(?\s*(?:await\s+)?(?:session|user|currentUser|token|auth|userId|me|authUser)\b"
    r"[\w?.!\[\]'\"]*\s*\)?\s*(?:\|\|[^)]{0,80})?\)\s*\{?\s*(return|throw)\b([^;\n]{0,200})")
RE_STATUS = re.compile(r"status\s*:\s*(\d{3})|\.status\(\s*(\d{3})\s*\)|\b(401|403)\b")


class Auth(Adapter):
    name = "auth"

    def applies(self, app):
        return any(is_js(f) for f in app.files[:2000])

    def usages(self, app, rel, text):
        if not is_js(rel):
            return []
        code = strip_comments(text)
        out = [{"kind": "session", "offset": m.start()} for m in RE_SESSION.finditer(code)]
        for m in RE_BRANCH.finditer(code):
            tail = m.group(2)
            st = RE_STATUS.search(tail)
            if st:
                result = int(next(g for g in st.groups() if g))
            elif re.search(r"\bredirect\s*\(", tail):
                result = "redirect"
            elif re.search(r"\bnotFound\s*\(", tail):
                result = 404
            else:
                result = None
            out.append({"kind": "unauth-branch", "result": result, "offset": m.start()})
        return out
