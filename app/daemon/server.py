"""WebSocket plumbing: /bridge for the extension, /ui for the overlay."""

import asyncio
import itertools
import json
import logging
from urllib.parse import urlsplit

import websockets
from websockets.asyncio.server import serve

log = logging.getLogger("music-dj")

HOST = "127.0.0.1"
PORT = 8787
DEFAULT_TIMEOUT = 30

# Browsers do not apply same-origin policy to WebSockets, so binding to
# localhost keeps nothing out on its own: any page the user has open can
# reach ws://127.0.0.1 and start issuing commands. What a page cannot do is
# forge or drop the Origin header, so the Origin is the line we draw.
EXTENSION_SCHEMES = ("chrome-extension", "moz-extension", "safari-web-extension")

# The overlay is a webview, so it sends an Origin like any browser would --
# pywebview serves its page from a loopback port that changes every launch, so
# the host is the only stable part to match on. This does mean a page served
# from your own machine could reach /ui; that is a far smaller surface
# than the open internet, and closing it properly needs a shared token.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def bridge_origin_allowed(origin, extension_ids=()):
    """Only an extension (or a non-browser client) may become the transport.

    Whoever holds /bridge answers every command and can shut the daemon down,
    so ordinary pages -- including local http(s) and file:// ones -- stay out.
    When config lists extension_ids, the origin must name one of them exactly;
    an empty header stays allowed because browsers cannot omit Origin, so it
    can only be a non-browser client such as the test driver.
    """
    if not origin:
        return True                      # every non-browser client
    parts = urlsplit(origin)
    if parts.scheme not in EXTENSION_SCHEMES:
        return False
    if extension_ids:
        return parts.hostname in {str(i).lower() for i in extension_ids}
    return True


def ui_origin_allowed(origin):
    """Extensions, loopback pages, and non-browser clients only.

    Matched on the parsed hostname rather than a string prefix: an attacker can
    register 127.0.0.1.example.com, and "starts with http://127.0.0.1" would
    wave it straight through.
    """
    if not origin:
        return True                      # every non-browser client
    parts = urlsplit(origin)
    if parts.scheme in EXTENSION_SCHEMES:
        return True
    if parts.scheme == "file":
        return True
    return parts.scheme in ("http", "https") and parts.hostname in LOCAL_HOSTS


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

    def fail_pending(self, reason):
        """Give up on replies that are never coming.

        A tab reload destroys the page mid-command while the worker's socket
        stays open, so nothing else would notice and the caller would sit out
        its full timeout.
        """
        for mid, fut in list(self.pending.items()):
            if not fut.done():
                fut.set_result({"error": reason})
            self.pending.pop(mid, None)

    def detach(self, ws):
        if self.ws is not ws:
            # A newer connection already superseded this one (extension
            # reload, worker churn). Failing the pending map here would kill
            # in-flight calls that were sent on the LIVE socket and burn a
            # perfectly playable track; anything truly orphaned on the old
            # socket falls back to its timeout.
            return
        self.ws = None
        # Nothing will ever answer these now; fail them rather than letting
        # callers sit until their timeout.
        self.fail_pending("extension disconnected")

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
        # Config can pin /bridge to specific extension ids; empty means any
        # extension origin (the id changes with every unpacked reload).
        self.extension_ids = tuple(i for i in
                                   (dj.config.get("extension_ids") or []) if i)
        self._sends = set()           # strong refs, same pattern as _handlers
        dj.subscribe(self._broadcast)

    def _broadcast(self, state):
        payload = json.dumps(state, ensure_ascii=False)
        for ws in list(self.ui_clients):
            task = asyncio.create_task(self._send(ws, payload))
            self._sends.add(task)
            task.add_done_callback(self._sends.discard)

    async def _send(self, ws, payload):
        try:
            await ws.send(payload)
        except Exception:
            self.ui_clients.discard(ws)

    async def handler(self, conn):
        origin = conn.request.headers.get("Origin")
        path = conn.request.path
        if path.startswith("/bridge"):
            if not bridge_origin_allowed(origin, self.extension_ids):
                log.warning("rejected /bridge connection from origin %s", origin)
                await conn.close(code=1008, reason="origin not allowed")
                return
            await self._bridge(conn)
        elif path.startswith("/ui"):
            if not ui_origin_allowed(origin):
                log.warning("rejected /ui connection from origin %s", origin)
                await conn.close(code=1008, reason="origin not allowed")
                return
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
