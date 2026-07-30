"""Native messaging launcher: framing, idempotence, spawn wiring, and the
registration that has to land somewhere different on every platform."""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host import launcher, register  # noqa: E402


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


# --------------------------------------------------------------- spawning

def test_windows_detaches_with_creation_flags(monkeypatch):
    monkeypatch.setattr(launcher, "WINDOWS", True)
    flags = launcher._detach()
    assert flags["creationflags"] == (launcher.DETACHED_PROCESS |
                                      launcher.CREATE_NO_WINDOW)
    assert "start_new_session" not in flags


def test_posix_detaches_with_a_new_session(monkeypatch):
    # Passing Windows creation flags to Popen on POSIX raises, and without
    # anything in their place the daemon takes a hangup when the browser's
    # native-host pipe closes.
    monkeypatch.setattr(launcher, "WINDOWS", False)
    assert launcher._detach() == {"start_new_session": True}


def test_the_windowless_interpreter_is_a_windows_idea_only(monkeypatch):
    monkeypatch.setattr(launcher, "WINDOWS", False)
    assert launcher._interpreter() == sys.executable


# ----------------------------------------------------------- registration

def test_chromium_browsers_are_allowed_by_extension_origin():
    body = register.manifest("Chrome", "/x/launcher.sh", extension_id="abc123")
    assert body["allowed_origins"] == ["chrome-extension://abc123/"]
    assert "allowed_extensions" not in body


def test_firefox_is_allowed_by_add_on_id_instead():
    # Firefox is the one browser that spells this differently; getting it
    # wrong shows up only as "host not found", with nothing to go on.
    body = register.manifest("Firefox", "/x/launcher.sh", gecko_id="dj@example")
    assert body["allowed_extensions"] == ["dj@example"]
    assert "allowed_origins" not in body


def test_each_platform_has_its_own_manifest_directories():
    mac = register.target_dirs("darwin", "/home/me")
    linux = register.target_dirs("linux", "/home/me")
    assert "Library" in mac["Chrome"] and ".config" in linux["Chrome"]
    assert mac["Chrome"] != linux["Chrome"]
    # Firefox does not keep its hosts where the Chromium browsers do.
    assert "Mozilla" in mac["Firefox"] and ".mozilla" in linux["Firefox"]


def test_windows_registers_through_the_registry_not_a_directory():
    assert register.target_dirs("win32", "C:/Users/me") == {}
    assert "Firefox" in register.WINDOWS_KEYS


def test_every_platform_covers_the_same_browsers():
    assert set(register.MAC_DIRS) == set(register.LINUX_DIRS)
    assert set(register.WINDOWS_KEYS) <= set(register.MAC_DIRS)


def test_the_manifest_is_written_without_a_byte_order_mark(tmp_path):
    # Chrome rejects a host manifest carrying a BOM, and reports it as the
    # host not existing -- an hour of debugging the wrong thing.
    path = str(tmp_path / "nested" / "com.music_dj.launcher.json")
    register.write_manifest(path, register.manifest("Chrome", "/x/launcher"))
    raw = open(path, "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8"))["type"] == "stdio"
