# music-dj (plugin)

The plugin behind the music-dj repo — see the repo root README for install
and usage. This file documents the plugin internals.

## Components

- `skills/music-dj/SKILL.md` — the DJ brain: mode selection, mood-from-
  writing inference, setup flow, etiquette.
- `skills/music-dj/references/` — per-service control guides (Apple Music
  with tested MusicKit snippets; Spotify browser + Web API; SoundCloud;
  YouTube Music; Deezer/Tidal/Amazon/Qobuz/Bandcamp/Pandora) and the
  taste-profiling template.
- `hooks/` — PostToolUse / UserPromptSubmit / SessionStart / SessionEnd
  classification of activity into moods (coding, writing, debugging,
  building, research). Signals are weighted (failures shout, doc-glances
  whisper) and accumulate as decaying per-mood scores, so switching is
  debounced and sticky without a stray glance derailing it. On macOS switches Apple Music
  natively; elsewhere emits a `[music-dj]` context marker prompting Claude
  to switch via the browser.
- `server/apple_music_mcp.py` — dependency-free MCP stdio server: config
  management everywhere (`get_dj_status`, `configure_dj`); native Apple
  Music playback tools on macOS (`set_mood`, `play_playlist`,
  `list_playlists`, `now_playing`, `pause_music`, `resume_music`).
- `lib/musicdj.py` — shared config/state, activity classifier, debounce
  logic, AppleScript control.
- `.mcp.json` — launches the MCP server with `${MUSIC_DJ_PYTHON:-python3}`.
  MCP commands are spawned without a shell, so no `python3 || python`
  fallback is possible here (hooks do use that trick). `python3` is right on
  macOS/Linux and Microsoft-Store Python; on a python.org Windows install
  there is no `python3.exe`, so the installer sets the `MUSIC_DJ_PYTHON`
  user environment variable to the real interpreter path instead.

## User-machine files (never in the repo)

- `~/.music-dj/config.json` — service choice + tuning
  (`min_seconds_between_switches`, `confirmations_needed`, macOS playlist
  mappings, etc.). Created with defaults on first hook run.
- `~/.music-dj/taste-profile.md` — the learned taste profile.
- `~/.music-dj/spotify.json` — optional Spotify API credentials.

## Development

Run the test suite from the repo root:

```bash
python3 tests/test_music_dj.py
```

Covers the classifier, debounce/sticky switching, hook silence guarantees,
and the MCP server handshake — all runnable off-macOS (AppleScript calls
degrade gracefully).
