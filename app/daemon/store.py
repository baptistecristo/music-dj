"""Storage for ~/.music-dj.

Two rules, both learned the hard way on Windows:

- Read with utf-8-sig. PowerShell writes a BOM and json.load chokes on it.
  The existing plugin's musicdj.py already does this; we match it so the same
  files stay readable from both sides.
- Write atomically. The plugin's hook writes state.json while we're reading it,
  and a half-written file is worse than a stale one.
"""

import json
import os
import tempfile

DIR = os.path.expanduser("~/.music-dj")

# Owned by the plugin -- we read these, and only ever merge into config.
CONFIG = "config.json"
STATE = "state.json"
PROFILE = "taste-profile.md"

# Ours.
RATINGS = "ratings.json"
HISTORY = "history.json"
QUEUE = "queue.json"


def path(name):
    return os.path.join(DIR, name)


def read_json(name, default=None):
    """Never raises: a missing or corrupt file reads as the default."""
    try:
        with open(path(name), encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def read_text(name, default=""):
    try:
        with open(path(name), encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return default


def write_json(name, data):
    """Temp file in the same directory, then os.replace -- atomic on Windows."""
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".tmp-" + name + "-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path(name))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mtime(name):
    try:
        return os.path.getmtime(path(name))
    except OSError:
        return 0.0


def merge_config(updates):
    """Read-modify-write config.json, preserving keys the plugin owns."""
    cfg = read_json(CONFIG, {})
    cfg.update(updates)
    write_json(CONFIG, cfg)
    return cfg
