"""画面を持たない入口（JavaScript）。

定期実行（node-cron / cron / Nest の @Cron / Deno.cron / vercel.json の crons）、
キュー（BullMQ / Bull / Nest の @Processor / amqplib / SQS）、イベント（EventEmitter /
Nest の @OnEvent / Pub/Sub）、常駐ワーカー、CLI、WebSocket、GraphQL、tRPC。
"""

import json
import re

from .base import (Adapter, is_cron_expression, line_of, match_brace, segments, stem,
                   strip_comments)
from .lang_js import is_js, parse_imports

RE_NODE_CRON = re.compile(r"""\b(\w+)\s*\.\s*schedule\(\s*(['"`])([^'"`]+)\2""")
RE_CRONJOB = re.compile(r"""\bnew\s+CronJob\(\s*(['"`])([^'"`]+)\1""")
RE_CRONJOB_FROM = re.compile(r"""CronJob\.from\(\s*\{[^}]*?cronTime\s*:\s*(['"`])([^'"`]+)\1""", re.S)
RE_NEST_CRON = re.compile(r"""@Cron\(\s*(?:(['"`])([^'"`]+)\1|([\w.]+))""")
RE_NEST_INTERVAL = re.compile(r"""@(Interval|Timeout)\(\s*(?:['"](\w+)['"]\s*,\s*)?(\d+)""")
RE_DENO_CRON = re.compile(r"""\bDeno\.cron\(\s*(['"`])([^'"`]+)\1\s*,\s*(['"`])([^'"`]+)\3""")
RE_METHOD_AFTER = re.compile(r"\n\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?(\w+)\s*\(")

RE_BULL_WORKER = re.compile(r"""\bnew\s+Worker\(\s*(['"`])([\w:.\-]+)\1""")
RE_BULL_QUEUE = re.compile(r"""\b(?:const|let|var|export\s+const)\s+(\w+)\s*=\s*new\s+Queue\(\s*(['"`])([\w:.\-]+)\2""")
RE_QUEUE_PROCESS = re.compile(r"""\b(\w+)\s*\.\s*process\(\s*(?:(['"`])([\w:.\-]+)\2\s*,)?""")
RE_QUEUE_ADD = re.compile(r"""\b(\w+)\s*\.\s*(?:add|addBulk)\(""")
RE_NEST_PROCESSOR = re.compile(r"""@Processor\(\s*(['"`])([\w:.\-]+)\1""")
RE_AMQP_CONSUME = re.compile(r"""\.\s*consume\(\s*(['"`])([\w:.\-]+)\1""")
RE_AMQP_SEND = re.compile(r"""\.\s*sendToQueue\(\s*(['"`])([\w:.\-]+)\1""")
RE_SQS_RECEIVE = re.compile(r"\bReceiveMessageCommand\b")
RE_SQS_SEND = re.compile(r"\bSendMessage(?:Batch)?Command\b")

# イベント名は `order.created` `user:signup` のように区切りを持つものだけを拾う。
# `click` `data` `error` のような汎用イベントを機能の入口と取り違えないため
EVENT_NAME = r"[A-Za-z][\w\-]*[.:][\w.:\-]+"
RE_EVENT_ON = re.compile(r"""\b(\w+)\s*\.\s*(?:on|once|addListener|subscribe)\(\s*(['"`])(""" + EVENT_NAME + r""")\2""")
RE_EVENT_EMIT = re.compile(r"""\b(\w+)\s*\.\s*(?:emit|emitAsync|publish|dispatch)\(\s*(['"`])(""" + EVENT_NAME + r""")\2""")
RE_NEST_ON_EVENT = re.compile(r"""@OnEvent\(\s*(['"`])(""" + EVENT_NAME + r""")\1""")
NOT_EVENT_RECEIVERS = {"process", "window", "document", "req", "res", "request", "response",
                       "stream", "socket", "io", "ws", "child", "server", "app", "router"}

RE_SET_INTERVAL = re.compile(r"\bsetInterval\(")
RE_WHILE_TRUE = re.compile(r"\bwhile\s*\(\s*true\s*\)")
RE_WORKER_FILE = re.compile(r"(^|/)workers?/[^/]+\.(ts|js|mjs)$|\.worker\.(ts|js|mjs)$")

RE_COMMANDER = re.compile(r"""\.\s*command\(\s*(['"`])([\w:\-]+)""")

RE_SOCKET_ON = re.compile(r"""\b(socket|client|ws)\s*\.\s*on\(\s*(['"`])([\w:.\-]+)\2""")
RE_WS_SERVER = re.compile(r"\bnew\s+(?:WebSocketServer|WebSocket\.Server|Server)\(|\bio\.on\(\s*['\"]connection['\"]")
SOCKET_BUILTIN = {"connection", "connect", "disconnect", "disconnecting", "error", "close", "open",
                  "message", "ping", "pong", "upgrade", "headers", "listening"}

RE_GQL_TYPE = re.compile(r"\b(?:extend\s+)?type\s+(Query|Mutation|Subscription)\s*\{")
RE_GQL_FIELD = re.compile(r"^\s*(\w+)\s*(?:\(|:)", re.M)
RE_TRPC = re.compile(r"\b(\w+)\s*:\s*(?:\w+)?[pP]rocedure\b")
RE_TRPC_KIND = re.compile(r"\.\s*(query|mutation|subscription)\s*\(")


def _imports_any(text, packages):
    return any(spec == p or spec.startswith(p + "/") for _, spec in parse_imports(text) for p in packages)


class BackgroundJs(Adapter):
    name = "background-js"

    def applies(self, app):
        return any(is_js(f) for f in app.files)

    def collect(self, app, b):
        # キューの変数名は別ファイルで定義されることが多いので、先に全体から集める
        self.queue_vars = getattr(self, "queue_vars", {})
        queue_vars = self.queue_vars.setdefault(app.id, {})
        for rel in app.files:
            if is_js(rel) and "Queue(" in app.read(rel):
                for m in RE_BULL_QUEUE.finditer(strip_comments(app.read(rel))):
                    queue_vars[m.group(1)] = m.group(3)
        consumers = set()
        for rel in app.files:
            if not is_js(rel):
                continue
            text = app.read(rel)
            if not text:
                continue
            code = strip_comments(text)
            if self._jobs(app, b, rel, code):
                consumers.add(rel)
            if self._queues(app, b, rel, code):
                consumers.add(rel)
            self._events(app, b, rel, code)
            self._cli(app, b, rel, code)
            self._websocket(app, b, rel, code)
            self._graphql(app, b, rel, code)
            self._trpc(app, b, rel, code)
        for rel in app.files:
            if RE_WORKER_FILE.search(app.app_rel(rel)) and rel not in consumers:
                self._worker(app, b, rel)
        self._package_bin(app, b)
        for rel in app.files:
            if app.app_rel(rel) == "vercel.json":
                self._vercel(app, b, rel)
        for rel in app.files:
            if rel.endswith((".graphql", ".gql")):
                self._graphql(app, b, rel, app.read(rel), sdl=True)

    # --- 定期実行 ------------------------------------------------------------

    def _jobs(self, app, b, rel, code):
        found = False
        imports_cron = _imports_any(code, ("node-cron", "cron", "@nestjs/schedule", "croner",
                                           "node-schedule", "toad-scheduler"))
        hits = []
        if imports_cron:
            for m in RE_NODE_CRON.finditer(code):
                if is_cron_expression(m.group(3)):
                    hits.append((m.start(), m.group(3), None, "node-cron"))
            for m in list(RE_CRONJOB.finditer(code)) + list(RE_CRONJOB_FROM.finditer(code)):
                hits.append((m.start(), m.group(2), None, "cron"))
        for m in RE_NEST_CRON.finditer(code):
            method = RE_METHOD_AFTER.search(code, m.end())
            hits.append((m.start(), m.group(2) or m.group(3), method.group(1) if method else None,
                         "nest.cron"))
        for m in RE_DENO_CRON.finditer(code):
            hits.append((m.start(), m.group(4), m.group(2), "deno.cron"))
        spans = {s: (s, e) for s, e in segments(code, [h[0] for h in hits])}
        for start, schedule, name, rule in hits:
            name = name or (f"{stem(rel)}" if len(hits) == 1 else f"{stem(rel)}#{line_of(code, start)}")
            s, e = spans[start]
            job = b.add_job(app.ref, "cron", name, app.ev(rel, line_of(code, start), rule),
                            schedule=schedule, location=app.loc(rel, s, e, line_of(code, start)))
            if not is_cron_expression(schedule or ""):
                b.mark_unresolved(job, f"スケジュールが定数参照（{schedule}）で、値をここでは確定していない")
            found = True
        for m in RE_NEST_INTERVAL.finditer(code):
            method = RE_METHOD_AFTER.search(code, m.end())
            name = m.group(2) or (method.group(1) if method else f"{stem(rel)}#{line_of(code, m.start())}")
            b.add_job(app.ref, "scheduler", name, app.ev(rel, line_of(code, m.start()), "nest.interval"),
                      schedule=f"{m.group(1).lower()} {m.group(3)}ms",
                      location=app.loc(rel, m.start(), None, line_of(code, m.start())))
            found = True
        return found

    # --- キュー --------------------------------------------------------------

    def _queues(self, app, b, rel, code):
        found = False
        for m in RE_BULL_WORKER.finditer(code):
            if not _imports_any(code, ("bullmq",)):
                continue
            self._consumer(app, b, rel, code, m.start(), m.group(2), "bullmq.worker")
            found = True
        for m in RE_QUEUE_PROCESS.finditer(code):
            queue = self.queue_vars[app.id].get(m.group(1))
            if queue:
                self._consumer(app, b, rel, code, m.start(), queue, "bull.process")
                found = True
        for m in RE_NEST_PROCESSOR.finditer(code):
            self._consumer(app, b, rel, code, m.start(), m.group(2), "nest.processor")
            found = True
        if _imports_any(code, ("amqplib", "amqp-connection-manager")):
            for m in RE_AMQP_CONSUME.finditer(code):
                self._consumer(app, b, rel, code, m.start(), m.group(2), "amqp.consume")
                found = True
        if RE_SQS_RECEIVE.search(code) and _imports_any(code, ("@aws-sdk/client-sqs",)):
            m = RE_SQS_RECEIVE.search(code)
            job = self._consumer(app, b, rel, code, m.start(), None, "sqs.receive")
            b.mark_unresolved(job, "受信するキューの名前をコードから確定できない（URL を設定値から読む）")
            found = True
        return found

    def _consumer(self, app, b, rel, code, start, queue, rule):
        return b.add_job(app.ref, "queue-consumer", queue or stem(rel),
                         app.ev(rel, line_of(code, start), rule), queue=queue,
                         location=app.loc(rel, start, None, line_of(code, start)))

    # --- イベント -------------------------------------------------------------

    def _events(self, app, b, rel, code):
        subs = [(m.start(), m.group(3)) for m in RE_EVENT_ON.finditer(code)
                if m.group(1) not in NOT_EVENT_RECEIVERS]
        subs += [(m.start(), m.group(2)) for m in RE_NEST_ON_EVENT.finditer(code)]
        spans = {s: (s, e) for s, e in segments(code, [s for s, _ in subs])}
        for start, name in subs:
            s, e = spans[start]
            b.add_event_subscriber(app.ref, name, app.ev(rel, line_of(code, start), "event.subscribe"),
                                   location=app.loc(rel, s, e, line_of(code, start)))

    # --- CLI / WebSocket / GraphQL / tRPC -----------------------------------

    def _cli(self, app, b, rel, code):
        if not _imports_any(code, ("commander", "yargs", "cac", "citty", "clipanion")):
            return
        hits = [(m.start(), m.group(2)) for m in RE_COMMANDER.finditer(code)]
        spans = {s: (s, e) for s, e in segments(code, [s for s, _ in hits])}
        for start, name in hits:
            s, e = spans[start]
            b.add_command(app.ref, name, app.ev(rel, line_of(code, start), "cli.command"),
                          location=app.loc(rel, s, e, line_of(code, start)))

    def _package_bin(self, app, b):
        rel = "/".join(p for p in (app.root_rel, "package.json") if p)
        if not app.repo.exists(rel):
            return
        try:
            data = json.loads(app.read(rel) or "{}")
        except ValueError:
            return
        bins = data.get("bin")
        if isinstance(bins, str):
            bins = {data.get("name") or "cli": bins}
        if not isinstance(bins, dict):
            return
        for name, target in bins.items():
            target_rel = "/".join(p for p in (app.root_rel, str(target).lstrip("./")) if p)
            loc = app.loc(target_rel, 0, None, 1) if app.repo.exists(target_rel) else None
            b.add_command(app.ref, name, app.ev(rel, None, "package.bin"), location=loc)

    def _websocket(self, app, b, rel, code):
        if not RE_WS_SERVER.search(code):
            return
        hits = [(m.start(), m.group(3)) for m in RE_SOCKET_ON.finditer(code)
                if m.group(3) not in SOCKET_BUILTIN]
        spans = {s: (s, e) for s, e in segments(code, [s for s, _ in hits])}
        for start, name in hits:
            s, e = spans[start]
            b.add_endpoint(app.ref, "MESSAGE", name, app.ev(rel, line_of(code, start), "websocket.on"),
                           location=app.loc(rel, s, e, line_of(code, start)), protocol="websocket")

    def _graphql(self, app, b, rel, code, sdl=False):
        if not sdl and "type Query" not in code and "type Mutation" not in code:
            return
        for m in RE_GQL_TYPE.finditer(code):
            close = match_brace(code, m.end() - 1)
            body = code[m.end():close]
            for f in RE_GQL_FIELD.finditer(body):
                start = m.end() + f.start()
                b.add_endpoint(app.ref, m.group(1).upper(), f"{m.group(1)}.{f.group(1)}",
                               app.ev(rel, line_of(code, start), "graphql.sdl"),
                               protocol="graphql", operation=f.group(1))

    def _trpc(self, app, b, rel, code):
        if not _imports_any(code, ("@trpc/server",)) and "Procedure" not in code:
            return
        hits = []
        for m in RE_TRPC.finditer(code):
            kind = RE_TRPC_KIND.search(code, m.end(), m.end() + 1500)
            if kind:
                hits.append((m.start(), m.group(1), kind.group(1).upper()))
        spans = {s: (s, e) for s, e in segments(code, [h[0] for h in hits])}
        for start, name, kind in hits:
            s, e = spans[start]
            b.add_endpoint(app.ref, kind, f"trpc.{name}", app.ev(rel, line_of(code, start), "trpc.procedure"),
                           location=app.loc(rel, s, e, line_of(code, start)), protocol="rpc", operation=name)

    # --- ワーカー・プラットフォーム設定 ---------------------------------------------

    def _worker(self, app, b, rel):
        text = app.read(rel)
        code = strip_comments(text)
        loop = RE_SET_INTERVAL.search(code) or RE_WHILE_TRUE.search(code)
        if not loop:
            b.add_unsupported(app.ref, rel, "worker", "ワーカーらしい置き場所だが、起動の仕組みを読み取れない")
            return
        interval = re.search(r"setInterval\([\s\S]*?,\s*([\d_*\s]+)\)", code)
        job = b.add_job(app.ref, "worker", stem(rel), app.ev(rel, line_of(code, loop.start()), "worker.loop"),
                        schedule=("interval " + interval.group(1).strip() + "ms") if interval else "loop",
                        location=app.loc(rel, 0, len(code), 1))
        if not interval:
            b.mark_unresolved(job, "実行間隔をコードから確定できない")

    def _vercel(self, app, b, rel):
        try:
            data = json.loads(app.read(rel) or "{}")
        except ValueError:
            return
        for cron in data.get("crons") or []:
            path, schedule = cron.get("path"), cron.get("schedule")
            if not path:
                continue
            b.add_job(app.ref, "cron", path, app.ev(rel, None, "vercel.cron"), schedule=schedule,
                      trigger={"kind": "call", "method": "GET", "path": path})

    # --- 使っているもの --------------------------------------------------------

    def usages(self, app, rel, text):
        if not is_js(rel):
            return []
        code = strip_comments(text)
        out = []
        for m in RE_EVENT_EMIT.finditer(code):
            if m.group(1) not in NOT_EVENT_RECEIVERS:
                out.append({"kind": "publish", "event": m.group(3), "offset": m.start()})
        for m in RE_QUEUE_ADD.finditer(code):
            queue = getattr(self, "queue_vars", {}).get(app.id, {}).get(m.group(1))
            if queue:
                out.append({"kind": "enqueue", "queue": queue, "offset": m.start()})
        for m in RE_AMQP_SEND.finditer(code):
            out.append({"kind": "enqueue", "queue": m.group(2), "offset": m.start()})
        if RE_SQS_SEND.search(code):
            out.append({"kind": "enqueue", "queue": None, "offset": RE_SQS_SEND.search(code).start()})
        return out
