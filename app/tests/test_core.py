"""Daemon behaviour tests, with the extension mocked.

Storage is redirected to a tmp dir in every test -- these must never touch the
real ~/.music-dj.
"""

import asyncio
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import core, library, moods, store  # noqa: E402

PROFILE = """
## Mood → seed directions

- **energized / shipping something** → French touch: Folamour, Bellaire, Dabeull.
- **tense / debugging / frustrated** → warm soul: Bill Withers, Al Green.
- **locked in / deep focus** → instrumental: Daft Punk, Sofiane Pamart.
- **mellow / writing / reflective** → chanson: Francis Cabrel, Barbara.
- **loose / late night** → feel-good: Bon Entendeur, Gipsy Kings.
"""


class FakeTransport:
    """Stands in for the extension. Records calls, replies from a script."""

    def __init__(self, connected=True):
        self.connected = connected
        self.calls = []
        self.fail_search = False
        self.fail_play = False
        self.search_delay = 0
        self.empty_search = False
        self.playlist_tracks = []
        self.counter = 0

    async def call(self, cmd, timeout=None):
        self.calls.append(cmd)
        kind = cmd.get("cmd")

        if kind == "search":
            if self.search_delay:
                await asyncio.sleep(self.search_delay)
            if self.fail_search:
                return {"error": "no tab"}
            if self.empty_search:
                return {"songs": []}
            self.counter += 1
            term = cmd.get("term", "")
            return {"songs": [{
                "catalogId": "cat%d" % self.counter,
                "title": "Song %d" % self.counter,
                "artist": term.split()[0] if term else "Someone",
                "artworkUrl": None,
                "durationMs": 200000,
            }]}

        if kind == "play":
            return {"error": "playback failed"} if self.fail_play else {"ok": True}
        if kind == "playlistTracks":
            return {"trackIds": list(self.playlist_tracks)}
        return {"ok": True}

    def sent(self, kind):
        return [c for c in self.calls if c.get("cmd") == kind]


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DIR", str(tmp_path))
    (tmp_path / store.PROFILE).write_text(PROFILE, encoding="utf-8")
    (tmp_path / store.STATE).write_text('{"current_mood": "coding"}', encoding="utf-8")
    return tmp_path


def make_dj(tx=None, config=None):
    dj = core.DJ(tx or FakeTransport(), config=config or {},
                 now=lambda: 1000.0, rng=random.Random(0))
    return dj


# ------------------------------------------------------------------- startup

def test_dj_adopts_the_mood_already_in_state_json():
    assert make_dj().mood == "coding"


def test_dj_maps_that_mood_onto_a_profile_lane():
    assert make_dj().lane == "focus"


def test_dj_loads_seeds_from_the_profile():
    assert make_dj().seeds["tense"] == ["Bill Withers", "Al Green"]


# --------------------------------------------------------------- queue build

@pytest.mark.asyncio
async def test_refill_resolves_picks_to_catalog_ids():
    dj = make_dj()
    await dj.refill()
    tracks = library.queue_tracks(dj.queue)
    assert tracks and all(t["catalogId"].startswith("cat") for t in tracks)


@pytest.mark.asyncio
async def test_refill_writes_the_queue_to_disk_with_the_why_attached(isolated_storage):
    dj = make_dj()
    await dj.refill()
    saved = store.read_json(store.QUEUE, {})
    assert saved["tracks"][0]["why"].startswith("from your profile")


@pytest.mark.asyncio
async def test_refill_drops_picks_that_do_not_resolve():
    tx = FakeTransport()
    tx.empty_search = True
    dj = make_dj(tx)
    await dj.refill()
    assert library.queue_tracks(dj.queue) == []


@pytest.mark.asyncio
async def test_refill_skips_tracks_rated_one_star_in_this_mood():
    dj = make_dj()
    await dj.refill()
    banned = library.queue_tracks(dj.queue)[0]["catalogId"]
    dj.ratings = library.rate({}, {"catalogId": banned, "title": "x", "artist": "y"},
                              "coding", 1)
    dj.queue = library.make_queue([], "coding", "focus", "profile", 0)
    await dj.refill()
    assert banned not in [t["catalogId"] for t in library.queue_tracks(dj.queue)]


@pytest.mark.asyncio
async def test_a_one_star_track_is_still_allowed_in_a_different_mood():
    dj = make_dj()
    await dj.refill()
    cid = library.queue_tracks(dj.queue)[0]["catalogId"]
    dj.ratings = library.rate({}, {"catalogId": cid, "title": "x", "artist": "y"},
                              "debugging", 1)
    assert cid not in library.banned_ids(dj.ratings, "coding")


# ------------------------------------------------------------------ playback

@pytest.mark.asyncio
async def test_play_next_sends_a_play_command_and_records_history():
    dj = make_dj()
    track = await dj.play_next()
    assert track is not None
    assert dj.tx.sent("play")[0]["catalogId"] == track["catalogId"]
    assert dj.history["plays"][0]["catalogId"] == track["catalogId"]


@pytest.mark.asyncio
async def test_track_ended_advances_to_the_next_track():
    dj = make_dj()
    first = await dj.play_next()
    await dj.on_event({"evt": "trackEnded", "catalogId": first["catalogId"]})
    assert dj.current["catalogId"] != first["catalogId"]
    assert len(dj.tx.sent("play")) == 2


@pytest.mark.asyncio
async def test_playback_continues_across_many_tracks_without_repeating():
    dj = make_dj()
    seen = []
    for _ in range(25):
        track = await dj.play_next()
        assert track is not None, "the music stopped"
        seen.append(track["catalogId"])
    assert len(set(seen)) == len(seen), "a track repeated"


@pytest.mark.asyncio
async def test_the_queue_refills_itself_before_running_out():
    dj = make_dj()
    await dj.refill()
    for _ in range(20):
        await dj.play_next()
        await asyncio.sleep(0)
    assert library.queue_tracks(dj.queue), "queue ran dry"


@pytest.mark.asyncio
async def test_a_failed_play_does_not_record_history_or_crash():
    tx = FakeTransport()
    tx.fail_play = True
    dj = make_dj(tx)
    assert await dj.play_next() is None
    assert dj.history.get("plays", []) == []


@pytest.mark.asyncio
async def test_nothing_is_sent_when_the_extension_is_disconnected():
    tx = FakeTransport(connected=False)
    dj = make_dj(tx)
    assert await dj.play_next() is None
    assert tx.sent("play") == []
    assert dj.ui_state()["notice"] == "no player"
    assert dj.ui_state()["connected"] is False


@pytest.mark.asyncio
async def test_search_failure_leaves_the_daemon_alive():
    tx = FakeTransport()
    tx.fail_search = True
    dj = make_dj(tx)
    await dj.refill()          # must not raise
    assert await dj.play_next() is None


# --------------------------------------------------------------- mood change

@pytest.mark.asyncio
async def test_changing_mood_rebuilds_the_queue_for_the_new_lane():
    dj = make_dj()
    await dj.set_mood("debugging")
    assert dj.lane == "tense"
    assert dj.queue["lane"] == "tense"
    # Seeds must come from the tense lane, not the previous one.
    terms = [c["term"] for c in dj.tx.sent("search")]
    assert any("Withers" in t or "Green" in t for t in terms)


@pytest.mark.asyncio
async def test_setting_the_same_mood_again_does_not_rebuild():
    dj = make_dj()
    await dj.set_mood("coding")
    assert dj.tx.sent("play") == []


@pytest.mark.asyncio
async def test_state_json_changes_drive_the_mood(isolated_storage):
    dj = make_dj()
    # The watcher must be running *before* the file changes, or its opening
    # mtime snapshot already contains the change and there is nothing to notice.
    task = asyncio.create_task(dj.watch_state(interval=0.01))
    await asyncio.sleep(0.05)
    (isolated_storage / store.STATE).write_text(
        '{"current_mood": "writing"}', encoding="utf-8")
    os.utime(isolated_storage / store.STATE, (2e9, 2e9))
    await asyncio.sleep(0.15)
    task.cancel()
    assert dj.mood == "writing" and dj.lane == "mellow"


@pytest.mark.asyncio
async def test_a_pinned_mood_ignores_state_json(isolated_storage):
    dj = make_dj()
    await dj.set_mood("loose", pinned=True, force=True)
    task = asyncio.create_task(dj.watch_state(interval=0.01))
    await asyncio.sleep(0.05)
    (isolated_storage / store.STATE).write_text(
        '{"current_mood": "debugging"}', encoding="utf-8")
    os.utime(isolated_storage / store.STATE, (2e9, 2e9))
    await asyncio.sleep(0.15)
    task.cancel()
    assert dj.mood == "loose"


@pytest.mark.asyncio
async def test_unpinning_hands_control_back_to_state_json():
    dj = make_dj()
    await dj.set_mood("loose", pinned=True, force=True)
    await dj.on_action({"action": "unpin"})
    assert dj.pinned is False and dj.mood == "coding"


@pytest.mark.asyncio
async def test_the_ui_reports_who_chose_the_mood():
    dj = make_dj()
    assert dj.ui_state()["mood"]["source"] == "claude"
    await dj.set_mood("loose", pinned=True, force=True)
    assert dj.ui_state()["mood"]["source"] == "pinned"


# ------------------------------------------------------------- tab lifecycle

@pytest.mark.asyncio
async def test_a_tab_reload_re_seeds_the_current_track():
    dj = make_dj()
    track = await dj.play_next()
    dj.tx.calls.clear()
    # "ready" is the signal, not "injected": injected fires at document_start
    # when MusicKit does not exist yet and a play command is simply dropped.
    await dj.on_event({"evt": "ready", "previewOnly": False})
    assert dj.tx.sent("play")[0]["catalogId"] == track["catalogId"]


@pytest.mark.asyncio
async def test_injected_alone_does_not_try_to_play_into_a_half_built_page():
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.on_event({"evt": "injected"})
    assert dj.tx.sent("play") == []


@pytest.mark.asyncio
async def test_a_reload_with_nothing_playing_starts_the_queue():
    dj = make_dj()
    await dj.on_event({"evt": "ready", "previewOnly": False})
    assert dj.tx.sent("play"), "a reload left the music off"


@pytest.mark.asyncio
async def test_a_failed_reseed_moves_on_rather_than_going_silent():
    class DeadTrack(FakeTransport):
        """The old track no longer resolves; the next one still should."""
        def __init__(self):
            super().__init__()
            self.dead = None

        async def call(self, cmd, timeout=None):
            if cmd.get("cmd") == "play" and cmd["catalogId"] == self.dead:
                self.calls.append(cmd)
                return {"error": "NOT_FOUND"}
            return await super().call(cmd, timeout)

    tx = DeadTrack()
    dj = make_dj(tx)
    track = await dj.play_next()
    tx.dead = track["catalogId"]
    tx.calls.clear()
    await dj.on_event({"evt": "ready", "previewOnly": False})
    played = [c["catalogId"] for c in tx.sent("play")]
    assert played[0] == tx.dead and len(played) > 1, "gave up after one failure"


@pytest.mark.asyncio
async def test_autoplay_blocked_is_explained_not_swallowed():
    dj = make_dj()
    await dj.on_event({"evt": "autoplayBlocked"})
    assert "click play" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_autoplay_block_clears_once_audio_actually_flows():
    dj = make_dj()
    await dj.on_event({"evt": "autoplayBlocked"})
    assert dj.autoplay_blocked is True
    # State 2 is "playing": the click happened, the block is gone.
    await dj.on_event({"evt": "playback", "state": 2, "catalogId": "x",
                       "title": "T", "artist": "A"})
    assert dj.autoplay_blocked is False
    assert dj.ui_state()["notice"] is None


@pytest.mark.asyncio
async def test_a_still_blocked_state_does_not_clear_the_warning():
    dj = make_dj()
    await dj.on_event({"evt": "autoplayBlocked"})
    # State 1 is "loading" -- stuck there is what being blocked looks like.
    await dj.on_event({"evt": "playback", "state": 1, "catalogId": "x"})
    assert dj.autoplay_blocked is True
    assert "click play" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_a_reload_abandons_commands_the_old_page_will_never_answer():
    calls = []

    class Tracking(FakeTransport):
        def fail_pending(self, reason):
            calls.append(reason)

    dj = make_dj(Tracking())
    await dj.on_event({"evt": "injected"})
    assert calls, "in-flight commands were left to time out"


@pytest.mark.asyncio
async def test_repeating_an_artist_yields_a_different_song():
    # Real search returns the same ranked list every time, so cycling a short
    # lane used to resolve the same track repeatedly and dedupe down to one.
    class StableSearch(FakeTransport):
        async def call(self, cmd, timeout=None):
            self.calls.append(cmd)
            if cmd.get("cmd") == "search":
                artist = cmd["term"]
                return {"songs": [
                    {"catalogId": artist + "-1", "title": "First",
                     "artist": artist, "artworkUrl": None, "durationMs": 1000},
                    {"catalogId": artist + "-2", "title": "Second",
                     "artist": artist, "artworkUrl": None, "durationMs": 1000},
                ]}
            return {"ok": True}

    dj = make_dj(StableSearch())
    picks = [{"title": None, "artist": "Al Green", "why": "w"},
             {"title": None, "artist": "Al Green", "why": "w"}]
    resolved = await dj.resolve(picks)
    ids = [t["catalogId"] for t in resolved]
    assert len(set(ids)) == 2, "the repeat resolved to the same track"


@pytest.mark.asyncio
async def test_preview_only_points_at_the_apple_id():
    dj = make_dj()
    await dj.on_event({"evt": "ready", "storefront": "fr", "previewOnly": True})
    assert "Apple ID" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_a_ready_with_a_subscription_shows_no_notice():
    dj = make_dj()
    await dj.on_event({"evt": "ready", "storefront": "fr", "previewOnly": False})
    assert dj.ui_state()["notice"] is None


@pytest.mark.asyncio
async def test_the_user_changing_track_in_the_tab_is_followed_not_fought():
    dj = make_dj()
    await dj.play_next()
    await dj.on_event({"evt": "playback", "state": 2, "catalogId": "manual1",
                       "title": "Their Pick", "artist": "Someone"})
    assert dj.ui_state()["nowPlaying"]["title"] == "Their Pick"


# ------------------------------------------------------------------- actions

@pytest.mark.asyncio
async def test_skip_uses_our_queue_rather_than_apples():
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.on_action({"action": "skip"})
    assert dj.tx.sent("play") and not dj.tx.sent("skip")


@pytest.mark.asyncio
async def test_pause_and_resume_are_passed_through():
    dj = make_dj()
    await dj.on_action({"action": "pause"})
    await dj.on_action({"action": "resume"})
    assert dj.tx.sent("pause") and dj.tx.sent("resume")


@pytest.mark.asyncio
async def test_rating_is_stored_against_the_current_mood():
    dj = make_dj()
    track = await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 4})
    assert library.rating_for(dj.ratings, track["catalogId"], "coding") == 4
    assert dj.ui_state()["rating"] == 4


@pytest.mark.asyncio
async def test_five_stars_adds_to_the_configured_playlist():
    tx = FakeTransport()
    dj = make_dj(tx, config={"starred_playlist": {"id": "p.abc", "name": "🦅"}})
    track = await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    adds = tx.sent("addToPlaylist")
    assert adds and adds[0]["catalogId"] == track["catalogId"]
    assert adds[0]["playlistId"] == "p.abc"
    assert "added to" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_five_stars_does_not_duplicate_a_track_already_in_the_playlist():
    tx = FakeTransport()
    dj = make_dj(tx, config={"starred_playlist": {"id": "p.abc", "name": "🦅"}})
    track = await dj.play_next()
    tx.playlist_tracks = [track["catalogId"]]      # already starred once
    await dj.on_action({"action": "rate", "stars": 5})
    assert tx.sent("addToPlaylist") == []
    assert "already in" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_a_failed_membership_check_never_blind_writes_to_the_playlist():
    # Hundreds of curated tracks live in there. If we cannot confirm what is
    # already present, we add nothing.
    class Failing(FakeTransport):
        async def call(self, cmd, timeout=None):
            if cmd.get("cmd") == "playlistTracks":
                self.calls.append(cmd)
                return {"error": "network"}
            return await super().call(cmd, timeout)

    tx = Failing()
    dj = make_dj(tx, config={"starred_playlist": {"id": "p.abc", "name": "🦅"}})
    await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    assert tx.sent("addToPlaylist") == []


@pytest.mark.asyncio
async def test_fewer_than_five_stars_touches_no_playlist():
    tx = FakeTransport()
    dj = make_dj(tx, config={"starred_playlist": {"id": "p.abc", "name": "x"}})
    await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 3})
    assert tx.sent("addToPlaylist") == []


@pytest.mark.asyncio
async def test_five_stars_with_no_playlist_configured_says_so():
    dj = make_dj()
    await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    assert "no starred playlist" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_rating_with_nothing_playing_is_harmless():
    dj = make_dj()
    await dj.on_action({"action": "rate", "stars": 5})   # must not raise


# ----------------------------------------------------------------- ui push

@pytest.mark.asyncio
async def test_subscribers_are_pushed_state_on_change():
    dj = make_dj()
    seen = []
    dj.subscribe(seen.append)
    await dj.play_next()
    assert seen and seen[-1]["nowPlaying"]["title"]


@pytest.mark.asyncio
async def test_the_why_line_is_the_reason_the_picker_gave():
    dj = make_dj()
    await dj.play_next()
    assert dj.ui_state()["why"].startswith("from your profile")


@pytest.mark.asyncio
async def test_a_broken_subscriber_does_not_take_the_daemon_down():
    dj = make_dj()
    dj.subscribe(lambda _state: (_ for _ in ()).throw(RuntimeError("boom")))
    await dj.play_next()      # must not raise


# ------------------------------------------------- injected claude-style picks

@pytest.mark.asyncio
async def test_an_injected_picker_supplies_the_batch_and_its_reasons():
    def claude_like(mood, lane):
        return [{"title": "Veridis Quo", "artist": "Daft Punk",
                 "why": "French touch — you rated Bellaire 5 here",
                 "source": "claude"}]

    dj = core.DJ(FakeTransport(), config={}, now=lambda: 1.0,
                 rng=random.Random(0), picks_for=claude_like)
    await dj.play_next()
    assert dj.ui_state()["why"] == "French touch — you rated Bellaire 5 here"
    assert dj.queue["source"] == "claude"


@pytest.mark.asyncio
async def test_a_picker_that_returns_nothing_does_not_hang_playback():
    dj = core.DJ(FakeTransport(), config={}, now=lambda: 1.0,
                 rng=random.Random(0), picks_for=lambda m, l: [])
    assert await dj.play_next() is None
    assert dj.ui_state()["notice"] == "nothing to play"


@pytest.mark.asyncio
async def test_a_failed_play_moves_on_instead_of_going_silent():
    """A dead track must not end the session.

    play_next() pops the track before asking the extension to play it, so
    bailing out on an error leaves nothing playing and no trackEnded coming —
    the daemon just goes quiet until a human intervenes.
    """
    tx = FakeTransport()
    tx.fail_play = True
    dj = make_dj(tx)
    assert await dj.play_next() is None
    assert len(tx.sent("play")) > 1, "gave up after a single bad track"
    assert dj.notice, "the failure was never surfaced"


@pytest.mark.asyncio
async def test_a_mood_change_during_a_refill_is_not_dropped():
    """A refill in flight must not swallow the next mood change.

    The guard against concurrent refills used to make the second call a
    no-op, so a mood change landing mid-refill rebuilt an empty queue, found
    nothing to play, and stalled — then the in-flight refill finished and
    wrote its now-stale mood over the new one.
    """
    tx = FakeTransport()
    tx.search_delay = 0.2
    dj = make_dj(tx)
    inflight = asyncio.create_task(dj.refill())
    await asyncio.sleep(0.05)
    await dj.set_mood("debugging")
    await inflight
    assert dj.queue["mood"] == "debugging", "a stale refill overwrote the new mood"
    assert library.queue_tracks(dj.queue), "queue left empty after the mood change"
