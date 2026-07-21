"""WebSocket plumbing: /bridge for the extension, /ui for the overlay."""

import asyncio
import itertools
import json
import logging

import websockets
from websockets.asyncio.server import serve

log = logging.getLogger("music-dj")

HOST = "127.0.0.1"
PORT = 8787
DEFAULT_TIMEOUT = 30

# Browsers do not apply same-origin policy to WebSockets, so binding to
# localhost keeps nothing out on its own: any page the user has open can
# reach ws://127.0.0.1 and start issuing commands. What a page cannot do is
# forge or drop the Origin header, so an extension origin (or no origin at
# all, which is every non-browser client) is the line we draw.
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")


def origin_allowed(origin):
    if not origin:
        return True
    return origin.startswith(EXTENSION_ORIGINS)


class BridgeTransport:
    """Request/reply over the extension socket, correlated by id.

    `connected` is false whenever there is no live socket, which is what makes
    the daemon go quiet instead of piling up commands for a tab that is gone.
    """

    def __init__(self):
        self.ws = None
        self.pending = {}
        self._ids = itertools.count(1)
        self.on_event = None          # set by the daemon
        self._handlers = set()        # live event-handler tasks, see dispatch()

    @property
    def connected(self):
        return self.ws is not None

    async def call(self, cmd, timeout=DEFAULT_TIMEOUT):
        if self.ws is None:
            return {"error": "extension not connected"}
        mid = str(next(self._ids))
        fut = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        try:
            await self.ws.send(json.dumps(dict(cmd, id=mid)))
        except Exception as exc:
            self.pending.pop(mid, None)
            return {"error": "send failed: %s" % exc}
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(mid, None)
            return {"error": "timed out after %ss" % timeout}

    def attach(self, ws):
        self.ws = ws

    def detach(self, ws):
        if self.ws is ws:
            self.ws = None
        # Nothing will ever answer these now; fail them rather than letting
        # callers sit until their timeout.
        for mid, fut in list(self.pending.items()):
            if not fut.done():
                fut.set_result({"error": "extension disconnected"})
            self.pending.pop(mid, None)

    async def dispatch(self, msg):
        mid = msg.get("id")
        if mid is not None and mid in self.pending:
            fut = self.pending.pop(mid)
            if not fut.done():
                fut.set_result(msg)
            return
        if msg.get("evt") == "keepalive":
            return
        if self.on_event:
            # Handlers issue commands back over this same socket, and their
            # replies can only be read by the loop that called us. Awaiting
            # the handler here would park that loop until its own reply timed
            # out, so every trackEnded stalled the advance it triggered.
            task = asyncio.create_task(self.on_event(msg))
            self._handlers.add(task)
            task.add_done_callback(self._handler_done)

    def _handler_done(self, task):
        self._handlers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("event handler failed", exc_info=task.exception())


class Server:
    def __init__(self, dj, transport, host=HOST, port=PORT):
        self.dj = dj
        self.tx = transport
        self.host, self.port = host, port
        self.ui_clients = set()
        dj.subscribe(self._broadcast)

    def _broadcast(self, state):
        payload = json.dumps(state, ensure_ascii=False)
        for ws in list(self.ui_clients):
            asyncio.create_task(self._send(ws, payload))

    async def _send(self, ws, payload):
        try:
            await ws.send(payload)
        except Exception:
            self.ui_clients.discard(ws)

    async def handler(self, conn):
        origin = conn.request.headers.get("Origin")
        if not origin_allowed(origin):
            log.warning("rejected connection from origin %s", origin)
            await conn.close(code=1008, reason="origin not allowed")
            return

        path = conn.request.path
        if path.startswith("/bridge"):
            await self._bridge(conn)
        elif path.startswith("/ui"):
            await self._ui(conn)
        else:
            await conn.close(code=1008, reason="unknown path")

    async def _bridge(self, conn):
        log.info("extension connected")
        self.tx.attach(conn)
        self.dj.push()
        try:
            async for raw in conn:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                try:
                    await self.tx.dispatch(msg)
                except Exception:
                    log.exception("bridge dispatch failed")
        except websockets.ConnectionClosed:
            pass
        finally:
            self.tx.detach(conn)
            log.info("extension disconnected")
            self.dj.push()

    async def _ui(self, conn):
        self.ui_clients.add(conn)
        try:
            await conn.send(json.dumps(self.dj.ui_state(), ensure_ascii=False))
            async for raw in conn:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                try:
                    await self.dj.on_action(msg)
                except Exception:
                    log.exception("ui action failed")
        except websockets.ConnectionClosed:
            pass
        finally:
            self.ui_clients.discard(conn)

    async def run(self):
        async with serve(self.handler, self.host, self.port):
            log.info("daemon listening on ws://%s:%d", self.host, self.port)
            await asyncio.Future()      # run until cancelled
