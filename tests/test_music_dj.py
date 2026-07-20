#!/usr/bin/env python3
"""Tests for the music-dj plugin (run on Linux with mocked AppleScript)."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "music-dj")
sys.path.insert(0, os.path.join(ROOT, "lib"))

tmp = tempfile.mkdtemp()
os.environ["HOME"] = tmp  # isolate config

import musicdj  # noqa: E402

# repoint paths after HOME change
musicdj.CONFIG_DIR = os.path.join(tmp, ".music-dj")
musicdj.CONFIG_PATH = os.path.join(musicdj.CONFIG_DIR, "config.json")
musicdj.STATE_PATH = os.path.join(musicdj.CONFIG_DIR, "state.json")

failures = []


def check(name, cond, extra=""):
    if cond:
        print("PASS  " + name)
    else:
        print("FAIL  " + name + (" -- " + str(extra) if extra else ""))
        failures.append(name)


# --- classifier ---
c = musicdj.classify_tool
check("edit .py -> coding", c("Edit", {"file_path": "a/b.py"}, None) == "coding")
check("write .md -> writing", c("Write", {"file_path": "README.md"}, None) == "writing")
check("websearch -> research", c("WebSearch", {}, None) == "research")
check("bash pytest pass -> building",
      c("Bash", {"command": "pytest tests/"}, {"stdout": "12 passed"}) == "building")
check("bash pytest fail -> debugging",
      c("Bash", {"command": "pytest tests/"}, {"stdout": "2 FAILED, 1 passed"}) == "debugging")
check("bash traceback -> debugging",
      c("Bash", {"command": "python app.py"},
        {"stderr": "Traceback (most recent call last):"}) == "debugging")
check("bash ls -> None", c("Bash", {"command": "ls -la"}, {"stdout": "files"}) is None)
check("bash git push -> building",
      c("Bash", {"command": "git push origin main"}, {"stdout": "done"}) == "building")

p = musicdj.classify_prompt
check("prompt bug -> debugging", p("there's a bug in the login flow") == "debugging")
check("prompt blog -> writing", p("draft a blog post about rust") == "writing")
check("prompt research -> research", p("research the best sqlite orm") == "research")
check("prompt neutral -> None", p("hello") is None)

# --- debounce / sticky switching (mock playback) ---
played = []
musicdj.play_playlist = lambda name, cfg=None: (played.append(name) or (True, "ok"))

cfg = musicdj.load_config()  # creates defaults
check("default config created", os.path.exists(musicdj.CONFIG_PATH))
check("5 moods mapped", set(cfg["playlists"]) == set(musicdj.MOODS))

sw, msg = musicdj.maybe_switch("coding", force=True)
check("forced switch plays", sw and played == ["Deep Focus"], msg)
sw, msg = musicdj.maybe_switch("coding")
check("same mood no-op", not sw and len(played) == 1, msg)
sw, msg = musicdj.maybe_switch("debugging")
check("debounce blocks fast switch", not sw and len(played) == 1, msg)

# age the last switch beyond the window
st = musicdj.load_state()
st["last_switch"] = 0
musicdj.save_state(st)
sw, msg = musicdj.maybe_switch("debugging")
check("1st confirmation pending", not sw and "pending" in msg, msg)
sw, msg = musicdj.maybe_switch("debugging")
check("2nd confirmation switches", sw and played[-1] == "Calm", msg)
check("state updated", musicdj.load_state()["current_mood"] == "debugging")

sw, msg = musicdj.maybe_switch("nonsense")
check("unknown mood rejected", not sw)

# disabled
cfg["enabled"] = False
musicdj.save_config(cfg)
sw, msg = musicdj.maybe_switch("coding", force=True)
check("disabled blocks", not sw and len(played) == 2, msg)
cfg["enabled"] = True
musicdj.save_config(cfg)

# --- hook script end-to-end (subprocess, non-mac so should silently no-op) ---
r = subprocess.run(
    [sys.executable, os.path.join(ROOT, "hooks", "scripts", "dj_hook.py"), "posttool"],
    input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x.py"}}),
    capture_output=True, text=True, timeout=15, env=dict(os.environ))
check("hook exits 0, silent", r.returncode == 0 and r.stdout == "", r.stderr[:200])

# --- MCP server handshake ---
msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "get_dj_status", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "configure_dj",
                "arguments": {"mood": "coding", "playlist": "Test List"}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
     "params": {"name": "now_playing", "arguments": {}}},
]
inp = "\n".join(json.dumps(m) for m in msgs) + "\n"
r = subprocess.run([sys.executable, os.path.join(ROOT, "server", "apple_music_mcp.py")],
                   input=inp, capture_output=True, text=True, timeout=20,
                   env=dict(os.environ))
lines = [json.loads(l) for l in r.stdout.strip().splitlines() if l.strip()]
by_id = {m.get("id"): m for m in lines}
check("mcp: 5 responses", len(by_id) == 5, r.stdout[:300] + r.stderr[:300])
check("mcp: initialize ok",
      by_id.get(1, {}).get("result", {}).get("serverInfo", {}).get("name") == "apple-music")
tools = [t["name"] for t in by_id.get(2, {}).get("result", {}).get("tools", [])]
check("mcp: 8 tools listed", len(tools) == 8, tools)
status = json.loads(by_id.get(3, {}).get("result", {})["content"][0]["text"])
check("mcp: status has config", "playlists" in status.get("config", {}))
check("mcp: configure_dj applied",
      "Test List" in by_id.get(4, {}).get("result", {})["content"][0]["text"])
check("mcp: configure persisted",
      musicdj.load_config()["playlists"]["coding"] == "Test List")
np = by_id.get(5, {}).get("result", {})
check("mcp: now_playing graceful off-mac", "macOS" in np["content"][0]["text"], np)

# --- hooks.json schema shape (regression: loader requires top-level "hooks" key) ---
hj = json.load(open(os.path.join(ROOT, "hooks", "hooks.json")))
check("hooks.json has top-level hooks record", isinstance(hj.get("hooks"), dict))
check("hooks.json events present", {"PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"} <= set(hj["hooks"]))

print()
if failures:
    print("FAILURES: %d" % len(failures))
    sys.exit(1)
print("ALL TESTS PASSED")
