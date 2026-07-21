"""Shared library for the music-dj plugin.

Config + state management, Apple Music control via AppleScript (osascript),
and activity -> mood classification. Dependency-free (stdlib only) so it runs
on any Mac with the system python3.
"""
import json
import os
import re
import subprocess
import sys
import time

CONFIG_DIR = os.path.expanduser("~/.music-dj")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")

MOODS = ["coding", "writing", "debugging", "building", "research"]

DEFAULT_CONFIG = {
    "enabled": True,
    # Music service: apple-music | spotify | soundcloud | youtube-music |
    # deezer | tidal | amazon-music | qobuz | bandcamp | pandora
    "service": "apple-music",
    "shuffle": True,
    # Don't switch playlists more often than this (seconds).
    "min_seconds_between_switches": 300,
    # A new mood must be observed this many times in a row before switching
    # (prevents flapping when activity bounces around).
    "confirmations_needed": 2,
    # Moods that mean "things just went wrong": they switch on a single
    # observation and with half the debounce window, so the calm-down music
    # lands while it still matters.
    "urgent_moods": ["debugging"],
    # If the Music app isn't running, stay silent instead of launching it.
    "launch_music_if_closed": False,
    # Pause playback when a Claude session ends.
    "pause_on_session_end": False,
    # Mood to start when a new session begins ("" disables).
    "session_start_mood": "coding",
    # Mood -> Apple Music playlist name (must exist in your library).
    "playlists": {
        "coding": "Deep Focus",
        "writing": "Mellow",
        "debugging": "Calm",
        "building": "Momentum",
        "research": "Ambient"
    }
}


# ---------------------------------------------------------------- config/state

def load_config():
    # utf-8-sig so a BOM-prefixed config (PowerShell writes one on Windows)
    # still parses; it's a no-op when there's no BOM.
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
        save_config(DEFAULT_CONFIG)
    except Exception as e:
        # Unreadable but present: fall back to defaults in memory, but never
        # overwrite the file — that would erase the user's settings.
        print("music-dj: could not read %s (%s); using defaults" % (CONFIG_PATH, e),
              file=sys.stderr)
        cfg = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg or {})
    playlists = dict(DEFAULT_CONFIG["playlists"])
    playlists.update((cfg or {}).get("playlists", {}))
    merged["playlists"] = playlists
    return merged


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


# ---------------------------------------------------------------- Apple Music

def is_macos():
    return sys.platform == "darwin"


def music_running():
    try:
        r = subprocess.run(["pgrep", "-x", "Music"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def osascript(script, timeout=8):
    """Run an AppleScript. Returns (ok, output)."""
    if not is_macos():
        return False, "AppleScript is only available on macOS"
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return True, (r.stdout or "").strip()
        return False, (r.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def _esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def play_playlist(name, cfg=None):
    """Shuffle-play a playlist by name. Returns (ok, message)."""
    cfg = cfg or load_config()
    if not is_macos():
        return False, "Not on macOS"
    if not music_running() and not cfg.get("launch_music_if_closed", False):
        return False, "Music app is not running (set launch_music_if_closed to change this)"
    lines = ['tell application "Music"']
    if cfg.get("shuffle", True):
        lines.append("set shuffle enabled to true")
    lines.append('play playlist "%s"' % _esc(name))
    lines.append("end tell")
    return osascript("\n".join(lines))


def pause():
    return osascript('tell application "Music" to pause')


def resume():
    return osascript('tell application "Music" to play')


def now_playing():
    script = (
        'tell application "Music"\n'
        "if player state is playing then\n"
        'return (name of current track) & " - " & (artist of current track)\n'
        "else\n"
        'return "not playing"\n'
        "end if\n"
        "end tell"
    )
    return osascript(script)


def list_playlists():
    script = (
        'tell application "Music"\n'
        "set out to \"\"\n"
        "repeat with p in user playlists\n"
        'set out to out & (name of p) & linefeed\n'
        "end repeat\n"
        "return out\n"
        "end tell"
    )
    return osascript(script, timeout=15)


# ------------------------------------------------------------- classification

_TEST_RE = re.compile(
    r"\b(pytest|jest|vitest|mocha|rspec|go test|cargo (test|nextest)|"
    r"npm (run )?test|yarn test|pnpm test|bun test|deno test|phpunit|tox|"
    r"ctest|unittest|dotnet test|mvn test|gradlew? test|swift test|mix test|"
    r"rake test|playwright test|cypress run)\b")
_BUILD_RE = re.compile(
    r"\b(npm run build|yarn build|pnpm build|bun run build|cargo build|"
    r"go build|docker build|docker compose|xcodebuild|gradle|mvn|tsc\b|"
    r"vite build|webpack|next build|dotnet build|swift build|ninja|bazel|"
    r"cmake --build|mix compile|git push|git commit|deploy)\b"
    r"|(^|\s|&&\s*)make(\s|$)")
_FAIL_RE = re.compile(
    r"Traceback \(most recent call last\)|FAILED|npm ERR!|npm error|"
    r"error\[E|error:|Error:|error TS\d|AssertionError|assertion failed|"
    r"Segmentation fault|Exception in|Test Suites?: .*failed|\bFAIL\b|"
    r"panicked at|BUILD FAILED|Compilation (error|failed)|ELIFECYCLE|"
    r"UnhandledPromiseRejection|\b\d+ (failing|failed)\b")
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".cc", ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".sh", ".bash",
    ".css", ".scss", ".sql", ".vue", ".svelte", ".zig", ".lua", ".ex", ".exs",
    ".json", ".yaml", ".yml", ".toml"}
_DOC_EXTS = {".md", ".mdx", ".txt", ".rst", ".tex", ".adoc", ".html"}
_RESEARCH_TOOLS = {"WebSearch", "WebFetch", "Task", "Explore", "Agent"}
# Prompt classification is bilingual (English + French) and errs on the side
# of returning None: a wrong mood switch is worse than no switch.
_PROMPT_DEBUG_RE = re.compile(
    r"\b(bug|bogue|debug|broken|fix(e[sd])?|error|erreur|crash|plante|"
    r"failing|regression|régression|doesn'?t work|not working|"
    r"stopped working|marche (pas|plus)|fonctionne (pas|plus)|"
    r"corrige[rz]?|répare[rz]?|échoue|stack ?trace|traceback)\b", re.I)
_PROMPT_WRITE_RE = re.compile(
    r"\b(draft|blog|docs?|documentation|readme|essay|email|e-mail|article|"
    r"report|newsletter|changelog|release notes|rédige[rz]?|courriel|"
    r"rapport|billet)\b", re.I)
_PROMPT_RESEARCH_RE = re.compile(
    r"\b(research|investigate|look up|find out|compare|explore|search|"
    r"recherche[rz]?|renseigne[rz]?|investigue[rz]?|explorer?)\b", re.I)
_PROMPT_SHIP_RE = re.compile(
    r"\b(ship( it)?|deploy|déploie[rz]?|release|publish|publie[rz]?|"
    r"mets? en (prod|ligne))\b", re.I)
_PROMPT_CODE_RE = re.compile(
    r"\b(implement|implémente[rz]?|refactor|add (a |the )?"
    r"(feature|endpoint|button|test)|build (a|an|the|out)\b|"
    r"développe[rz]?|ajoute[rz]?|créé?e[rz]?)\b", re.I)


def _ext_of(tool_input):
    path = (tool_input or {}).get("file_path") or (tool_input or {}).get("path") or ""
    return os.path.splitext(path)[1].lower()


def classify_tool(tool_name, tool_input, tool_response):
    """Map a tool call to a mood, or None if it carries no signal."""
    resp_text = ""
    if tool_response is not None:
        try:
            resp_text = json.dumps(tool_response)[:20000]
        except Exception:
            resp_text = str(tool_response)[:20000]

    if tool_name == "Bash":
        cmd = (tool_input or {}).get("command", "") or ""
        if _TEST_RE.search(cmd):
            return "debugging" if _FAIL_RE.search(resp_text) else "building"
        if _BUILD_RE.search(cmd):
            return "debugging" if _FAIL_RE.search(resp_text) else "building"
        if _FAIL_RE.search(resp_text):
            return "debugging"
        return None

    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        ext = _ext_of(tool_input)
        if ext in _DOC_EXTS:
            return "writing"
        if ext in _CODE_EXTS or ext == "":
            return "coding"
        return "coding"

    if tool_name == "Read":
        # Reading docs is research; reading code is just part of whatever
        # the user is already doing — no signal (it used to count as
        # research, which dragged every coding session toward ambient).
        return "research" if _ext_of(tool_input) in _DOC_EXTS else None

    if tool_name in _RESEARCH_TOOLS:
        return "research"

    return None


def classify_prompt(text):
    """Map a user prompt to a mood, or None. Understands English and French."""
    text = text or ""
    if _PROMPT_DEBUG_RE.search(text):
        return "debugging"
    if _PROMPT_SHIP_RE.search(text):
        return "building"
    if _PROMPT_WRITE_RE.search(text):
        return "writing"
    if _PROMPT_RESEARCH_RE.search(text):
        return "research"
    if _PROMPT_CODE_RE.search(text):
        return "coding"
    return None


# ------------------------------------------------------------------ switching

def decide_switch(mood, force=False):
    """Debounced, sticky mood-switch decision (no playback). When it returns
    True the state is updated to the new mood. Returns (should_switch, msg)."""
    if mood not in MOODS:
        return False, "unknown mood: %s" % mood
    cfg = load_config()
    if not cfg.get("enabled", True):
        return False, "music-dj is disabled"

    st = load_state()
    now = time.time()

    # "Things just broke" moods react faster: half the debounce window and a
    # single observation is enough. Calm music 5 minutes after the failure
    # would miss the moment.
    urgent = mood in cfg.get("urgent_moods", DEFAULT_CONFIG["urgent_moods"])

    if not force:
        if st.get("current_mood") == mood:
            if st.get("pending_mood"):
                st["pending_mood"] = None
                st["pending_count"] = 0
                save_state(st)
            return False, "already in mood %s" % mood
        min_wait = float(cfg.get("min_seconds_between_switches", 300))
        if urgent:
            min_wait /= 2
        if now - st.get("last_switch", 0) < min_wait:
            return False, "switched too recently"
        need = 1 if urgent else int(cfg.get("confirmations_needed", 2))
        if need > 1:
            if st.get("pending_mood") == mood:
                st["pending_count"] = int(st.get("pending_count", 0)) + 1
            else:
                st["pending_mood"] = mood
                st["pending_count"] = 1
            if st["pending_count"] < need:
                save_state(st)
                return False, "mood %s pending (%d/%d)" % (mood, st["pending_count"], need)

    st.update({"current_mood": mood, "last_switch": now,
               "pending_mood": None, "pending_count": 0})
    save_state(st)
    return True, "switch to mood %s" % mood


def maybe_switch(mood, force=False):
    """Decide + act natively (macOS). Returns (switched, message)."""
    should, msg = decide_switch(mood, force=force)
    if not should:
        return False, msg
    cfg = load_config()
    playlist = cfg["playlists"].get(mood)
    if not playlist:
        return False, "no playlist configured for mood %s" % mood
    ok, pmsg = play_playlist(playlist, cfg)
    if ok:
        return True, "now shuffling %s (%s)" % (playlist, mood)
    return False, pmsg
