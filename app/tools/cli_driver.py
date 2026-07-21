"""Throwaway driver for milestone 1.

Stands up the /bridge WebSocket the extension dials, then gives you a prompt to
fire commands at the tab and watch events come back. This is scaffolding to
prove the extension works from outside the browser -- the real daemon replaces
it in milestone 2.

    python tools/cli_driver.py

Commands:
    playlists                 list editable playlists
    search <term>             search the catalog
    play <catalogId>          play a track
    pause | resume | skip | prev | status
    create <name>             create a scratch playlist (for testing adds)
    tracks <playlistId>       list track ids already in a playlist
    add <playlistId> <catalogId>
    raw {"cmd":...}           send an arbitrary command
    quit
"""

import asyncio
import itertools
import json
import shlex
import sys

import websockets
from websockets.asyncio.server import serve

HOST, PORT = "127.0.0.1", 8787

bridge = None                      # the extension's socket
pending: dict[str, asyncio.Future] = {}
counter = itertools.count(1)


def out(msg: str) -> None:
    # Keep prints on their own line so they don't collide with the prompt.
    sys.stdout.write("\r\033[K" + msg + "\n")
    sys.stdout.flush()


async def handler(conn):
    global bridge
    path = conn.request.path
    if not path.startswith("/bridge"):
        await conn.close(code=1008, reason="only /bridge in the CLI driver")
        return

    bridge = conn
    out("== extension connected")
    try:
        async for raw in conn:
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            mid = msg.get("id")
            if mid is not None and mid in pending:
                fut = pending.pop(mid)
                if not fut.done():
                    fut.set_result(msg)
                continue

            evt = msg.get("evt")
            if evt == "keepalive":
                continue
            out("<< event " + json.dumps(msg, ensure_ascii=False)[:400])
    except websockets.ConnectionClosed:
        pass
    finally:
        if bridge is conn:
            bridge = None
        out("== extension disconnected")


async def call(cmd: dict, timeout: float = 30.0):
    if bridge is None:
        return {"error": "extension not connected"}
    mid = str(next(counter))
    cmd = dict(cmd, id=mid)
    fut = asyncio.get_running_loop().create_future()
    pending[mid] = fut
    await bridge.send(json.dumps(cmd))
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        pending.pop(mid, None)
        return {"error": "timed out after %ss" % timeout}


def show(reply: dict) -> None:
    if "error" in reply:
        out("!! " + str(reply["error"]))
        return

    if "playlists" in reply:
        pls = reply["playlists"]
        out("%d editable playlists" % len(pls))
        for p in pls:
            count = "" if p.get("trackCount") is None else "  %s tracks" % p["trackCount"]
            out("   %-24s %s%s" % (p["id"], p["name"], count))
        return

    if "songs" in reply:
        for s in reply["songs"]:
            out("   %-12s %s - %s" % (s["catalogId"], s.get("artist"), s.get("title")))
        return

    if "trackIds" in reply:
        ids = reply["trackIds"]
        out("%d track ids in playlist" % len(ids))
        out("   " + ", ".join(ids[:20]) + (" ..." if len(ids) > 20 else ""))
        return

    out(json.dumps(reply, ensure_ascii=False, indent=2))


async def repl():
    loop = asyncio.get_running_loop()
    out("driver up on ws://%s:%d/bridge -- load the extension and open "
        "music.apple.com" % (HOST, PORT))

    while True:
        line = await loop.run_in_executor(None, lambda: sys.stdin.readline())
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            out("!! unbalanced quotes")
            continue
        verb, args = parts[0].lower(), parts[1:]

        if verb in ("quit", "exit"):
            return

        simple = {"pause": "pause", "resume": "resume", "skip": "skip",
                  "prev": "previous", "status": "status",
                  "playlists": "listPlaylists"}
        if verb in simple:
            show(await call({"cmd": simple[verb]}))
        elif verb == "search":
            show(await call({"cmd": "search", "term": " ".join(args)}))
        elif verb == "play":
            show(await call({"cmd": "play", "catalogId": args[0]}, timeout=40))
        elif verb == "create":
            show(await call({"cmd": "createPlaylist", "name": " ".join(args)}))
        elif verb == "tracks":
            show(await call({"cmd": "playlistTracks", "playlistId": args[0]}, timeout=60))
        elif verb == "add":
            show(await call({"cmd": "addToPlaylist",
                             "playlistId": args[0], "catalogId": args[1]}))
        elif verb == "raw":
            show(await call(json.loads(line[4:])))
        else:
            out("?? unknown: " + verb)


async def main():
    async with serve(handler, HOST, PORT):
        await repl()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
