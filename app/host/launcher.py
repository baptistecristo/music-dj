"""Native messaging host: the browser's way to start the DJ.

Registered by register.py. When the toolbar icon is clicked with nothing
running, the browser spawns this with one framed JSON message on stdin; it
starts the daemon and the overlay exactly like start.cmd (or start.sh) does,
replies, and exits. Nothing stays resident.
"""

import json
import os
import socket
import struct
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8787

WINDOWS = sys.platform == "win32"

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def read_message(stream):
    """One native-messaging frame: 4-byte little-endian length, then JSON."""
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (length,) = struct.unpack("<I", raw)
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream, msg):
    data = json.dumps(msg).encode("utf-8")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()


def daemon_running(port=PORT):
    """A quick TCP probe. Double-clicks and click-while-starting races must
    not stack a second daemon on the port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _interpreter():
    """The python to launch with.

    On Windows that is pythonw, sitting next to whichever python is running
    us, so the daemon and overlay do not flash a console. Nothing else has a
    windowed interpreter, and nothing else needs one.
    """
    if WINDOWS:
        cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return sys.executable


def _detach():
    """Whatever this platform needs to outlive the browser that spawned us.

    Windows takes creation flags; POSIX has none of those, and passing them
    raises. There the equivalent is a new session, so the daemon does not
    take a hangup when the browser or its native-host pipe goes away.
    """
    if WINDOWS:
        return {"creationflags": DETACHED_PROCESS | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def spawn(module):
    subprocess.Popen(
        [_interpreter(), "-m", module], cwd=APP_DIR,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True, **_detach())


def handle(msg):
    if (msg or {}).get("cmd") != "start":
        return {"ok": False, "error": "unknown command"}
    if daemon_running():
        return {"ok": True, "already": True}
    spawn("daemon.main")
    spawn("overlay.app")
    return {"ok": True, "already": False}


def main():
    write_message(sys.stdout.buffer, handle(read_message(sys.stdin.buffer)))


if __name__ == "__main__":
    main()
