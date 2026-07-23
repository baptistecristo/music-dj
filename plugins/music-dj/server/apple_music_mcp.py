#!/usr/bin/env python3
"""Dependency-free MCP stdio server exposing Apple Music control tools.

This is what makes music-dj usable from any Claude interface: the Claude
desktop app (and Claude Code) launch it locally, and Claude can call these
tools directly — no hooks required. Newline-delimited JSON-RPC 2.0 over stdio.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import musicdj  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
# Keep in sync with .claude-plugin/plugin.json and SKILL.md metadata.
VERSION = "0.6.0"

TOOLS = [
    {
        "name": "set_mood",
        "description": (
            "Switch Apple Music to the playlist configured for a mood. "
            "Moods: coding, writing, debugging, building, research. Call this "
            "when the kind of work changes (e.g. starting to code, tests "
            "failing, doing research, writing docs)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mood": {"type": "string", "enum": musicdj.MOODS},
                "force": {
                    "type": "boolean",
                    "description": "Bypass debounce and switch immediately (default true for explicit calls)"
                }
            },
            "required": ["mood"]
        }
    },
    {
        "name": "play_playlist",
        "description": "Shuffle-play a specific Apple Music playlist by name.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]
        }
    },
    {
        "name": "list_playlists",
        "description": "List the user's Apple Music playlists.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "now_playing",
        "description": "Get the currently playing track and artist.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "pause_music",
        "description": "Pause Apple Music playback.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "resume_music",
        "description": "Resume Apple Music playback.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_dj_status",
        "description": "Get music-dj config (mood -> playlist mapping) and current state.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "configure_dj",
        "description": (
            "Update music-dj settings. Map a mood to a playlist, enable/disable "
            "the auto-DJ, or tune behavior."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mood": {"type": "string", "enum": musicdj.MOODS},
                "playlist": {"type": "string", "description": "Playlist name to assign to the mood"},
                "service": {"type": "string", "description": "Music service id (apple-music, spotify, soundcloud, youtube-music, deezer, tidal, amazon-music, qobuz, bandcamp, pandora)"},
                "browser": {"type": "string", "description": "Preferred browser for the web player (chrome, edge, brave, arc, opera, vivaldi). Empty string means ask each time."},
                "browser_device_id": {"type": "string", "description": "deviceId of the chosen browser, from list_connected_browsers"},
                "enabled": {"type": "boolean"},
                "shuffle": {"type": "boolean"},
                "min_seconds_between_switches": {"type": "integer"},
                "confirmations_needed": {"type": "integer", "description": "Signal weight a new mood must accumulate (with decay) before switching: strong signals like edits or test runs count 1.0, failures 1.25, doc-glances 0.5. Default 2 ~= two solid observations. Urgent moods need only 1"},
                "urgent_moods": {"type": "array", "items": {"type": "string", "enum": musicdj.MOODS}, "description": "Moods that switch fast (single observation, half the debounce window). Default: [\"debugging\"]"},
                "pause_on_session_end": {"type": "boolean"},
                "session_start_mood": {"type": "string"},
                "launch_music_if_closed": {"type": "boolean"}
            }
        }
    }
]


def call_tool(name, args):
    if name == "set_mood":
        force = args.get("force", True)
        ok, msg = musicdj.maybe_switch(args["mood"], force=force)
        return msg
    if name == "play_playlist":
        ok, msg = musicdj.play_playlist(args["name"])
        return msg if not ok else "now playing playlist: %s" % args["name"]
    if name == "list_playlists":
        ok, msg = musicdj.list_playlists()
        return msg or "(no playlists found)"
    if name == "now_playing":
        ok, msg = musicdj.now_playing()
        return msg
    if name == "pause_music":
        ok, msg = musicdj.pause()
        return "paused" if ok else msg
    if name == "resume_music":
        ok, msg = musicdj.resume()
        return "playing" if ok else msg
    if name == "get_dj_status":
        return json.dumps({
            "config": musicdj.load_config(),
            "state": musicdj.load_state(),
            "music_app_running": musicdj.music_running() if musicdj.is_macos() else False,
            "platform_ok": musicdj.is_macos()
        }, indent=2)
    if name == "configure_dj":
        cfg = musicdj.load_config()
        if args.get("mood") and args.get("playlist"):
            cfg["playlists"][args["mood"]] = args["playlist"]
        # Switching browsers invalidates the remembered deviceId — it points at
        # the old browser. Drop it unless this same call supplies a new one.
        if ("browser" in args and args["browser"] != cfg.get("browser")
                and "browser_device_id" not in args):
            cfg["browser_device_id"] = ""
        for key in ("service", "browser", "browser_device_id", "enabled", "shuffle",
                    "min_seconds_between_switches",
                    "confirmations_needed", "urgent_moods",
                    "pause_on_session_end", "session_start_mood",
                    "launch_music_if_closed"):
            if key in args:
                cfg[key] = args[key]
        musicdj.save_config(cfg)
        return "updated config:\n" + json.dumps(cfg, indent=2)
    raise ValueError("unknown tool: %s" % name)


def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    # JSON-RPC over stdio is UTF-8. On Windows, Python defaults pipes to the
    # legacy locale codepage, which corrupts accented playlist/track names in
    # both directions — force UTF-8 (and tolerate a BOM on input).
    try:
        sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if msg_id is None:
            continue  # notification, nothing to answer

        if method == "initialize":
            # Per the MCP negotiation contract: echo the client's requested
            # protocol version when it's one we can serve, otherwise answer
            # with the latest version we support.
            asked = (msg.get("params") or {}).get("protocolVersion")
            proto = asked if isinstance(asked, str) and asked else PROTOCOL_VERSION
            reply(msg_id, {
                "protocolVersion": proto,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "apple-music", "version": VERSION}
            })
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                text = call_tool(params.get("name", ""),
                                 params.get("arguments") or {})
                reply(msg_id, {"content": [{"type": "text", "text": str(text)}]})
            except Exception as e:
                reply(msg_id, {
                    "content": [{"type": "text", "text": "error: %s" % e}],
                    "isError": True
                })
        elif method == "ping":
            reply(msg_id, {})
        else:
            reply(msg_id, error={"code": -32601,
                                 "message": "method not found: %s" % method})


if __name__ == "__main__":
    main()
