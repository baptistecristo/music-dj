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
        "(the 'DJ' web-player tab in the user's configured browser) or the "
        "user has asked for music this session, switch the music to match — "
        "see the music-dj skill. Otherwise ignore this silently." % mood)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": marker,
        }
    }))


def handle_mood(event, mood, weight=1.0, force=False):
    """Switch natively when we can, otherwise tell Claude to do it.

    decide_switch() is called exactly once: it owns the debounce and writes
    the new mood into state, so calling it twice would consume the switch and
    then report "already in mood X" on the second pass.
    """
    import musicdj
    should, _msg = musicdj.decide_switch(mood, force=force, weight=weight)
    if not should:
        return

    cfg = musicdj.load_config()
    # Native AppleScript only drives the Music app, so it only applies on
    # macOS *and* when Apple Music is the chosen service. A Mac user on
    # Spotify or YouTube Music is running the browser DJ like everyone else.
    if (musicdj.is_macos()
            and cfg.get("service", "apple-music") == "apple-music"):
        playlist = cfg.get("playlists", {}).get(mood)
        if playlist and musicdj.play_playlist(playlist, cfg)[0]:
            return
        # Native control was unavailable (Music app closed, or no playlist
        # mapped for this mood). Fall through rather than swallowing the
        # mood, so the browser DJ still gets told.

    emit_context(event, mood)


def main():
    try:
        import musicdj
    except Exception:
        return

    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        # The payload is UTF-8 JSON. On Windows, piped stdin defaults to the
        # legacy locale codepage (mangling accented text, so non-English mood
        # keywords never match) and may carry a BOM (which breaks json.load
        # entirely) — utf-8-sig handles both.
        sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")
    except Exception:
        pass
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    try:
        if event == "posttool":
            sig = musicdj.classify_tool_signal(
                data.get("tool_name", ""),
                data.get("tool_input") or {},
                data.get("tool_response"))
            if sig:
                handle_mood(event, sig[0], weight=sig[1])
        elif event == "prompt":
            mood = musicdj.classify_prompt(data.get("prompt", ""))
            if mood:
                handle_mood(event, mood, weight=musicdj.PROMPT_WEIGHT)
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
