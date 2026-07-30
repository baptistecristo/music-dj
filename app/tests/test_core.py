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
        self.playlists = []
        self.fail_list = False
        self.ttml = None
        self.additions = []
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
        if kind == "listPlaylists":
            if self.fail_list:
                return {"error": "no tab"}
            return {"playlists": list(self.playlists)}
        if kind == "lyrics":
            return {"ttml": self.ttml}
        if kind == "recentlyAdded":
            return {"items": list(self.additions)}
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
    track = {"catalogId": cid, "artist": "y"}
    assert library.rank_by_taste(
        library.taste(dj.ratings, {}, "focus", dj.now()), [track]) == [track]


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
async def test_a_second_start_does_not_cut_in_over_the_first():
    # Confirming a play takes seconds, and `current` stays None throughout.
    # A poller watching only that used to start a second track a couple of
    # seconds into the first -- a song began, then swapped for another.
    tx = FakeTransport()
    tx.search_delay = 0.05
    dj = make_dj(tx)

    async def slow_play(cmd, timeout=None):
        if cmd.get("cmd") == "play":
            tx.calls.append(cmd)
            await asyncio.sleep(0.3)      # the player taking its time
            return {"ok": True, "state": 2}
        return await FakeTransport.call(tx, cmd, timeout)

    tx.call = slow_play
    first = asyncio.create_task(dj.ensure_playing())
    await asyncio.sleep(0.1)
    await dj.ensure_playing()             # the next poll tick, mid-play
    await first
    assert len(tx.sent("play")) == 1, "started a second track over the first"


@pytest.mark.asyncio
async def test_ensure_playing_does_nothing_when_a_track_is_already_on():
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.ensure_playing()
    assert dj.tx.sent("play") == []


@pytest.mark.asyncio
async def test_ensure_playing_starts_the_music_when_there_is_silence():
    dj = make_dj()
    assert await dj.ensure_playing() is not None


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
async def test_a_mood_drift_lets_the_current_song_finish():
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.set_mood("writing")          # not urgent
    assert dj.tx.sent("play") == [], "cut a song off mid-play"
    assert dj.lane == "mellow"             # but the new lane is queued up


@pytest.mark.asyncio
async def test_the_next_track_comes_from_the_new_lane():
    dj = make_dj()
    await dj.play_next()
    # Mirror the hook: the state file moves first, set_mood follows it. The
    # boundary re-read trusts the file, so the two must agree.
    store.write_json(store.STATE, {"current_mood": "writing"})
    await dj.set_mood("writing")
    dj.tx.calls.clear()
    await dj.on_event({"evt": "trackEnded", "catalogId": dj.current["catalogId"]})
    assert dj.tx.sent("play"), "the music stopped at the track boundary"
    assert dj.queue["lane"] == "mellow"


@pytest.mark.asyncio
async def test_something_breaking_switches_the_music_immediately():
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.set_mood("debugging")        # urgent: calm music should land now
    assert dj.tx.sent("play"), "waited for the track to end"


@pytest.mark.asyncio
async def test_pinning_a_mood_by_hand_lets_the_song_finish():
    # Picking a mood asks for what to play next. Cutting the current song off
    # to serve it -- after ten seconds of searching, no less -- is not what
    # the click meant; the queue behind it is what changes.
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.set_mood("loose", pinned=True, force=True)
    assert dj.tx.sent("play") == [], "cut a song off to serve the new lane"
    assert dj.tx.sent("pause") == [], "went quiet instead of finishing the song"
    assert dj.queue["lane"] == "loose", "the queue behind it never changed"
    assert "loose" in dj.ui_state()["notice"], "the click said nothing back"


@pytest.mark.asyncio
async def test_a_hand_picked_lane_starts_at_the_end_of_that_song():
    dj = make_dj()
    track = await dj.play_next()
    await dj.set_mood("loose", pinned=True, force=True)
    dj.tx.calls.clear()
    await dj.on_event({"evt": "trackEnded", "catalogId": track["catalogId"],
                       "position": 199000, "duration": 200000})
    assert dj.tx.sent("play"), "the music stopped at the boundary"
    assert dj.queue["lane"] == "loose"
    assert dj.ui_state()["notice"] is None, "the waiting notice outlived the wait"


@pytest.mark.asyncio
async def test_a_hand_picked_urgent_mood_does_not_interrupt_either():
    # is_urgent exists for the hook noticing something broke, not for a mood
    # the user chose from the picker a moment ago.
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.set_mood("debugging", pinned=True, force=True)
    assert dj.tx.sent("play") == []


@pytest.mark.asyncio
async def test_a_switch_from_silence_says_which_mood_it_is_lining_up():
    # With no song to finish behind it, the switch is a plain wait: ten or
    # twenty seconds of searching with nothing on screen to explain it.
    tx = FakeTransport()
    tx.search_delay = 0.05
    dj = make_dj(tx)
    seen = []
    dj.subscribe(lambda s: seen.append(s.get("preparing")))
    switch = asyncio.create_task(dj.set_mood("loose", pinned=True, force=True))
    await asyncio.sleep(0.02)
    assert dj.ui_state()["preparing"] == "loose"
    await switch
    assert "loose" in seen
    assert dj.ui_state()["preparing"] is None, "the wait was never cleared"
    assert dj.current is not None, "the switch never started the music"


@pytest.mark.asyncio
async def test_the_start_poller_keeps_out_while_a_lane_is_being_built():
    # ensure_playing() fires every few seconds on nothing being current. The
    # silence during a switch is deliberate and the queue behind it is half
    # written; starting from it would play a track from neither lane.
    tx = FakeTransport()
    tx.search_delay = 0.1
    dj = make_dj(tx)
    switch = asyncio.create_task(dj.set_mood("loose", pinned=True, force=True))
    await asyncio.sleep(0.02)
    assert await dj.ensure_playing() is None, "cut in on a half-built queue"
    await switch


@pytest.mark.asyncio
async def test_a_mood_drift_does_not_stop_the_music_to_build_the_new_lane():
    dj = make_dj()
    await dj.play_next()
    dj.tx.calls.clear()
    await dj.set_mood("writing")
    assert dj.tx.sent("pause") == []
    assert dj.ui_state()["preparing"] is None
    assert dj.ui_state()["nowPlaying"] is not None


@pytest.mark.asyncio
async def test_a_mood_change_with_nothing_playing_starts_the_music():
    dj = make_dj()
    await dj.set_mood("writing")
    assert dj.tx.sent("play"), "silence should not wait for a track boundary"


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
async def test_a_vibe_can_be_pinned_directly_not_just_an_activity():
    # The picker offers the profile's own lanes alongside the hook's activity
    # moods; pinning one has to select that lane, not fall back to a default.
    dj = make_dj()
    await dj.set_mood("loose", pinned=True, force=True)
    assert dj.lane == "loose"
    assert dj.queue["lane"] == "loose"
    terms = [c["term"] for c in dj.tx.sent("search")]
    assert any("Bon Entendeur" in t or "Gipsy" in t for t in terms)


@pytest.mark.asyncio
async def test_every_offered_vibe_maps_to_a_real_lane():
    for vibe in ["energized", "focus", "mellow", "loose", "tense"]:
        assert moods.lane_for(vibe) == vibe


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
async def test_previous_restarts_the_song_before_it_leaves_it():
    dj = make_dj()
    track = await dj.play_next()
    dj.current["position"] = 45000
    dj.tx.calls.clear()
    await dj.on_action({"action": "previous"})
    assert dj.tx.sent("seek") == [{"cmd": "seek", "position": 0}]
    assert dj.tx.sent("play") == [], "left a song 45 seconds in"
    assert dj.current["catalogId"] == track["catalogId"]


@pytest.mark.asyncio
async def test_previous_again_goes_back_a_song():
    # No click counter: the first press left the playhead at zero, so the
    # second one lands in the other branch by itself.
    dj = make_dj()
    first = await dj.play_next()
    second = await dj.play_next()
    dj.current["position"] = 45000
    await dj.on_action({"action": "previous"})       # restarts
    assert dj.current["catalogId"] == second["catalogId"]
    dj.tx.calls.clear()
    await dj.on_action({"action": "previous"})       # now goes back
    assert dj.tx.sent("play"), "the second press did not move"
    assert dj.current["catalogId"] == first["catalogId"]


@pytest.mark.asyncio
async def test_previous_within_the_first_seconds_goes_straight_back():
    dj = make_dj()
    first = await dj.play_next()
    await dj.play_next()
    dj.current["position"] = 1200
    dj.tx.calls.clear()
    await dj.on_action({"action": "previous"})
    assert dj.tx.sent("seek") == [], "restarted a song that had just begun"
    assert dj.current["catalogId"] == first["catalogId"]


@pytest.mark.asyncio
async def test_previous_with_nothing_playing_is_harmless():
    dj = make_dj()
    await dj.on_action({"action": "previous"})       # must not raise


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


# ------------------------------------------------------- first-run setup

BANGERS = [{"id": "p.one", "name": "🦅", "canEdit": True, "trackCount": 12},
           {"id": "p.two", "name": "gym", "canEdit": True, "trackCount": 40}]


@pytest.mark.asyncio
async def test_a_five_star_with_no_playlist_offers_the_picker():
    tx = FakeTransport()
    tx.playlists = BANGERS
    dj = make_dj(tx)
    track = await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    assert dj.ui_state()["setup"] == {"playlists": BANGERS}
    assert "choose a playlist" in dj.ui_state()["notice"]
    # The rating itself must not wait for the picker.
    assert library.rating_for(dj.ratings, track["catalogId"], "coding") == 5
    assert tx.sent("addToPlaylist") == []


@pytest.mark.asyncio
async def test_choosing_a_playlist_persists_it_and_adds_the_prompting_track():
    tx = FakeTransport()
    tx.playlists = BANGERS
    dj = make_dj(tx)
    track = await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    await dj.on_action({"action": "chooseStarred", "id": "p.one", "name": "🦅"})
    assert store.read_json(store.CONFIG, {})["starred_playlist"] == \
        {"id": "p.one", "name": "🦅"}
    assert dj.config["starred_playlist"]["id"] == "p.one"
    adds = tx.sent("addToPlaylist")
    assert adds and adds[0]["catalogId"] == track["catalogId"]
    assert dj.ui_state()["setup"] is None
    assert "added to 🦅" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_a_later_five_star_skips_the_picker_entirely():
    tx = FakeTransport()
    tx.playlists = BANGERS
    dj = make_dj(tx)
    await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    await dj.on_action({"action": "chooseStarred", "id": "p.one", "name": "🦅"})
    listings = len(tx.sent("listPlaylists"))
    await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    assert len(tx.sent("listPlaylists")) == listings
    assert dj.ui_state()["setup"] is None
    assert len(tx.sent("addToPlaylist")) == 2


@pytest.mark.asyncio
async def test_dismissing_the_picker_writes_nothing():
    tx = FakeTransport()
    tx.playlists = BANGERS
    dj = make_dj(tx)
    await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    await dj.on_action({"action": "dismissSetup"})
    assert dj.ui_state()["setup"] is None
    assert "starred_playlist" not in store.read_json(store.CONFIG, {})
    assert tx.sent("addToPlaylist") == []


@pytest.mark.asyncio
async def test_choosing_without_an_id_is_harmless():
    tx = FakeTransport()
    dj = make_dj(tx)
    await dj.on_action({"action": "chooseStarred", "id": None, "name": "x"})
    assert "starred_playlist" not in store.read_json(store.CONFIG, {})


@pytest.mark.asyncio
async def test_setup_with_the_extension_down_keeps_the_plain_notice():
    tx = FakeTransport()
    tx.playlists = BANGERS
    dj = make_dj(tx)
    await dj.play_next()
    tx.connected = False               # the tab died before the five-star
    await dj.on_action({"action": "rate", "stars": 5})
    assert dj.ui_state()["setup"] is None
    assert "no starred playlist" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_a_failed_playlist_listing_keeps_the_plain_notice():
    tx = FakeTransport()
    tx.fail_list = True
    dj = make_dj(tx)
    await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    assert dj.ui_state()["setup"] is None
    assert "no starred playlist" in dj.ui_state()["notice"]


@pytest.mark.asyncio
async def test_rating_with_nothing_playing_is_harmless():
    dj = make_dj()
    await dj.on_action({"action": "rate", "stars": 5})   # must not raise


# ------------------------------------------------- stars reaching the picker
#
# Each hop is unit-tested elsewhere. These walk the whole loop, because that
# is the claim the stars make: rate a song and the next batch knows.

def watched(dj):
    """Point the DJ at the real advisor and collect the prompts it writes.

    The runner stands in for the CLI and answers nothing, so every batch falls
    back to the profile -- what is being checked is what the picker was told,
    not what it picked.
    """
    from daemon import advisor
    prompts = []
    dj.picks_for = lambda mood, lane: advisor.picks_for(
        mood, lane, seeds=dj.seeds, rng=dj.rng,
        runner=lambda prompt, timeout: prompts.append(prompt) or "")
    return prompts


def section(prompt, heading):
    """The block under one '## …' heading, or "" if it is not there.

    Titles show up in several sections -- "just played" lists them all -- so
    which section a track lands in is the whole question.
    """
    start = prompt.find(heading)
    if start < 0:
        return ""
    rest = prompt[start:]
    end = rest.find("\n## ", 1)
    return rest if end < 0 else rest[:end]


@pytest.mark.asyncio
async def test_five_stars_reaches_the_prompt_that_picks_the_next_batch(
        isolated_storage):
    dj = make_dj()
    prompts = watched(dj)
    track = await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 5})
    prompts.clear()
    await dj.refill()

    assert prompts, "the next batch was picked without asking"
    loved = section(prompts[-1], "## They rated these 5 stars")
    assert track["title"] in loved, "the star never reached the picker"
    assert "Lean towards this register" in loved


@pytest.mark.asyncio
async def test_one_star_reaches_the_prompt_and_bars_the_track(isolated_storage):
    dj = make_dj()
    prompts = watched(dj)
    track = await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 1})
    assert track["catalogId"] not in [t["catalogId"]
                                      for t in library.queue_tracks(dj.queue)], \
        "a one-star track was left sitting in the queue"

    prompts.clear()
    await dj.refill()
    assert track["title"] in section(prompts[-1], "## They rated these 1 star")
    assert track["catalogId"] not in [t["catalogId"]
                                      for t in library.queue_tracks(dj.queue)]


@pytest.mark.asyncio
async def test_two_early_skips_reach_the_picker_as_a_verdict(isolated_storage):
    dj = make_dj()
    prompts = watched(dj)
    track = await dj.play_next()
    for _ in range(2):
        dj.current["position"] = 9000
        dj.current["duration"] = 200000
        await dj.on_action({"action": "skip"})
        dj.current = track                  # same song back under the needle
    prompts.clear()
    await dj.refill()
    assert track["title"] in section(prompts[-1], "## They skip these early")


@pytest.mark.asyncio
async def test_a_star_given_while_coding_reaches_the_research_prompt(
        isolated_storage):
    # Both draw from the focus lane. Kept apart by mood, a star given in one
    # was invisible in the other even though the music comes from one pool.
    dj = make_dj()
    prompts = watched(dj)
    track = await dj.play_next()                    # mood is "coding"
    await dj.on_action({"action": "rate", "stars": 5})
    dj.mood = "research"
    prompts.clear()
    await dj.refill()
    assert track["title"] in section(prompts[-1], "## They rated these 5 stars")


@pytest.mark.asyncio
async def test_an_artist_they_rate_here_is_offered_ahead_of_a_stranger():
    # The generalisation that makes a star worth giving: it has to change what
    # comes next, not just what that one song does if it ever returns.
    class TwoArtists(FakeTransport):
        async def call(self, cmd, timeout=None):
            self.calls.append(cmd)
            if cmd.get("cmd") == "search":
                artist = cmd["term"]
                return {"songs": [{"catalogId": artist + "-new", "title": "New",
                                   "artist": artist, "artworkUrl": None,
                                   "durationMs": 200000}]}
            return {"ok": True}

    dj = make_dj(TwoArtists())
    dj.picks_for = lambda mood, lane: [
        {"title": None, "artist": "Sofiane Pamart", "why": "w"},
        {"title": None, "artist": "Daft Punk", "why": "w"},
    ]
    dj.ratings = library.rate({}, {"catalogId": "old", "title": "Something",
                                   "artist": "Daft Punk"}, "coding", 5)
    await dj.refill()
    assert [t["artist"] for t in library.queue_tracks(dj.queue)][0] == "Daft Punk"


@pytest.mark.asyncio
async def test_an_artist_they_have_twice_rejected_here_is_not_offered_again():
    class FullCredit(FakeTransport):
        """Search that answers with the whole artist name, as Apple does."""
        async def call(self, cmd, timeout=None):
            self.calls.append(cmd)
            if cmd.get("cmd") == "search":
                return {"songs": [{"catalogId": "fresh", "title": "Unheard",
                                   "artist": cmd["term"], "artworkUrl": None,
                                   "durationMs": 200000}]}
            return {"ok": True}

    dj = make_dj(FullCredit())
    for cid in ("a", "b"):
        dj.ratings = library.rate(dj.ratings,
                                  {"catalogId": cid, "title": cid,
                                   "artist": "Al Green"}, "coding", 1)
    dj.picks_for = lambda mood, lane: [
        {"title": None, "artist": "Al Green", "why": "w"}]
    await dj.refill()
    assert library.queue_tracks(dj.queue) == [], \
        "offered a fresh song by an artist rejected twice in this lane"


@pytest.mark.asyncio
async def test_a_rating_given_in_one_mood_stays_out_of_another(isolated_storage):
    dj = make_dj()
    prompts = watched(dj)
    await dj.play_next()                            # mood is "coding"
    await dj.on_action({"action": "rate", "stars": 5})
    dj.mood = "loose"
    prompts.clear()
    await dj.refill()
    assert section(prompts[-1], "## They rated these 5 stars") == "", \
        "a verdict from another mood leaked into this one"


@pytest.mark.asyncio
async def test_a_rewritten_taste_profile_reaches_the_next_batch(isolated_storage):
    # The advisor re-reads the profile for every batch, so Claude's picks
    # followed a rewrite immediately. The fallback used seeds parsed once at
    # startup, so refreshing the profile changed nothing until a restart --
    # and the fallback is where you land whenever Claude is slow.
    dj = make_dj()
    assert "Bill Withers" in dj.seeds["tense"]
    (isolated_storage / store.PROFILE).write_text(
        "## Mood → seed directions\n\n"
        "- **tense / debugging** → warm soul: Lee Fields, Charles Bradley.\n",
        encoding="utf-8")
    os.utime(isolated_storage / store.PROFILE, (2e9, 2e9))
    dj.mood = "debugging"
    await dj.refill()
    terms = [c["term"] for c in dj.tx.sent("search")]
    assert any("Lee Fields" in t or "Charles Bradley" in t for t in terms)
    assert not any("Bill Withers" in t for t in terms), "picked from a stale profile"


@pytest.mark.asyncio
async def test_the_stars_survive_a_restart(isolated_storage):
    dj = make_dj()
    track = await dj.play_next()
    await dj.on_action({"action": "rate", "stars": 4})
    # A fresh DJ over the same storage is what tomorrow morning looks like.
    assert library.rating_for(make_dj().ratings, track["catalogId"], "coding") == 4


# --------------------------------------------------------------- signals

@pytest.mark.asyncio
async def test_an_early_skip_is_recorded_as_a_verdict():
    dj = make_dj()
    track = await dj.play_next()
    dj.current["position"] = 15000
    dj.current["duration"] = 200000
    await dj.on_action({"action": "skip"})
    m = store.read_json(store.SIGNALS, {})[track["catalogId"]]["byMood"]["coding"]
    assert m["skips"] == 1 and m["earlySkips"] == 1


@pytest.mark.asyncio
async def test_a_late_skip_is_not_held_against_the_track():
    dj = make_dj()
    track = await dj.play_next()
    dj.current["position"] = 180000
    dj.current["duration"] = 200000
    await dj.on_action({"action": "skip"})
    m = store.read_json(store.SIGNALS, {})[track["catalogId"]]["byMood"]["coding"]
    assert m["skips"] == 1 and m["earlySkips"] == 0


@pytest.mark.asyncio
async def test_a_full_listen_is_recorded_on_track_end():
    dj = make_dj()
    track = await dj.play_next()
    await dj.on_event({"evt": "trackEnded", "catalogId": track["catalogId"]})
    m = store.read_json(store.SIGNALS, {})[track["catalogId"]]["byMood"]["coding"]
    assert m["completes"] == 1


@pytest.mark.asyncio
async def test_the_new_tracks_early_events_are_not_a_user_skip():
    # Confirming a play takes seconds, and the commanded track's first
    # playback events can beat the reply. `current` still names the outgoing
    # track then; those events used to read as "the user changed the track"
    # and strike a spurious early skip against a song merely advanced past.
    tx = FakeTransport()
    dj = make_dj(tx)
    old = await dj.play_next()

    async def slow_play(cmd, timeout=None):
        if cmd.get("cmd") == "play":
            tx.calls.append(cmd)
            await asyncio.sleep(0.2)
            return {"ok": True, "state": 2}
        return await FakeTransport.call(tx, cmd, timeout)

    tx.call = slow_play
    advance = asyncio.create_task(dj.play_next())
    await asyncio.sleep(0.05)              # play sent, reply still pending
    new_id = dj._pending_play
    assert new_id and new_id != str(old["catalogId"])
    await dj.on_event({"evt": "playback", "catalogId": new_id,
                       "title": "Next", "artist": "Someone",
                       "position": 800, "duration": 200000, "state": 2})
    await advance
    m = (store.read_json(store.SIGNALS, {}).get(old["catalogId"], {})
         .get("byMood", {}).get("coding", {}))
    assert m.get("skips", 0) == 0, "the transition scored as a user skip"


@pytest.mark.asyncio
async def test_an_interrupted_tracks_end_is_not_a_completion():
    # An urgent mood change interrupts track A with a play for track B. The
    # player reports A as ended; scoring that as "finished" would inflate
    # completes (arguing a track back into favour it never earned), and
    # advancing on it would cut B off seconds after it began.
    tx = FakeTransport()
    dj = make_dj(tx)
    old = await dj.play_next()

    async def slow_play(cmd, timeout=None):
        if cmd.get("cmd") == "play":
            tx.calls.append(cmd)
            await asyncio.sleep(0.2)
            return {"ok": True, "state": 2}
        return await FakeTransport.call(tx, cmd, timeout)

    tx.call = slow_play
    advance = asyncio.create_task(dj.play_next())
    await asyncio.sleep(0.05)              # play for B in flight
    plays_during = len(tx.sent("play"))
    await dj.on_event({"evt": "trackEnded", "catalogId": old["catalogId"]})
    await advance
    await asyncio.sleep(0.05)              # a wrongly queued advance lands here
    m = (store.read_json(store.SIGNALS, {}).get(old["catalogId"], {})
         .get("byMood", {}).get("coding", {}))
    assert m.get("completes", 0) == 0, "an interrupted track scored as complete"
    assert len(tx.sent("play")) == plays_during, \
        "the old track's end triggered another advance"


@pytest.mark.asyncio
async def test_an_end_five_seconds_in_is_the_queue_swap_echoing():
    # Swapping the player's queue reports the teardown as an end, under the
    # name of the song that just started. The page gags that for a few
    # seconds; a slow swap outlives the gag, and the song then gets burned
    # five seconds in -- over and over, every track.
    dj = make_dj()
    track = await dj.play_next()
    dj.tx.calls.clear()
    await dj.on_event({"evt": "trackEnded", "catalogId": track["catalogId"],
                       "position": 5000, "duration": 200000})
    assert dj.tx.sent("play") == [], "burned a track five seconds in"
    assert dj.current["catalogId"] == track["catalogId"]
    m = (store.read_json(store.SIGNALS, {}).get(track["catalogId"], {})
         .get("byMood", {}).get("coding", {}))
    assert m.get("completes", 0) == 0, "five seconds scored as a full listen"


@pytest.mark.asyncio
async def test_a_song_that_played_through_still_advances():
    dj = make_dj()
    track = await dj.play_next()
    dj.tx.calls.clear()
    await dj.on_event({"evt": "trackEnded", "catalogId": track["catalogId"],
                       "position": 199000, "duration": 200000})
    assert dj.tx.sent("play"), "the music stopped at the end of a song"


def test_two_early_skips_shun_a_track_but_a_full_listen_redeems_it():
    now = 1000.0
    signals = {}
    track = {"catalogId": "c1", "title": "T", "artist": "A"}
    for _ in range(2):
        signals = library.record_signal(signals, track, "coding", "skip",
                                        10000, 200000, now)
    shunned = library.taste({}, signals, "focus", now)
    assert library.rank_by_taste(shunned, [track]) == []
    # Per lane: skipping it while coding says nothing about writing.
    assert library.rank_by_taste(
        library.taste({}, signals, "mellow", now), [track]) == [track]

    signals = library.record_signal(signals, track, "coding", "complete",
                                    200000, 200000, now)
    redeemed = library.taste({}, signals, "focus", now)
    assert library.rank_by_taste(redeemed, [track]) == [track]


def test_no_stars_is_neutral_not_negative():
    # A track with no rating and no skips must never be shunned or banned.
    view = library.taste({}, {}, "focus", 1000.0)
    track = {"catalogId": "c1", "artist": "Nobody Rated"}
    assert library.score_track(view, track) == 0
    assert library.rank_by_taste(view, [track]) == [track]


@pytest.mark.asyncio
async def test_the_mood_is_reread_at_each_song_boundary():
    dj = make_dj()
    track = await dj.play_next()
    store.write_json(store.STATE, {"current_mood": "writing"})
    await dj.on_event({"evt": "trackEnded", "catalogId": track["catalogId"]})
    assert dj.mood == "writing"
    assert dj.current is not None            # music kept going in the new mood


@pytest.mark.asyncio
async def test_a_pinned_mood_survives_the_song_boundary():
    dj = make_dj()
    track = await dj.play_next()
    dj.pinned = True
    store.write_json(store.STATE, {"current_mood": "writing"})
    await dj.on_event({"evt": "trackEnded", "catalogId": track["catalogId"]})
    assert dj.mood == "coding"


@pytest.mark.asyncio
async def test_songs_already_in_their_playlists_are_not_offered():
    dj = make_dj()
    dj.avoid_ids = {"cat1"}          # first search hit is already curated
    track = await dj.play_next()
    assert track["catalogId"] != "cat1"


@pytest.mark.asyncio
async def test_ready_learns_which_tracks_their_playlists_hold():
    tx = FakeTransport()
    tx.additions = [{"type": "album", "name": "New", "artist": "Someone"}]
    tx.playlists = BANGERS
    tx.playlist_tracks = ["cat9"]
    dj = make_dj(tx)
    await dj.on_event({"evt": "ready"})
    await asyncio.sleep(0.05)
    assert "cat9" in dj.avoid_ids
    assert "cat9" in store.read_json(store.PLAYLIST_TRACKS, {})["ids"]


@pytest.mark.asyncio
async def test_ready_refreshes_the_library_view_once():
    tx = FakeTransport()
    tx.additions = [{"type": "album", "name": "New Album", "artist": "Someone"}]
    dj = make_dj(tx)
    await dj.on_event({"evt": "ready"})
    await asyncio.sleep(0.05)
    snap = store.read_json(store.LIBRARY_RECENT, {})
    assert snap["items"] == tx.additions
    await dj.on_event({"evt": "ready"})      # a reload later
    await asyncio.sleep(0.05)
    assert len(tx.sent("recentlyAdded")) == 1


# ---------------------------------------------------------------- lyrics

@pytest.mark.asyncio
async def test_lyrics_are_fetched_for_the_current_track_and_cached():
    tx = FakeTransport()
    tx.ttml = "<tt><body><p begin='1s'>la la</p></body></tt>"
    dj = make_dj(tx)
    track = await dj.play_next()
    await dj.on_action({"action": "lyrics"})
    assert dj.ui_state()["lyrics"] == {"catalogId": track["catalogId"],
                                       "ttml": tx.ttml}
    await dj.on_action({"action": "lyrics"})       # second ask hits the cache
    assert len(tx.sent("lyrics")) == 1


@pytest.mark.asyncio
async def test_lyrics_are_dropped_when_the_track_changes():
    tx = FakeTransport()
    tx.ttml = "<tt><body><p>words</p></body></tt>"
    dj = make_dj(tx)
    await dj.play_next()
    await dj.on_action({"action": "lyrics"})
    await dj.play_next()
    assert dj.ui_state()["lyrics"] is None


@pytest.mark.asyncio
async def test_lyrics_with_nothing_playing_is_harmless():
    dj = make_dj()
    await dj.on_action({"action": "lyrics"})       # must not raise
    assert dj.ui_state()["lyrics"] is None


@pytest.mark.asyncio
async def test_a_track_without_lyrics_still_answers():
    tx = FakeTransport()                            # ttml stays None
    dj = make_dj(tx)
    track = await dj.play_next()
    await dj.on_action({"action": "lyrics"})
    assert dj.ui_state()["lyrics"] == {"catalogId": track["catalogId"],
                                       "ttml": None}


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


# ------------------------------------------------------------------ shutdown


# Marked explicitly (like every other async test here) instead of relying on
# pytest.ini's asyncio_mode = auto: from a rootdir that misses that ini, bare
# async tests error out instead of running.
@pytest.mark.asyncio
async def test_shutdown_event_pauses_broadcasts_and_sets_event():
    tx = FakeTransport()
    dj = make_dj(tx)
    dj.playing = True
    seen = []
    dj.subscribe(seen.append)

    await dj.on_event({"evt": "shutdown"})

    assert {"cmd": "pause"} in tx.calls
    assert {"shutdown": True} in seen
    assert dj.shutdown_event.is_set()


@pytest.mark.asyncio
async def test_shutdown_when_not_playing_skips_pause():
    tx = FakeTransport()
    dj = make_dj(tx)
    dj.playing = False

    await dj.on_event({"evt": "shutdown"})

    assert {"cmd": "pause"} not in tx.calls
    assert dj.shutdown_event.is_set()
