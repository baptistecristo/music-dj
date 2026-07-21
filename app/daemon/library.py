"""Pure logic over queue, history and ratings.

Everything here is a function of injected state -- no I/O, no transport, no
clock beyond what the caller passes in. That is what makes the daemon testable
without a browser.
"""

HISTORY_LIMIT = 200          # what we keep on disk
RECENT_FOR_DEDUPE = 30       # how far back "played recently" reaches
REFILL_AT = 3                # refill the queue when it drops to this many


# ----------------------------------------------------------------- history

def recent_ids(history, window=RECENT_FOR_DEDUPE):
    """Catalog ids of the last `window` plays, newest first."""
    entries = (history or {}).get("plays", [])
    return [e.get("catalogId") for e in entries[:window] if e.get("catalogId")]


def remember_play(history, track, now):
    """Prepend a play, newest first, trimmed to HISTORY_LIMIT."""
    history = dict(history or {})
    plays = list(history.get("plays", []))
    plays.insert(0, {
        "catalogId": track.get("catalogId"),
        "title": track.get("title"),
        "artist": track.get("artist"),
        "mood": track.get("mood"),
        "at": now,
    })
    history["plays"] = plays[:HISTORY_LIMIT]
    return history


# ------------------------------------------------------------------- queue

def dedupe_picks(picks, history, already_queued=(), window=RECENT_FOR_DEDUPE):
    """Drop picks we played recently, already have queued, or that repeat.

    Picks arrive resolved (they carry a catalogId). Unresolved ones are the
    caller's problem -- they never reach here.
    """
    seen = set(recent_ids(history, window))
    seen.update(t.get("catalogId") for t in (already_queued or []))
    out = []
    for pick in picks or []:
        cid = pick.get("catalogId")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(pick)
    return out


def needs_refill(queue, threshold=REFILL_AT):
    return len(queue_tracks(queue)) <= threshold


def queue_tracks(queue):
    return list((queue or {}).get("tracks", []))


def make_queue(tracks, mood, lane, source, now):
    """A queue is the batch plus the provenance of how it was picked."""
    return {
        "mood": mood,
        "lane": lane,
        "source": source,          # "claude" | "profile"
        "createdAt": now,
        "tracks": list(tracks),
    }


def advance(queue):
    """Pop the head. Returns (next_track_or_None, new_queue)."""
    tracks = queue_tracks(queue)
    if not tracks:
        return None, dict(queue or {}, tracks=[])
    head, rest = tracks[0], tracks[1:]
    return head, dict(queue or {}, tracks=rest)


# ----------------------------------------------------------------- ratings

def rate(ratings, track, mood, stars):
    """Store a rating scoped to the mood it was given in.

    Scoping is the whole point: one star while debugging must not ban a track
    from a Friday night.
    """
    if not track or not track.get("catalogId"):
        return dict(ratings or {})
    stars = max(0, min(5, int(stars)))
    ratings = dict(ratings or {})
    cid = str(track["catalogId"])
    entry = dict(ratings.get(cid) or {})
    entry.setdefault("title", track.get("title"))
    entry.setdefault("artist", track.get("artist"))
    by_mood = dict(entry.get("byMood") or {})
    if stars == 0:
        by_mood.pop(mood, None)      # 0 clears rather than records a zero
    else:
        by_mood[mood] = stars
    entry["byMood"] = by_mood
    if not by_mood:
        ratings.pop(cid, None)
    else:
        ratings[cid] = entry
    return ratings


def rating_for(ratings, catalog_id, mood):
    entry = (ratings or {}).get(str(catalog_id)) or {}
    return int((entry.get("byMood") or {}).get(mood, 0))


def rated_in_mood(ratings, mood, stars):
    """Tracks rated exactly `stars` in `mood` -- feeds the Claude prompt."""
    out = []
    for cid, entry in (ratings or {}).items():
        if int((entry.get("byMood") or {}).get(mood, 0)) == stars:
            out.append({"catalogId": cid,
                        "title": entry.get("title"),
                        "artist": entry.get("artist")})
    return out


def banned_ids(ratings, mood):
    """One star in this mood means don't play it in this mood again."""
    return {cid for cid, entry in (ratings or {}).items()
            if int((entry.get("byMood") or {}).get(mood, 0)) == 1}
