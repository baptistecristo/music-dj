"""Pure logic over queue, history and ratings.

Everything here is a function of injected state -- no I/O, no transport, no
clock beyond what the caller passes in. That is what makes the daemon testable
without a browser.
"""

from . import moods

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


ECHO_POSITION_MS = 60000      # nothing real ends this soon after it started
ECHO_FRACTION = 0.5           # ...unless the item is genuinely that short


def ended_too_early(evt):
    """Is this "ended" the queue swap echoing rather than a song finishing?

    Swapping the player's queue tears the old one down, and the player reports
    that teardown as an end -- naming the song that just started, because by
    then that is what it considers current. Taken at face value it burns a
    track seconds in.

    How far the playhead actually got settles it. An event that does not say
    is taken at its word: only the page knows, and older pages do not send it.
    """
    position = (evt or {}).get("position")
    duration = (evt or {}).get("duration")
    if position is None:
        return False
    if position >= ECHO_POSITION_MS:
        return False
    # A 30s preview clip really does end at 30s, so a small position is only
    # suspicious when the track had much further to go.
    return not duration or position < duration * ECHO_FRACTION


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


def rated_in_lane(ratings, lane, stars, lane_of=moods.lane_for):
    """Tracks rated exactly `stars` anywhere in this lane -- for the prompt.

    Pooled across every mood that draws from the lane, because that is what
    the batch is built for: a star given while coding is evidence for
    research too, since both are focus.
    """
    out, seen = [], set()
    for cid, entry in (ratings or {}).items():
        for mood, value in ((entry or {}).get("byMood") or {}).items():
            if cid in seen or lane_of(mood) != lane or int(value) != stars:
                continue
            seen.add(cid)
            out.append({"catalogId": cid,
                        "title": entry.get("title"),
                        "artist": entry.get("artist")})
    return out


# ----------------------------------------------------------------- signals
#
# What they do is a rating they never typed: skipping a song ten seconds in
# says more than any star, and letting one play to the end is a quiet nod.
# Withholding stars says nothing at all -- unrated stays strictly neutral.
# What these add up to is worked out under "taste" below; this half only
# records them.

EARLY_SKIP_FRACTION = 0.4     # skipped before 40% through counts as a verdict


def record_signal(signals, track, mood, kind, position_ms, duration_ms, now):
    """Fold one listen outcome into the signals store. kind: skip|complete."""
    cid = str((track or {}).get("catalogId") or "")
    if not cid or not mood:
        return signals
    signals = dict(signals or {})
    entry = dict(signals.get(cid) or {})
    entry["title"] = track.get("title") or entry.get("title")
    entry["artist"] = track.get("artist") or entry.get("artist")
    by_mood = dict(entry.get("byMood") or {})
    m = dict(by_mood.get(mood) or {"skips": 0, "earlySkips": 0, "completes": 0})
    if kind == "complete":
        m["completes"] = m.get("completes", 0) + 1
    else:
        m["skips"] = m.get("skips", 0) + 1
        if duration_ms and (position_ms or 0) / duration_ms < EARLY_SKIP_FRACTION:
            m["earlySkips"] = m.get("earlySkips", 0) + 1
    m["lastAt"] = now
    by_mood[mood] = m
    entry["byMood"] = by_mood
    signals[cid] = entry
    return signals


def often_skipped_in_lane(signals, lane, limit=10, lane_of=moods.lane_for):
    """What they keep skipping in this lane, worst first, for the advisor."""
    rows = []
    for entry in (signals or {}).values():
        early = sum((m or {}).get("earlySkips", 0)
                    for mood, m in ((entry or {}).get("byMood") or {}).items()
                    if lane_of(mood) == lane)
        if early >= 1 and entry.get("title"):
            rows.append((early, "%s — %s" % (entry["title"], entry.get("artist"))))
    rows.sort(reverse=True)
    return [name for _n, name in rows[:limit]]


# ------------------------------------------------------------------- taste
#
# What the stars and the skips add up to. Three ideas, taken from how the
# ListenBrainz playlist engine (troi) uses its feedback:
#
# - Pool by lane, not by mood. Verdicts are recorded against the mood they
#   were given in, but the music is picked per lane, and coding and research
#   both draw from focus. Keeping them apart split five labels' worth of thin
#   evidence where there were only ever a handful of lanes underneath.
# - Carry the verdict to the artist. A catalogue has tens of millions of
#   songs and you meet the same one twice a year, so a per-track verdict is
#   spent on a track that never comes back. The artist is the cheapest
#   similarity edge we have, and it is already on every row we store.
# - Score, do not ban. One bad afternoon is not a verdict, and a verdict from
#   March should not weigh the same as one from yesterday.

STAR_VALUE = {1: -2.0, 2: -0.5, 3: 0.0, 4: 1.0, 5: 2.0}
EARLY_SKIP_VALUE = -0.75     # per early skip
COMPLETE_VALUE = 0.25        # per full listen -- weak: it may just be on
HALF_LIFE_DAYS = 45.0        # a verdict is worth half this much, this long on
ARTIST_WEIGHT = 0.5          # an artist verdict next to a verdict on the track
ARTIST_CAP = 3.0             # ...and no artist outvotes the profile outright
DROP_BELOW = -1.5            # at or under this, stop offering it


def decayed(value, at, now, half_life=HALF_LIFE_DAYS):
    """Fade a verdict by its age. An undated one never fades.

    Undated rows are the ones written before signals carried a timestamp;
    treating them as brand new would be wrong, but so would discarding
    somebody's ratings on an upgrade, so they simply stand still.
    """
    if not at or not now:
        return value
    days = max(0.0, (now - at) / 86400.0)
    return value * (0.5 ** (days / half_life))


# Apple's artist string for the same act is not stable: the track you rated
# came back as "Daft Punk" and the next one as "Daft Punk feat. Julian
# Casablancas". Matched literally, the verdict would not carry -- which is the
# one thing this is for.
_CREDIT_SPLITS = (" feat.", " feat ", " featuring ", " ft. ", " ft ",
                  " with ", " presents", " vs. ", " vs ", " & friends")
MIN_ARTIST_MATCH = 4          # below this, containment is coincidence


def _artist_key(name):
    key = (name or "").strip().lower()
    for token in _CREDIT_SPLITS:
        cut = key.find(token)
        if cut > 0:
            key = key[:cut]
    if key.startswith("the "):
        key = key[4:]
    return " ".join(key.split())


def _artist_score(artists, name):
    """This artist's standing, tolerating how the name came back.

    Exact first; then containment either way, so "Daft Punk" and "Daft Punk &
    Pharrell" are recognised as the same claim. Short names are matched only
    exactly -- "Air" is inside far too many strings to mean anything.
    """
    key = _artist_key(name)
    if not key:
        return 0.0
    if key in artists:
        return artists[key]
    if len(key) < MIN_ARTIST_MATCH:
        return 0.0
    best = 0.0
    for known, value in artists.items():
        if len(known) < MIN_ARTIST_MATCH:
            continue
        if key in known or known in key:
            # The strongest claim wins rather than the first one iterated, so
            # the answer does not depend on dict order.
            if abs(value) > abs(best):
                best = value
    return best


def taste(ratings, signals, lane, now, lane_of=moods.lane_for):
    """Everything learned that bears on this lane.

    Returns {"tracks": {catalogId: score}, "artists": {key: score},
    "seenAs": {key: name}}, where a positive score is evidence for and a
    negative one evidence against, and seenAs remembers how each artist was
    actually spelled.
    """
    tracks, artists, seen_as = {}, {}, {}

    def add(cid, artist, value, at):
        value = decayed(value, at, now)
        if not value:
            return
        tracks[str(cid)] = tracks.get(str(cid), 0.0) + value
        key = _artist_key(artist)
        if key:
            artists[key] = artists.get(key, 0.0) + value
            # Keep how it was spelled, so the prompt can name the artist the
            # way the store does rather than in the flattened match key.
            seen_as.setdefault(key, (artist or "").strip() or key)

    for cid, entry in (ratings or {}).items():
        for mood, stars in ((entry or {}).get("byMood") or {}).items():
            if lane_of(mood) != lane:
                continue
            # Ratings carry no timestamp of their own: a star is a considered
            # verdict, and it stands until it is changed.
            add(cid, entry.get("artist"), STAR_VALUE.get(int(stars), 0.0), None)

    for cid, entry in (signals or {}).items():
        for mood, m in ((entry or {}).get("byMood") or {}).items():
            if lane_of(mood) != lane:
                continue
            at = (m or {}).get("lastAt")
            value = (EARLY_SKIP_VALUE * (m.get("earlySkips") or 0)
                     + COMPLETE_VALUE * (m.get("completes") or 0))
            add(cid, entry.get("artist"), value, at)

    # Left uncapped here: score_track has to take this track's own verdict
    # back out before capping, or the cap would be applied to a number that
    # still has the track itself inside it.
    return {"tracks": tracks, "artists": artists, "seenAs": seen_as}


def score_track(view, track):
    """How much this lane's history argues for or against one candidate.

    Two terms: what was said about this song, and what was said about the rest
    of the artist's. The second counts half -- a weaker claim than a verdict on
    the song itself, but the only claim available for a song that has never
    come up before, which is nearly all of them.

    The artist term deliberately excludes this song. Counting it twice made a
    single track's own bad run look like a pattern across the artist, which
    was enough to bury a track its one full listen should have redeemed.
    """
    view = view or {}
    tracks = view.get("tracks") or {}
    artists = view.get("artists") or {}
    cid = str((track or {}).get("catalogId") or "")
    own = tracks.get(cid, 0.0)

    key = _artist_key((track or {}).get("artist"))
    if key in artists:
        theirs = artists[key] - own          # everything else by them
    else:
        theirs = _artist_score(artists, (track or {}).get("artist"))
    # Capped so a run of bad luck with one prolific artist cannot bury a whole
    # lane, and a favourite cannot crowd out everything else.
    theirs = max(-ARTIST_CAP, min(ARTIST_CAP, theirs))
    return own + theirs * ARTIST_WEIGHT


def rank_by_taste(view, tracks):
    """Best-first, dropping what this lane has argued against.

    Stable, so an order the picker chose deliberately survives wherever the
    evidence is silent -- which, for a fresh lane, is everywhere.
    """
    scored = [(score_track(view, t), i, t) for i, t in enumerate(tracks or [])]
    kept = [(s, i, t) for s, i, t in scored if s > DROP_BELOW]
    kept.sort(key=lambda row: (-row[0], row[1]))
    return [t for _s, _i, t in kept]


def artists_by_verdict(view, limit=8):
    """(liked, disliked) artist names for this lane, strongest first.

    For the prompt: naming who lands and who does not is what turns a pile of
    per-track stars into something a picker can actually act on.
    """
    seen_as = (view or {}).get("seenAs") or {}
    rows = sorted(((v, k) for k, v in ((view or {}).get("artists") or {}).items()),
                  reverse=True)
    # Named as the store spells them, not as the match key flattens them.
    liked = [seen_as.get(k, k) for value, k in rows if value >= 1.0][:limit]
    disliked = [seen_as.get(k, k)
                for value, k in reversed(rows) if value <= -1.0][:limit]
    return liked, disliked
