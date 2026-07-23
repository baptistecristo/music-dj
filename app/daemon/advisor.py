"""Asking Claude for the next batch.

Batched on purpose: one call per mood change or queue refill, not one per
track. Per-track would spawn a subprocess every three minutes all day and
stall playback between songs.

Every failure path here returns [] rather than raising. The caller reads that
as "use the profile instead". Claude makes the picks better; it must never be
the reason the music stops.
"""

import json
import logging
import shutil
import subprocess
import sys
import time

from . import library, picker, store

log = logging.getLogger("music-dj")

# The brief said 15s. Measured against the real CLI with the full profile, a
# batch takes ~17s, so 15 would have timed out every single time and quietly
# served profile picks forever. Nothing waits on this call -- the queue refills
# while a track is still playing -- so a generous ceiling costs nothing, and
# the fallback still catches a CLI that has genuinely hung.
TIMEOUT = 45
# Small batches on purpose: each refill is a fresh look at what they are
# doing, so the DJ re-decides every few songs instead of every ten.
BATCH = 6
RECENT_IN_PROMPT = 30
ADDITIONS_IN_PROMPT = 20


def build_prompt(profile, mood, lane, history, ratings,
                 signals=None, additions=None):
    """Everything Claude needs to pick well, and nothing else."""
    recent = []
    for play in (history or {}).get("plays", [])[:RECENT_IN_PROMPT]:
        title, artist = play.get("title"), play.get("artist")
        if title and artist:
            recent.append("%s — %s" % (title, artist))

    loved = library.rated_in_mood(ratings, mood, 5)
    hated = library.rated_in_mood(ratings, mood, 1)
    skipped = library.often_skipped(signals, mood)

    def names(entries):
        return [("%s — %s" % (e.get("title"), e.get("artist")))
                for e in entries if e.get("title")]

    parts = [
        "You are picking music for someone who is working. Their taste profile "
        "follows. Pick %d songs that fit the mood below." % BATCH,
        "",
        "## Their taste profile",
        profile or "(no profile available)",
        "",
        "## Right now",
        "They are %s. In the profile's terms, that is the '%s' direction."
        % (mood or "working", lane),
    ]

    if recent:
        parts += ["", "## Just played (do not repeat these)", "\n".join(recent)]

    # Ratings are per mood on purpose: one star while debugging says nothing
    # about a Friday night, so only this mood's verdicts are shown.
    if loved:
        parts += ["", "## They rated these 5 stars in this exact mood",
                  "\n".join(names(loved)),
                  "Lean towards this register. Do not simply replay them."]
    if hated:
        parts += ["", "## They rated these 1 star in this exact mood",
                  "\n".join(names(hated)), "Avoid anything like these here."]
    if skipped:
        parts += ["", "## They skip these early in this mood",
                  "\n".join(skipped),
                  "Read that as a no for this register, here. A song they "
                  "simply never rated is neutral, not disliked."]

    adds = [("%s — %s" % (a.get("name"), a.get("artist"))) if a.get("artist")
            else str(a.get("name"))
            for a in (additions or {}).get("items", [])[:ADDITIONS_IN_PROMPT]
            if a.get("name")]
    if adds:
        parts += ["", "## Recently added to their library",
                  "\n".join(adds),
                  "Fresh interest. Weave these in when they fit the mood, "
                  "rather than only replaying old favourites."]

    parts += [
        "",
        "## Answer with JSON only",
        'Exactly this shape, no prose around it, no markdown fence:',
        '{"picks":[{"title":"...","artist":"...","why":"..."}]}',
        "",
        "Each 'why' is one short line, max 60 characters, addressed to them. "
        "Say what actually motivated the pick — a link to their taste, the "
        "mood, or something they rated. Do not pad it out.",
        "Prefer things in or adjacent to their library, but not songs already "
        "saved in their own playlists -- they know those; bring them what "
        "they have not curated yet. Vary the artists: at most one song per "
        "artist.",
    ]
    return "\n".join(parts)


def extract_json(text):
    """Pull the object out of whatever the CLI wrote around it."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def ask_claude(prompt, timeout=TIMEOUT, runner=None):
    """Run `claude -p`. Returns raw stdout, or None if it could not be used."""
    runner = runner or _run_cli
    try:
        return runner(prompt, timeout)
    except FileNotFoundError:
        log.info("claude CLI not found; picking from the profile instead")
    except subprocess.TimeoutExpired:
        log.info("claude took longer than %ss; picking from the profile", timeout)
    except Exception:
        log.info("claude call failed; picking from the profile", exc_info=True)
    return None


def _run_cli(prompt, timeout):
    # The prompt goes over stdin, never as an argument. On Windows `claude` is
    # a .CMD shim, so it always runs through cmd.exe, which treats newlines as
    # command separators -- a multi-line prompt passed as argv arrives at the
    # model cut off at the first line break, and it answers a question you
    # never asked. stdin has no such problem.
    exe = shutil.which("claude")
    if not exe:
        raise FileNotFoundError("claude")
    proc = subprocess.Popen(
        [exe, "-p"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        stdout, _ = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        # subprocess.run's own cleanup is not enough here: on Windows the
        # .CMD shim means kill() only reaches cmd.exe, the node child keeps
        # the pipe handles open, and the final communicate() blocks forever
        # -- taking the refill lock with it. Kill the whole tree first.
        _kill_tree(proc)
        proc.communicate()
        raise
    if proc.returncode != 0:
        log.info("claude exited %s; picking from the profile", proc.returncode)
        return None
    return stdout


def _kill_tree(proc):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        proc.kill()                  # no shim on POSIX; this is the tree


def picks_for(mood, lane, *, seeds=None, history=None, ratings=None,
              profile=None, runner=None, rng=None, timeout=TIMEOUT):
    """Claude's picks for this mood, falling back to the profile.

    Always returns something playable as long as the profile has seeds.
    """
    profile = profile if profile is not None else store.read_text(store.PROFILE)
    history = history if history is not None else store.read_json(store.HISTORY, {})
    ratings = ratings if ratings is not None else store.read_json(store.RATINGS, {})
    signals = store.read_json(store.SIGNALS, {})
    additions = store.read_json(store.LIBRARY_RECENT, {})

    prompt = build_prompt(profile, mood, lane, history, ratings,
                          signals=signals, additions=additions)
    raw = ask_claude(prompt, timeout=timeout, runner=runner)
    picks = picker.validate_claude_picks(extract_json(raw)) if raw else []

    if picks:
        log.info("claude picked %d tracks for %s", len(picks), lane)
        return picks

    if raw:
        # Reached Claude but could not use the answer. Worth a line in the log,
        # not worth showing as an error: the music carries on either way.
        log.info("claude's answer was unusable; picking from the profile")

    recent_artists = [p.get("artist")
                      for p in (history or {}).get("plays", [])[:12]]
    return picker.profile_batch(seeds or {}, lane, count=BATCH,
                                avoid_artists=recent_artists, rng=rng)
