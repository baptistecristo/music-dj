"""End-to-end over real WebSockets, with a mock extension.

Exercises the wiring the unit tests skip: the server, the id correlation, the
UI push path, and recovery when the extension drops. Everything except the
browser itself is the real code.
"""

import asyncio
import json
import os
import sys

import pytest
import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import core, server, store  # noqa: E402

PROFILE = """
## Mood → seed directions

- **locked in / deep focus** → instrumental: Daft Punk, Sofiane Pamart.
- **tense / debugging** → warm soul: Bill Withers, Al Green.
"""

PORT = 8791


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DIR", str(tmp_path))
    (tmp_path / store.PROFILE).write_text(PROFILE, encoding="utf-8")
    (tmp_path / store.STATE).write_text('{"current_mood": "coding"}', encoding="utf-8")
    return tmp_path


class MockExtension:
    """Speaks the documented protocol back at the daemon."""

    def __init__(self, url):
        self.url = url
        self.ws = None
        self.played = []
        self.task = None

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url)
        self.task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        await self.ws.close()

    async def _loop(self):
        counter = 0
        async for raw in self.ws:
            msg = json.loads(raw)
            cmd, mid = msg.get("cmd"), msg.get("id")
            if cmd == "search":
                counter += 1
                await self.ws.send(json.dumps({"id": mid, "songs": [{
                    "catalogId": "cat%d" % counter,
                    "title": "Song %d" % counter,
                    "artist": msg.get("term", "x").split()[0],
                    "artworkUrl": "http://art/%d.jpg" % counter,
                    "durationMs": 200000}]}))
            elif cmd == "play":
                self.played.append(msg["catalogId"])
                await self.ws.send(json.dumps({"id": mid, "ok": True}))
            elif cmd == "playlistTracks":
                await self.ws.send(json.dumps({"id": mid, "trackIds": []}))
            else:
                await self.ws.send(json.dumps({"id": mid, "ok": True}))

    async def emit(self, evt):
        await self.ws.send(json.dumps(evt))


async def boot(port):
    tx = server.BridgeTransport()
    dj = core.DJ(tx, config={})
    tx.on_event = dj.on_event
    srv = server.Server(dj, tx, port=port)
    task = asyncio.create_task(srv.run())
    await asyncio.sleep(0.15)
    return dj, tx, task


async def test_extension_connects_and_the_daemon_plays():
    dj, tx, task = await boot(PORT)
    try:
        async with MockExtension("ws://127.0.0.1:%d/bridge" % PORT) as ext:
            await asyncio.sleep(0.1)
            assert tx.connected is True
            track = await dj.play_next()
            assert track is not None
            await asyncio.sleep(0.1)
            assert ext.played == [track["catalogId"]]
    finally:
        task.cancel()


async def test_track_ended_from_the_extension_advances_playback():
    dj, tx, task = await boot(PORT + 1)
    try:
        async with MockExtension("ws://127.0.0.1:%d/bridge" % (PORT + 1)) as ext:
            await asyncio.sleep(0.1)
            first = await dj.play_next()
            await ext.emit({"evt": "trackEnded", "catalogId": first["catalogId"]})
            await asyncio.sleep(0.4)
            assert len(ext.played) == 2
            assert ext.played[1] != first["catalogId"]
    finally:
        task.cancel()


async def test_the_overlay_receives_state_and_can_drive_playback():
    dj, tx, task = await boot(PORT + 2)
    try:
        async with MockExtension("ws://127.0.0.1:%d/bridge" % (PORT + 2)) as ext:
            await asyncio.sleep(0.1)
            async with websockets.connect("ws://127.0.0.1:%d/ui" % (PORT + 2)) as ui:
                first = json.loads(await asyncio.wait_for(ui.recv(), 2))
                assert "nowPlaying" in first and first["connected"] is True

                await dj.play_next()
                pushed = json.loads(await asyncio.wait_for(ui.recv(), 2))
                assert pushed["nowPlaying"]["title"]
                assert pushed["why"].startswith("from your profile")

                await ui.send(json.dumps({"action": "skip"}))
                await asyncio.sleep(0.3)
                assert len(ext.played) == 2
    finally:
        task.cancel()


async def test_the_overlay_can_pin_a_mood():
    dj, tx, task = await boot(PORT + 3)
    try:
        async with MockExtension("ws://127.0.0.1:%d/bridge" % (PORT + 3)):
            await asyncio.sleep(0.1)
            async with websockets.connect("ws://127.0.0.1:%d/ui" % (PORT + 3)) as ui:
                await ui.recv()
                await ui.send(json.dumps({"action": "setMood", "mood": "debugging",
                                          "pinned": True}))
                await asyncio.sleep(0.5)
                assert dj.mood == "debugging" and dj.pinned is True
                assert dj.lane == "tense"
    finally:
        task.cancel()


async def test_losing_the_extension_stops_commands_without_crashing():
    dj, tx, task = await boot(PORT + 4)
    try:
        async with MockExtension("ws://127.0.0.1:%d/bridge" % (PORT + 4)):
            await asyncio.sleep(0.1)
            await dj.play_next()
        await asyncio.sleep(0.2)

        assert tx.connected is False
        assert await dj.play_next() is None
        assert dj.ui_state()["notice"] == "no player"
    finally:
        task.cancel()


async def test_the_extension_can_reconnect_after_a_tab_reload():
    port = PORT + 5
    dj, tx, task = await boot(port)
    try:
        async with MockExtension("ws://127.0.0.1:%d/bridge" % port):
            await asyncio.sleep(0.1)
            track = await dj.play_next()
        await asyncio.sleep(0.2)
        assert tx.connected is False

        # Tab reloaded: fresh socket, and the daemon re-seeds what was playing.
        async with MockExtension("ws://127.0.0.1:%d/bridge" % port) as ext2:
            await asyncio.sleep(0.1)
            await ext2.emit({"evt": "injected"})
            await asyncio.sleep(0.3)
            assert ext2.played == [track["catalogId"]]
    finally:
        task.cancel()


async def test_a_command_with_no_reply_times_out_rather_than_hanging():
    port = PORT + 6
    dj, tx, task = await boot(port)
    try:
        async with websockets.connect("ws://127.0.0.1:%d/bridge" % port):
            await asyncio.sleep(0.1)
            reply = await tx.call({"cmd": "status"}, timeout=0.3)
            assert "timed out" in reply["error"]
            assert tx.pending == {}
    finally:
        task.cancel()


async def test_a_disconnect_fails_calls_in_flight_immediately():
    port = PORT + 7
    dj, tx, task = await boot(port)
    try:
        conn = await websockets.connect("ws://127.0.0.1:%d/bridge" % port)
        await asyncio.sleep(0.1)
        pending = asyncio.create_task(tx.call({"cmd": "status"}, timeout=30))
        await asyncio.sleep(0.1)
        await conn.close()
        reply = await asyncio.wait_for(pending, 2)   # must not wait out the 30s
        assert "disconnected" in reply["error"]
    finally:
        task.cancel()


async def test_an_unknown_path_is_rejected():
    port = PORT + 8
    dj, tx, task = await boot(port)
    try:
        with pytest.raises(Exception):
            async with websockets.connect("ws://127.0.0.1:%d/nope" % port) as ws:
                await asyncio.wait_for(ws.recv(), 2)
    finally:
        task.cancel()


async def test_track_ended_completes_the_advance_without_deadlocking():
    """An event handler that issues a command must not block the socket reader.

    trackEnded is delivered on the bridge connection, and handling it sends a
    "play" back down that same connection. If the read loop waits for the
    handler to finish, the reply can never be read and the call sits until it
    times out — so asserting the command was *sent* is not enough, we have to
    see the advance actually land.
    """
    port = PORT + 9
    dj, tx, task = await boot(port)
    try:
        async with MockExtension("ws://127.0.0.1:%d/bridge" % port) as ext:
            await asyncio.sleep(0.1)
            first = await dj.play_next()
            await ext.emit({"evt": "trackEnded", "catalogId": first["catalogId"]})
            await asyncio.sleep(0.5)
            assert dj.current is not None
            assert dj.current["catalogId"] != first["catalogId"]
    finally:
        task.cancel()


async def test_a_web_page_origin_is_rejected():
    """Browsers do not apply same-origin policy to WebSockets.

    Without an Origin check, any page the user happens to have open can open
    ws://127.0.0.1 and drive the daemon — including rating a track five stars,
    which writes to their real Apple Music playlist.
    """
    port = PORT + 10
    dj, tx, task = await boot(port)
    try:
        with pytest.raises(Exception):
            async with websockets.connect(
                    "ws://127.0.0.1:%d/ui" % port,
                    origin="https://evil.example") as ws:
                await asyncio.wait_for(ws.recv(), 2)
    finally:
        task.cancel()


async def test_the_extension_origin_is_accepted():
    port = PORT + 11
    dj, tx, task = await boot(port)
    try:
        async with websockets.connect(
                "ws://127.0.0.1:%d/bridge" % port,
                origin="chrome-extension://abcdefghijklmnop") as ws:
            await asyncio.sleep(0.1)
            assert tx.connected is True
            assert ws.state.name == "OPEN"
    finally:
        task.cancel()
