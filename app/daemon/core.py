"""The DJ itself: mood in, music out.

The transport is injected, so every path in here can be exercised against a mock
extension. Nothing in this file knows about WebSockets.

A transport must provide:
    await call(cmd: dict) -> dict      # reply, or {"error": ...}
    .connected -> bool
"""

import asyncio
import logging
import random
import time

from . import library, moods, picker, store

log = logging.getLogger("music-dj")

SEARCH_TIMEOUT = 20
RESOLVE_CONCURRENCY = 4


class DJ:
    def __init__(self, transport, *, config=None, now=time.time, rng=None,
                 picks_for=None):
        self.tx = transport
        self.config = config or {}
        self.now = now
        self.rng = rng or random.Random()
        # Injected so milestone 4 can slot Claude in above the profile path
        # without this file having to care which one produced the batch.
        self.picks_for = picks_for or self._profile_picks

        self.seeds = moods.parse_seeds(store.read_text(store.PROFILE))
        self.ratings = store.read_json(store.RATINGS, {})
        self.history = store.read_json(store.HISTORY, {})
        self.queue = store.read_json(store.QUEUE, {}) or \
            library.make_queue([], None, None, "profile", self.now())

        self.mood = (store.read_json(store.STATE, {}) or {}).get("current_mood")
        self.pinned = False
        self.current = None
        self.notice = None
        self.previews_only = False
        self.listeners = []          # UI push callbacks
        self._refilling = False

    # ------------------------------------------------------------- plumbing

    @property
    def lane(self):
        return moods.lane_for(self.mood)

    def _profile_picks(self, mood, lane):
        recent = [p.get("artist") for p in (self.history.get("plays") or [])[:12]]
        return picker.profile_batch(self.seeds, lane, avoid_artists=recent,
                                    rng=self.rng)

    def subscribe(self, fn):
        self.listeners.append(fn)

    def ui_state(self):
        rating = 0
        if self.current and self.current.get("catalogId"):
            rating = library.rating_for(self.ratings, self.current["catalogId"],
                                        self.mood)
        return {
            "nowPlaying": {
                "title": (self.current or {}).get("title"),
                "artist": (self.current or {}).get("artist"),
                "artworkUrl": (self.current or {}).get("artworkUrl"),
                "position": (self.current or {}).get("position", 0),
                "duration": (self.current or {}).get("duration", 0),
            } if self.current else None,
            "mood": {"name": self.mood, "lane": self.lane,
                     "source": "pinned" if self.pinned else "claude"},
            "why": (self.current or {}).get("why"),
            "rating": rating,
            "connected": bool(self.tx.connected),
            "notice": self.notice,
        }

    def push(self):
        state = self.ui_state()
        for fn in list(self.listeners):
            try:
                fn(state)
            except Exception:
                log.debug("ui listener failed", exc_info=True)

    # ---------------------------------------------------------------- moods

    async def set_mood(self, mood, *, pinned=None, force=False):
        """Change mood and rebuild the queue. No-op if nothing changed."""
        if pinned is not None:
            self.pinned = pinned
        if mood == self.mood and not force:
            self.push()
            return
        log.info("mood -> %s (%s)", mood, "pinned" if self.pinned else "inferred")
        self.mood = mood
        self.queue = library.make_queue([], mood, self.lane, "profile", self.now())
        await self.refill()
        await self.play_next()

    async def watch_state(self, interval=2.0):
        """Poll state.json's mtime. Simpler and more reliable on Windows than
        filesystem events, and the file changes at most every few minutes."""
        last = store.mtime(store.STATE)
        while True:
            await asyncio.sleep(interval)
            try:
                stamp = store.mtime(store.STATE)
                if stamp == last:
                    continue
                last = stamp
                if self.pinned:
                    continue          # following state.json is paused
                mood = (store.read_json(store.STATE, {}) or {}).get("current_mood")
                if mood and mood != self.mood:
                    await self.set_mood(mood)
            except Exception:
                log.exception("state watch failed")

    # ---------------------------------------------------------------- queue

    async def refill(self):
        """Build a batch, resolve it to catalog ids, store it."""
        if self._refilling:
            return
        self._refilling = True
        try:
            mood, lane = self.mood, self.lane
            picks = self.picks_for(mood, lane) or []
            source = picks[0].get("source", "profile") if picks else "profile"
            resolved = await self.resolve(picks)
            resolved = library.dedupe_picks(
                resolved, self.history,
                already_queued=library.queue_tracks(self.queue))

            banned = library.banned_ids(self.ratings, mood)
            resolved = [t for t in resolved if str(t["catalogId"]) not in banned]

            keep = library.queue_tracks(self.queue) + resolved
            self.queue = library.make_queue(keep, mood, lane, source, self.now())
            store.write_json(store.QUEUE, self.queue)
            log.info("queue refilled: %d tracks (%s)", len(keep), source)
        finally:
            self._refilling = False

    async def resolve(self, picks):
        """Pick -> playable track, via search inside the tab."""
        if not picks:
            return []
        exclude = set(library.recent_ids(self.history))
        sem = asyncio.Semaphore(RESOLVE_CONCURRENCY)

        async def one(pick):
            term = picker.pick_term(pick)
            if not term:
                return None
            async with sem:
                reply = await self.tx.call({"cmd": "search", "term": term},
                                           timeout=SEARCH_TIMEOUT)
            if not reply or reply.get("error"):
                return None
            song = picker.choose_resolution(reply.get("songs"),
                                            exclude_ids=exclude,
                                            preferred_artist=pick.get("artist"))
            if not song:
                return None
            return {
                "catalogId": str(song["catalogId"]),
                "title": song.get("title") or pick.get("title"),
                "artist": song.get("artist") or pick.get("artist"),
                "artworkUrl": song.get("artworkUrl"),
                "duration": song.get("durationMs"),
                # Claude's actual stated reason, carried through untouched.
                "why": pick.get("why"),
                "mood": self.mood,
            }

        results = await asyncio.gather(*(one(p) for p in picks),
                                       return_exceptions=True)
        out = []
        for r in results:
            if isinstance(r, Exception):
                log.debug("resolve failed", exc_info=r)
            elif r:
                out.append(r)
        return out

    # ------------------------------------------------------------- playback

    async def play_next(self):
        if not self.tx.connected:
            self.notice = "no player"
            self.push()
            return None

        if library.needs_refill(self.queue):
            await self.refill()

        track, rest = library.advance(self.queue)
        if not track:
            log.warning("queue empty after refill; nothing to play")
            self.notice = "nothing to play"
            self.push()
            return None

        self.queue = rest
        store.write_json(store.QUEUE, self.queue)

        reply = await self.tx.call({"cmd": "play", "catalogId": track["catalogId"]},
                                   timeout=45)
        if reply.get("error"):
            log.warning("play failed for %s: %s", track.get("title"), reply["error"])
            return None

        self.current = track
        self.history = library.remember_play(self.history, track, self.now())
        store.write_json(store.HISTORY, self.history)
        self.notice = None
        self.push()

        # Refill ahead of time so the next advance never waits on a search.
        if library.needs_refill(self.queue):
            asyncio.create_task(self.refill())
        return track

    # --------------------------------------------------------------- events

    async def on_event(self, evt):
        kind = evt.get("evt")

        if kind == "trackEnded":
            # Apple's own autoplay would pick the next track for us; we skip
            # deliberately, because curating is the entire point.
            await self.play_next()

        elif kind == "playback":
            if self.current and evt.get("catalogId") == self.current.get("catalogId"):
                self.current["position"] = evt.get("position", 0)
                self.current["duration"] = evt.get("duration") or self.current.get("duration")
            elif evt.get("catalogId"):
                # Something outside the DJ changed the track (the user clicked
                # around in the tab). Follow it rather than fighting it.
                self.current = {
                    "catalogId": evt.get("catalogId"), "title": evt.get("title"),
                    "artist": evt.get("artist"), "artworkUrl": evt.get("artworkUrl"),
                    "position": evt.get("position", 0), "duration": evt.get("duration"),
                    "why": (self.current or {}).get("why"),
                }
            self.push()

        elif kind == "autoplayBlocked":
            self.notice = "click play in the DJ tab once"
            self.push()

        elif kind == "ready":
            self.previews_only = bool(evt.get("previewOnly"))
            self.notice = ("check which Apple ID is signed in"
                           if self.previews_only else None)
            self.push()

        elif kind in ("injected", "tabReady"):
            # A reload wipes the MusicKit queue and every listener. Re-seed the
            # track we believe is current -- this is the most common failure.
            self.notice = None
            if self.current:
                await self.tx.call(
                    {"cmd": "play", "catalogId": self.current["catalogId"]},
                    timeout=45)
            self.push()

        elif kind in ("tabGone",):
            self.notice = "no player"
            self.push()

    # ------------------------------------------------------------ UI actions

    async def on_action(self, msg):
        action = msg.get("action")

        if action in ("pause", "resume", "skip", "previous"):
            if action == "skip":
                await self.play_next()      # our queue, not Apple's
            else:
                await self.tx.call({"cmd": action})
            self.push()

        elif action == "rate":
            await self.rate(int(msg.get("stars", 0)))

        elif action == "setMood":
            await self.set_mood(msg.get("mood"),
                                pinned=bool(msg.get("pinned", True)), force=True)

        elif action == "unpin":
            self.pinned = False
            mood = (store.read_json(store.STATE, {}) or {}).get("current_mood")
            if mood:
                await self.set_mood(mood)
            else:
                self.push()

    async def rate(self, stars):
        if not self.current:
            return
        self.ratings = library.rate(self.ratings, self.current, self.mood, stars)
        store.write_json(store.RATINGS, self.ratings)
        self.push()
        if stars == 5:
            await self.star(self.current)

    async def star(self, track):
        """Add to the configured playlist, but never twice."""
        target = (self.config.get("starred_playlist") or {})
        pid = target.get("id")
        if not pid:
            self.notice = "no starred playlist configured"
            self.push()
            return

        existing = await self.tx.call({"cmd": "playlistTracks", "playlistId": pid},
                                      timeout=60)
        if existing.get("error"):
            self.notice = "couldn't check %s" % (target.get("name") or "playlist")
            self.push()
            return

        if str(track["catalogId"]) in {str(i) for i in existing.get("trackIds", [])}:
            self.notice = "already in %s" % (target.get("name") or "playlist")
            self.push()
            return

        reply = await self.tx.call({"cmd": "addToPlaylist", "playlistId": pid,
                                    "catalogId": track["catalogId"]})
        self.notice = ("couldn't add to %s" % target.get("name")) if reply.get("error") \
            else "added to %s" % (target.get("name") or "playlist")
        self.push()
