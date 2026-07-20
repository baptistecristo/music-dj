#!/usr/bin/env python3
"""Hook entrypoint for the music-dj plugin.

Called by Claude Code / Cowork hooks with the event name as argv[1] and the
hook payload as JSON on stdin.

On macOS: switches Apple Music playlists natively via AppleScript.
Anywhere else (Windows, Linux, cloud): emits an additionalContext marker so
Claude can switch the music itself through the browser DJ (see the music-dj
skill). Must never block or fail the session: every path exits 0.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))

EVENT_NAMES = {
    "posttool": "PostToolUse",
    "prompt": "UserPromptSubmit",
    "sessionstart": "SessionStart",
}


def emit_context(event, mood):
    """Tell Claude (via hook additionalContext) that the mood changed."""
    hook_event = EVENT_NAMES.get(event)
    if not hook_event:
        return
    marker = (
        "[music-dj] Mood shift detected: %s. If the browser DJ is active "
        "(Apple Music 'DJ' tab via Claude in Chrome) or the user has asked "
        "for music this session, switch the music to match — see the "
        "music-dj skill. Otherwise ignore this silently." % mood)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": marker,
        }
    }))


def handle_mood(event, mood, force=False):
    import musicdj
    if musicdj.is_macos():
        musicdj.maybe_switch(mood, force=force)
        return
    should, _msg = musicdj.decide_switch(mood, force=force)
    if should:
        emit_context(event, mood)


def main():
    try:
        import musicdj
    except Exception:
        return

    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    try:
        if event == "posttool":
            mood = musicdj.classify_tool(
                data.get("tool_name", ""),
                data.get("tool_input") or {},
                data.get("tool_response"))
            if mood:
                handle_mood(event, mood)
        elif event == "prompt":
            mood = musicdj.classify_prompt(data.get("prompt", ""))
            if mood:
                handle_mood(event, mood)
        elif event == "sessionstart":
            cfg = musicdj.load_config()
            mood = cfg.get("session_start_mood") or ""
            if cfg.get("enabled", True) and mood:
                if musicdj.is_macos():
                    musicdj.maybe_switch(mood, force=True)
                # Non-mac: no context emitted on session start — starting
                # music unprompted every session would be intrusive.
        elif event == "sessionend":
            cfg = musicdj.load_config()
            if (cfg.get("enabled", True)
                    and cfg.get("pause_on_session_end", False)
                    and musicdj.is_macos()):
                musicdj.pause()
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
