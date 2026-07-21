"""Smoke-test the /bridge transport with a mock extension.

Proves the driver's server, id correlation, event passthrough and error replies
work without involving Chrome. It says nothing about whether MusicKit actually
plays -- that needs the live page.

    python tools/transport_smoke.py
"""

import asyncio
import json
import os
import sys

import websockets
from websockets.asyncio.server import serve

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_driver  # noqa: E402

PORT = 8799
failures = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else "  " + str(detail)))
    if not cond:
        failures.append(label)


async def mock_extension(url, ready):
    """Stands in for background.js: replies to commands, emits one event."""
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"evt": "ready", "storefront": "fr",
                                  "previewOnly": False}))
        ready.set()
        async for raw in ws:
            msg = json.loads(raw)
            cmd, mid = msg.get("cmd"), msg.get("id")
            if cmd == "search":
                await ws.send(json.dumps({"id": mid, "songs": [
                    {"catalogId": "1556503755", "title": "The Journey",
                     "artist": "Folamour", "artworkUrl": None,
                     "durationMs": 400000}]}))
            elif cmd == "listPlaylists":
                await ws.send(json.dumps({"id": mid, "playlists": [
                    {"id": "p.test", "name": "scratch", "canEdit": True,
                     "trackCount": 0}]}))
            elif cmd == "explode":
                await ws.send(json.dumps({"id": mid, "error": "no tab"}))
            elif cmd == "silent":
                pass  # never replies -- exercises the timeout path
            else:
                await ws.send(json.dumps({"id": mid, "ok": True}))


async def main():
    url = "ws://127.0.0.1:%d/bridge" % PORT
    cli_driver.HOST, cli_driver.PORT = "127.0.0.1", PORT

    events = []
    original_out = cli_driver.out
    cli_driver.out = lambda m: events.append(m)

    async with serve(cli_driver.handler, "127.0.0.1", PORT):
        ready = asyncio.Event()
        task = asyncio.create_task(mock_extension(url, ready))
        await asyncio.wait_for(ready.wait(), 5)
        await asyncio.sleep(0.3)

        cli_driver.out = original_out
        print("transport smoke test")

        check("extension connects on /bridge", cli_driver.bridge is not None)
        check("unprompted event reaches the driver",
              any('"evt": "ready"' in e or '"evt":"ready"' in e for e in events),
              events)

        r = await cli_driver.call({"cmd": "search", "term": "Folamour"})
        check("search reply correlates by id",
              r.get("songs", [{}])[0].get("catalogId") == "1556503755", r)

        r = await cli_driver.call({"cmd": "listPlaylists"})
        check("listPlaylists returns editable playlists",
              r.get("playlists", [{}])[0].get("id") == "p.test", r)

        r = await cli_driver.call({"cmd": "pause"})
        check("transport commands ack", r.get("ok") is True, r)

        # Two in flight at once must not cross their replies.
        a, b = await asyncio.gather(
            cli_driver.call({"cmd": "search", "term": "one"}),
            cli_driver.call({"cmd": "listPlaylists"}))
        check("concurrent calls don't cross replies",
              "songs" in a and "playlists" in b, (a, b))

        r = await cli_driver.call({"cmd": "explode"})
        check("error replies surface", r.get("error") == "no tab", r)

        r = await cli_driver.call({"cmd": "silent"}, timeout=0.5)
        check("unanswered command times out", "timed out" in str(r.get("error")), r)
        check("timed-out call leaves no pending entry", not cli_driver.pending,
              cli_driver.pending)

        task.cancel()
        await asyncio.sleep(0.2)
        check("disconnect clears the bridge", cli_driver.bridge is None)

    print(("\nFAILED: " + ", ".join(failures)) if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
