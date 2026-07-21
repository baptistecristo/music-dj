"""Turning a mood into a batch of picks.

Milestone 2 ships the profile path only. Claude picking lands in milestone 4 and
slots in above this: when it works we use it, and when it doesn't we land here.
The profile path is therefore not a degraded mode -- it is the floor, and it has
to be good enough to listen to on its own.
"""

import random

BATCH_SIZE = 12


def pick_term(pick):
    """The string we hand to the extension's search."""
    parts = [pick.get("artist"), pick.get("title")]
    term = " ".join(p for p in parts if p).strip()
    return term or (pick.get("term") or "")


def profile_batch(seeds, lane, count=BATCH_SIZE, avoid_artists=(), rng=None):
    """Seed a batch straight from taste-profile.md.

    The profile says to vary seeds and never repeat one back to back, so we
    shuffle the lane rather than walking it in order, and push recently used
    artists to the end instead of dropping them (a short lane would otherwise
    run dry).
    """
    rng = rng or random
    pool = list((seeds or {}).get(lane) or [])
    if not pool:
        # An unknown or unparsed lane still has to produce music.
        pool = [a for artists in (seeds or {}).values() for a in artists]
    if not pool:
        return []

    avoid = {a.lower() for a in (avoid_artists or ())}
    fresh = [a for a in pool if a.lower() not in avoid]
    stale = [a for a in pool if a.lower() in avoid]
    rng.shuffle(fresh)
    rng.shuffle(stale)
    ordered = fresh + stale

    picks = []
    i = 0
    while len(picks) < count and ordered:
        artist = ordered[i % len(ordered)]
        picks.append({
            "title": None,
            "artist": artist,
            "why": "from your profile — %s lane" % lane,
            "source": "profile",
        })
        i += 1
        if i > count * 4:       # pool smaller than the batch; stop cycling
            break
    return picks


def validate_claude_picks(payload):
    """Accept only the documented shape; return [] for anything else.

    Deliberately strict and silent. A malformed model response is a fallback
    trigger, not an error to surface -- the music must not stop because a
    subprocess returned prose.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("picks")
    if not isinstance(raw, list):
        return []

    picks = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        artist = item.get("artist")
        if not isinstance(title, str) or not isinstance(artist, str):
            continue
        if not title.strip() or not artist.strip():
            continue
        why = item.get("why")
        why = why.strip() if isinstance(why, str) else ""
        picks.append({
            "title": title.strip(),
            "artist": artist.strip(),
            # The overlay shows Claude's stated reason verbatim. If it gave
            # none, say so plainly rather than inventing one after the fact.
            "why": why[:80] if why else "picked for this mood",
            "source": "claude",
        })
    return picks


def choose_resolution(songs, exclude_ids=(), preferred_artist=None):
    """Pick which search result to actually play.

    Search returns near-misses (live versions, covers, remixes by other
    artists). Prefer a result whose artist matches what we asked for, then fall
    back to the first result we have not played recently.
    """
    exclude = {str(i) for i in (exclude_ids or ())}
    candidates = [s for s in (songs or [])
                  if s.get("catalogId") and str(s["catalogId"]) not in exclude]
    if not candidates:
        return None
    if preferred_artist:
        want = preferred_artist.lower()
        for song in candidates:
            artist = (song.get("artist") or "").lower()
            if want in artist or artist in want:
                return song
    return candidates[0]
