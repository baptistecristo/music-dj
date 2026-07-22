"""Native messaging launcher: framing, idempotence, spawn wiring."""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host import launcher  # noqa: E402


def test_framing_round_trip():
    buf = io.BytesIO()
    launcher.write_message(buf, {"ok": True, "already": False})
    buf.seek(0)
    assert launcher.read_message(buf) == {"ok": True, "already": False}


def test_read_message_on_closed_stream_returns_none():
    assert launcher.read_message(io.BytesIO()) is None


def test_start_skips_spawn_when_daemon_already_listening(monkeypatch):
    spawned = []
    monkeypatch.setattr(launcher, "daemon_running", lambda: True)
    monkeypatch.setattr(launcher, "spawn", spawned.append)
    assert launcher.handle({"cmd": "start"}) == {"ok": True, "already": True}
    assert spawned == []


def test_start_spawns_daemon_and_overlay(monkeypatch):
    spawned = []
    monkeypatch.setattr(launcher, "daemon_running", lambda: False)
    monkeypatch.setattr(launcher, "spawn", spawned.append)
    assert launcher.handle({"cmd": "start"}) == {"ok": True, "already": False}
    assert spawned == ["daemon.main", "overlay.app"]


def test_unknown_command_is_refused():
    assert launcher.handle({"cmd": "dance"})["ok"] is False
    assert launcher.handle(None)["ok"] is False
