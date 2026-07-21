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
PLAY_ATTEMPTS = 3       # dead tracks to walk past before giving up on a cycle


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
        self.autoplay_blocked = False
        self.playing = False
        self.listeners = []          # UI push callbacks
        self._refill_lock = asyncio.Lock()
        # Confirming a play takes several seconds. Without this, a mood change
        # arriving next to a trackEnded runs two of them at once and the queue
        # advances twice -- you hear one track while the log names another.
        self._play_lock = asyncio.Lock()

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
                # So the overlay's play/pause button shows the action it will
                # take, rather than guessing.
                "playing": self.playing,
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

        # Cutting a song off partway is jarring, and moods drift while you work.
        # So the new lane starts at the next track boundary -- except for the
        # moods that mean something just broke, where the whole point is that
        # calmer music arrives while it still matters. Pinning a mood by hand
        # is an explicit request, so that switches now too.
        if self.current and not (self.is_urgent(mood) or self.pinned):
            log.info("queued %s for the next track; letting this one finish",
                     self.lane)
            self.push()
            return
        await self.play_next()

    def is_urgent(self, mood):
        urgent = self.config.get("urgent_moods") or ["debugging"]
        return mood in urgent

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
        # Serialised rather than skipped. A refill arriving while one is in
        # flight is usually a mood change, and dropping it left the new mood
        # holding the empty queue set_mood() just built, with nothing playing.
        async with self._refill_lock:
            mood, lane = self.mood, self.lane
            # The Claude picker shells out and can sit there for 15s. Run it
            # off the event loop or playback events queue up behind it and the
            # music stutters between tracks.
            loop = asyncio.get_running_loop()
            picks = await loop.run_in_executor(
                None, self.picks_for, mood, lane) or []
            source = picks[0].get("source", "profile") if picks else "profile"
            resolved = await self.resolve(picks)

            if (self.mood, self.lane) != (mood, lane):
                # Resolving took long enough for the mood to move on. Writing
                # this batch would label the new queue with the old mood.
                log.info("dropping stale refill for %s/%s", mood, lane)
                return

            resolved = library.dedupe_picks(
                resolved, self.history,
                already_queued=library.queue_tracks(self.queue))

            banned = library.banned_ids(self.ratings, mood)
            resolved = [t for t in resolved if str(t["catalogId"]) not in banned]

            keep = library.queue_tracks(self.queue) + resolved
            self.queue = library.make_queue(keep, mood, lane, source, self.now())
            store.write_json(store.QUEUE, self.queue)
            log.info("queue refilled: %d tracks (%s)", len(keep), source)

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
            # A lane with fewer artists than the batch size gets cycled, so the
            # same artist is searched more than once. Excluding what this batch
            # already took makes the repeat yield their second song instead of
            # the same one over and over.
            song = picker.choose_resolution(reply.get("songs"),
                                            exclude_ids=exclude,
                                            preferred_artist=pick.get("artist"))
            if not song:
                return None
            # No await between choosing and claiming, so concurrent resolves
            # cannot land on the same track.
            exclude.add(str(song["catalogId"]))
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
        async with self._play_lock:
            return await self._play_next()

    async def ensure_playing(self):
        """Start the music if nothing is playing, and only then.

        Confirming a track takes several seconds, during which `current` is
        still None. A caller polling on that alone starts a second track over
        the first a couple of seconds in, which is what it sounds like: a song
        begins, then gets replaced. The in-flight check is the fix.
        """
        if self.current is not None or self._play_lock.locked():
            return None
        return await self.play_next()

    async def _play_next(self):
        if not self.tx.connected:
            self.notice = "no player"
            self.push()
            return None

        if library.needs_refill(self.queue):
            await self.refill()

        # A track is popped before we try to play it, so bailing out on an
        # error would leave nothing playing and no trackEnded on the way —
        # the daemon would simply go quiet. Walk past dead tracks instead,
        # but stop after a few so an unusable player can't eat the queue.
        track = None
        for _ in range(PLAY_ATTEMPTS):
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
            if not reply.get("error"):
                break

            log.warning("play failed for %s: %s", track.get("title"), reply["error"])
            self.notice = "skipped %s (%s)" % (track.get("title"), reply["error"])
            track = None
            if not self.tx.connected:
                # The player is gone; burning the rest of the queue against a
                # dead socket helps nobody.
                break

        if track is None:
            self.push()
            return None

        # State 2 is "playing". Anything else means the command was accepted
        # but no audio came out — the failure that looks like success, and the
        # one worth naming in the log rather than leaving you to wonder.
        state = reply.get("state")
        if state == 2:
            log.info("playing %s — %s", track.get("title"), track.get("artist"))
        else:
            log.warning("%s was accepted but did not start (state %s). State 1 "
                        "means the browser is holding audio: click play once in "
                        "the DJ tab.", track.get("title"), state)

        self.current = track
        self.history = library.remember_play(self.history, track, self.now())
        store.write_json(store.HISTORY, self.history)
        self.notice = None
        self.push()

        # Refill ahead of time so the next advance never waits on a search.
        if library.needs_refill(self.queue):
            asyncio.create_task(self.refill())
        return track

    async def reseed(self):
        """Put the current track back after the tab lost its queue.

        If nothing was playing, start the queue instead -- a reload should
        never be the reason the music stays off.
        """
        if not self.tx.connected:
            return
        if not self.current:
            await self.play_next()
            return
        reply = await self.tx.call(
            {"cmd": "play", "catalogId": self.current["catalogId"]}, timeout=45)
        if reply.get("error"):
            log.warning("re-seed failed (%s); moving on", reply["error"])
            await self.play_next()

    # --------------------------------------------------------------- events

    async def on_event(self, evt):
        kind = evt.get("evt")

        if kind == "trackEnded":
            # Cutting in mid-song makes the player report the interrupted track
            # as ended, and treating that as "finished, move on" skips a track
            # you never heard. Only the track we believe is playing can end.
            ended = evt.get("catalogId")
            playing = (self.current or {}).get("catalogId")
            if ended and playing and str(ended) != str(playing):
                log.debug("ignoring trackEnded for %s; we are on %s",
                          ended, playing)
                return
            # Apple's own autoplay would pick the next track for us; we skip
            # deliberately, because curating is the entire point.
            await self.play_next()

        elif kind == "playback":
            # State 2 is "playing", so audio is coming out and whatever the
            # browser was blocking, it isn't blocking any more.
            # 2 is playing, 3 is paused; the rest are transitional.
            if evt.get("state") in (2, 3):
                self.playing = evt.get("state") == 2
            if evt.get("state") == 2 and self.autoplay_blocked:
                log.info("audio is flowing again; autoplay unblocked")
                self.autoplay_blocked = False
                self.notice = None
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
            # Loud on purpose. With no overlay running this log line is the
            # only way to find out why the music went quiet, and the fix is a
            # single click that nobody would guess at.
            if not self.autoplay_blocked:
                log.warning("autoplay blocked by the browser — click play once "
                            "in the DJ tab and it will pick up from there")
            self.autoplay_blocked = True
            self.notice = "click play in the DJ tab once"
            self.push()

        elif kind == "ready":
            self.previews_only = bool(evt.get("previewOnly"))
            if self.previews_only:
                # No subscription: playback would be capped at 30s clips, so
                # say so rather than starting something that cannot work.
                self.notice = "check which Apple ID is signed in"
                self.push()
                return
            self.notice = None
            # A reload wipes the MusicKit queue and every listener, so re-seed
            # what was playing. This hangs off "ready" rather than "injected"
            # because injected fires at document_start, when MusicKit does not
            # exist yet and a play command would simply be dropped.
            await self.reseed()
            self.push()

        elif kind in ("injected", "tabReady"):
            # The page is back but not necessarily usable yet; wait for ready.
            # Anything in flight died with the old document, so stop waiting on
            # replies that will never arrive.
            if hasattr(self.tx, "fail_pending"):
                self.tx.fail_pending("the tab reloaded mid-command")
            self.notice = None
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
                # Reflect it straight away rather than waiting for the player
                # to report back, so the button never lags the click.
                if action in ("pause", "resume"):
                    self.playing = action == "resume"
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
