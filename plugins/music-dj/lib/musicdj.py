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
    # Which browser the web player is driven in: chrome | edge | brave |
    # arc | opera | vivaldi | "" (ask each time). This is a preference, not a
    # guarantee — the DJ can only use a browser that has the Claude extension
    # installed and connected.
    "browser": "",
    # deviceId of the browser picked last time, so later sessions can reselect
    # it without asking again. Cleared by changing "browser".
    "browser_device_id": "",
    "shuffle": True,
    # Don't switch playlists more often than this (seconds).
    "min_seconds_between_switches": 300,
    # A new mood must accumulate this much signal weight before switching.
    # Strong signals (edits, searches, test runs) count 1.0, weak ones
    # (glancing at a doc) less, failures a bit more — so two solid
    # observations switch, but a stray glance never does.
    "confirmations_needed": 2,
    # Mood evidence fades with this half-life (seconds), so a burst of
    # research an hour ago doesn't outvote what's happening now.
    "score_half_life_seconds": 300,
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


def _write_json_atomic(path, obj, indent=None):
    # Hooks run as one process per tool call and Claude Code issues tool calls
    # in parallel, so writers race each other and readers can catch a write
    # mid-flight. Write to a per-process temp file and os.replace() it in:
    # readers then see either the old or the new file, never a torn one.
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        _write_json_atomic(CONFIG_PATH, cfg, indent=2)
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
        _write_json_atomic(STATE_PATH, st)
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
_LINT_RE = re.compile(
    r"\b(ruff|eslint|flake8|pylint|mypy|pyright|cargo clippy|golangci-lint|"
    r"rubocop|shellcheck|prettier --check|black --check|biome (check|lint))\b")
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
    r"\b(bug|bogue|debug|broken|error|erreur|crash|plante|"
    r"failing|regression|régression|doesn'?t work|not working|"
    r"stopped working|marche (pas|plus)|fonctionne (pas|plus)|"
    r"échoue|stack ?trace|traceback)\b", re.I)
# "fix"-type verbs alone are weak evidence: "fix the docs" is a writing task.
# They only tip the scale next to a real failure word or with no competition.
_PROMPT_DEBUG_HINT_RE = re.compile(
    r"\b(fix(e[sd])?|corrige[rz]?|répare[rz]?)\b", re.I)
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


# Signal weights: how much one observation counts toward a mood switch
# (confirmations_needed is the target). Failures shout, doc-glances whisper.
WEIGHT_STRONG = 1.0
WEIGHT_FAILURE = 1.25
WEIGHT_GLANCE = 0.5
PROMPT_WEIGHT = 1.5   # the user saying it beats us inferring it
# How much inertia the mood we're already in gets. Capped at one solid
# observation: without a ceiling its score climbs for as long as the mood
# holds, and an hour into a session nothing could ever outscore it.
STICKINESS_CAP = WEIGHT_STRONG


def classify_tool_signal(tool_name, tool_input, tool_response):
    """Map a tool call to (mood, weight), or None if it carries no signal."""
    resp_text = ""
    if tool_response is not None:
        try:
            resp_text = json.dumps(tool_response)[:20000]
        except Exception:
            resp_text = str(tool_response)[:20000]

    if tool_name == "Bash":
        cmd = (tool_input or {}).get("command", "") or ""
        failed = bool(_FAIL_RE.search(resp_text))
        if _TEST_RE.search(cmd) or _BUILD_RE.search(cmd) or _LINT_RE.search(cmd):
            return ("debugging", WEIGHT_FAILURE) if failed \
                else ("building", WEIGHT_STRONG)
        if failed:
            return ("debugging", WEIGHT_FAILURE)
        return None

    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        ext = _ext_of(tool_input)
        if ext in _DOC_EXTS:
            return ("writing", WEIGHT_STRONG)
        return ("coding", WEIGHT_STRONG)

    if tool_name == "Read":
        # Glancing at docs is weak research evidence; reading code is just
        # part of whatever the user is already doing — no signal (it used to
        # count as research, which dragged every coding session toward
        # ambient).
        if _ext_of(tool_input) in _DOC_EXTS:
            return ("research", WEIGHT_GLANCE)
        return None

    if tool_name in _RESEARCH_TOOLS:
        return ("research", WEIGHT_STRONG)

    return None


def classify_tool(tool_name, tool_input, tool_response):
    """Map a tool call to a mood, or None if it carries no signal."""
    sig = classify_tool_signal(tool_name, tool_input, tool_response)
    return sig[0] if sig else None


_PROMPT_SCORED_RES = [
    ("debugging", _PROMPT_DEBUG_RE, 2),
    ("debugging", _PROMPT_DEBUG_HINT_RE, 1),
    ("building", _PROMPT_SHIP_RE, 2),
    ("writing", _PROMPT_WRITE_RE, 2),
    ("research", _PROMPT_RESEARCH_RE, 2),
    ("coding", _PROMPT_CODE_RE, 2),
]

# Tie-break order when scores are level (urgency first).
_PROMPT_TIE_ORDER = ("debugging", "building", "writing", "research", "coding")


def classify_prompt(text):
    """Map a user prompt to a mood, or None. Understands English and French.

    All mood keyword sets are scored and the best one wins, instead of
    first-match-wins — so "fix the docs page" reads as writing (docs beats
    the lone fix-verb), while "fix the crash" still reads as debugging.
    """
    text = text or ""
    scores = {}
    for mood, rx, weight in _PROMPT_SCORED_RES:
        n = len(rx.findall(text))
        if n:
            scores[mood] = scores.get(mood, 0) + n * weight
    if not scores:
        return None
    best = max(scores.values())
    for mood in _PROMPT_TIE_ORDER:
        if scores.get(mood) == best:
            return mood
    return None


# ------------------------------------------------------------------ switching

def _decayed_scores(st, now, half_life):
    """Per-mood evidence scores, decayed for the time since the last signal."""
    scores = {m: float(v) for m, v in (st.get("mood_scores") or {}).items()}
    elapsed = max(0.0, now - float(st.get("last_signal") or now))
    if scores and elapsed > 0 and half_life > 0:
        factor = 0.5 ** (elapsed / half_life)
        scores = {m: v * factor for m, v in scores.items() if v * factor >= 0.05}
    return scores


def decide_switch(mood, force=False, weight=1.0):
    """Debounced, sticky mood-switch decision (no playback). When it returns
    True the state is updated to the new mood. Returns (should_switch, msg).

    Evidence accumulates as decaying per-mood scores rather than a
    consecutive-observation counter, so a mixed workflow (edit, glance at a
    doc, edit again) still converges on its dominant mood instead of each
    signal resetting the last one's progress.
    """
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
        half_life = float(cfg.get("score_half_life_seconds", 300))
        scores = _decayed_scores(st, now, half_life)
        if st.get("current_mood") == mood:
            # Reinforce the current mood so competitors must genuinely
            # dominate it, not just outlast a stale counter — but cap it, so
            # two solid observations of something else still win no matter
            # how long we've been here.
            scores[mood] = min(scores.get(mood, 0.0) + weight, STICKINESS_CAP)
            st.update({"mood_scores": scores, "last_signal": now})
            save_state(st)
            return False, "already in mood %s" % mood
        min_wait = float(cfg.get("min_seconds_between_switches", 300))
        if urgent:
            min_wait /= 2
        scores[mood] = scores.get(mood, 0.0) + weight
        st.update({"mood_scores": scores, "last_signal": now})
        if now - st.get("last_switch", 0) < min_wait:
            # Still refuse to switch, but bank the evidence: a mood that holds
            # through the window then takes effect as soon as it expires,
            # instead of starting its count from zero.
            save_state(st)
            return False, "switched too recently"
        need = 1.0 if urgent else float(cfg.get("confirmations_needed", 2))
        rival = max((v for m, v in scores.items() if m != mood), default=0.0)
        # 0.1 tolerance: confirmations arriving within ~a minute of each
        # other decay a hair below the exact threshold; near enough counts.
        if scores[mood] < need - 0.1 or (not urgent and scores[mood] <= rival):
            save_state(st)
            return False, "mood %s pending (%.1f/%.1f)" % (mood, scores[mood], need)

    st.update({"current_mood": mood, "last_switch": now, "mood_scores": {},
               "last_signal": now})
    # Drop leftovers from the old consecutive-counter design so stale state
    # files stop carrying keys nothing reads.
    st.pop("pending_mood", None)
    st.pop("pending_count", None)
    save_state(st)
    return True, "switch to mood %s" % mood


def maybe_switch(mood, force=False, weight=1.0):
    """Decide + act natively (macOS). Returns (switched, message)."""
    should, msg = decide_switch(mood, force=force, weight=weight)
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
