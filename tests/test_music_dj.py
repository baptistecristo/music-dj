#!/usr/bin/env python3
"""Tests for the music-dj plugin (run on Linux with mocked AppleScript)."""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "music-dj")
sys.path.insert(0, os.path.join(ROOT, "lib"))

tmp = tempfile.mkdtemp()
# Isolate config. HOME covers POSIX; on Windows os.path.expanduser("~") reads
# USERPROFILE and ignores HOME, so set both or subprocesses (the MCP server)
# will write to the developer's REAL ~/.music-dj.
os.environ["HOME"] = tmp
os.environ["USERPROFILE"] = tmp

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
check("bash bun test fail -> debugging",
      c("Bash", {"command": "bun test"}, {"stdout": "3 failing"}) == "debugging")
check("bash npm error -> debugging",
      c("Bash", {"command": "npm run build"}, {"stderr": "npm error ELIFECYCLE"}) == "debugging")
check("bash cargo panic -> debugging",
      c("Bash", {"command": "cargo test"}, {"stderr": "thread 'main' panicked at src/lib.rs"}) == "debugging")
check("read code -> None (no false research)", c("Read", {"file_path": "src/app.ts"}, None) is None)
check("read docs -> research", c("Read", {"file_path": "notes/design.md"}, None) == "research")
check("grep -> None", c("Grep", {"pattern": "foo"}, None) is None)
check("agent tool -> research", c("Agent", {}, None) == "research")

p = musicdj.classify_prompt
check("prompt bug -> debugging", p("there's a bug in the login flow") == "debugging")
check("prompt blog -> writing", p("draft a blog post about rust") == "writing")
check("prompt research -> research", p("research the best sqlite orm") == "research")
check("prompt neutral -> None", p("hello") is None)
check("prompt fr plante -> debugging", p("l'app plante au démarrage") == "debugging")
check("prompt fr marche pas -> debugging", p("le login ne marche pas") == "debugging")
check("prompt fr corrige -> debugging", p("corrige l'erreur d'import") == "debugging")
check("prompt fr rédige -> writing", p("rédige un rapport pour l'équipe") == "writing")
check("prompt deploy -> building", p("deploy the new version to prod") == "building")
check("prompt fr déploie -> building", p("déploie ça en prod") == "building")
check("prompt implement -> coding", p("implement the retry logic") == "coding")
check("prompt fr ajoute -> coding", p("ajoute un bouton de partage") == "coding")
check("prompt 'write a test' not writing", p("write a test for the parser") != "writing")

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
# debugging is urgent by default: one observation is enough
sw, msg = musicdj.maybe_switch("debugging")
check("urgent mood switches on 1st observation", sw and played[-1] == "Calm", msg)
check("state updated", musicdj.load_state()["current_mood"] == "debugging")

# non-urgent moods still need two confirmations
st = musicdj.load_state()
st["last_switch"] = 0
musicdj.save_state(st)
sw, msg = musicdj.maybe_switch("writing")
check("1st confirmation pending", not sw and "pending" in msg, msg)
sw, msg = musicdj.maybe_switch("writing")
check("2nd confirmation switches", sw and played[-1] == "Mellow", msg)

# urgent mood gets half the debounce window
st = musicdj.load_state()
st["last_switch"] = time.time() - 200   # 200s ago: > 150 (urgent), < 300 (normal)
musicdj.save_state(st)
sw, msg = musicdj.maybe_switch("coding")
check("normal mood still debounced at 200s", not sw, msg)
sw, msg = musicdj.maybe_switch("debugging")
check("urgent mood allowed at 200s", sw and played[-1] == "Calm", msg)

sw, msg = musicdj.maybe_switch("nonsense")
check("unknown mood rejected", not sw)

# disabled
cfg["enabled"] = False
musicdj.save_config(cfg)
sw, msg = musicdj.maybe_switch("coding", force=True)
check("disabled blocks", not sw and len(played) == 4, msg)
cfg["enabled"] = True
musicdj.save_config(cfg)

# --- hook script end-to-end (subprocess, non-mac so should silently no-op) ---
r = subprocess.run(
    [sys.executable, os.path.join(ROOT, "hooks", "scripts", "dj_hook.py"), "posttool"],
    input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x.py"}}),
    capture_output=True, text=True, timeout=15, env=dict(os.environ))
check("hook exits 0, silent", r.returncode == 0 and r.stdout == "", r.stderr[:200])

# BOM-prefixed UTF-8 stdin with an accented French prompt (what Windows pipes
# can deliver) must still parse, classify, and emit the mood marker.
musicdj.save_state({"last_switch": 0})
r = subprocess.run(
    [sys.executable, os.path.join(ROOT, "hooks", "scripts", "dj_hook.py"), "prompt"],
    input="﻿" + json.dumps({"prompt": "corrige le bug, ça échoue encore"},
                                ensure_ascii=False),
    capture_output=True, text=True, encoding="utf-8", timeout=15,
    env=dict(os.environ))
check("hook survives BOM + accents, emits marker",
      r.returncode == 0 and "debugging" in r.stdout
      and "additionalContext" in r.stdout,
      (r.stdout + r.stderr)[:200])

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
np_text = np["content"][0]["text"]
if sys.platform == "darwin":
    # On a real Mac osascript actually runs, so the off-mac refusal string is
    # never produced. Music.app is absent on a CI runner, so the honest
    # assertion is that we get a string back instead of an exception.
    check("mcp: now_playing responds on macOS", isinstance(np_text, str) and np_text, np)
else:
    check("mcp: now_playing graceful off-mac", "macOS" in np_text, np)

# --- hooks.json schema shape (regression: loader requires top-level "hooks" key) ---
hj = json.load(open(os.path.join(ROOT, "hooks", "hooks.json")))
check("hooks.json has top-level hooks record", isinstance(hj.get("hooks"), dict))
check("hooks.json events present", {"PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"} <= set(hj["hooks"]))
cmds = [h["command"] for ev in hj["hooks"].values() for m in ev for h in m["hooks"]]
check("hooks fall back to 'python' for Windows",
      all("python3" in c and "|| python " in c for c in cmds), cmds)

print()
if failures:
    print("FAILURES: %d" % len(failures))
    sys.exit(1)
print("ALL TESTS PASSED")
