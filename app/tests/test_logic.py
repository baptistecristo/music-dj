"""Unit tests for the daemon's pure logic -- no browser, no sockets.

    python -m pytest tests/ -q
"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import library, moods, picker  # noqa: E402
from daemon.server import bridge_origin_allowed, ui_origin_allowed  # noqa: E402


# ------------------------------------------------------------------- origins

@pytest.mark.parametrize("origin", [
    "chrome-extension://cpoilmekfofffkmcblfelcdnndkbagom",
    "moz-extension://abc",
    "http://127.0.0.1:27628",     # the overlay: pywebview picks a new port each run
    "http://localhost:5000",
    "file:///C:/overlay/index.html",
    None,                         # every non-browser client
    "",
])
def test_the_ui_accepts_its_own_clients(origin):
    assert ui_origin_allowed(origin) is True


@pytest.mark.parametrize("origin", [
    "https://evil.com",
    "https://music.apple.com",    # the player page is not a control surface
    "http://127.0.0.1.evil.com",  # a registrable domain, not loopback
    "http://localhost.evil.com",
])
def test_the_daemon_turns_away_web_pages(origin):
    # Browsers do not apply same-origin policy to WebSockets, so any page the
    # user has open could otherwise reach ws://127.0.0.1 and drive the music.
    assert ui_origin_allowed(origin) is False
    assert bridge_origin_allowed(origin) is False


@pytest.mark.parametrize("origin", [
    "chrome-extension://cpoilmekfofffkmcblfelcdnndkbagom",
    "moz-extension://abc",
    None,                         # every non-browser client (the test driver)
    "",
])
def test_the_bridge_accepts_extensions_and_tools(origin):
    assert bridge_origin_allowed(origin) is True


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:27628",     # local pages may watch /ui, never drive /bridge
    "http://localhost:5000",
    "file:///C:/overlay/index.html",
])
def test_the_bridge_turns_away_local_pages(origin):
    # Whoever holds /bridge answers every command and can shut the daemon
    # down; only an extension (or a non-browser client) may be the transport.
    assert bridge_origin_allowed(origin) is False


def test_the_bridge_can_be_pinned_to_extension_ids():
    ours = "chrome-extension://cpoilmekfofffkmcblfelcdnndkbagom"
    other = "chrome-extension://aaaabbbbccccddddeeeeffffgggghhhh"
    ids = ("cpoilmekfofffkmcblfelcdnndkbagom",)
    assert bridge_origin_allowed(ours, ids) is True
    assert bridge_origin_allowed(other, ids) is False
    # An empty Origin can only be a non-browser client: browsers always send
    # one for extensions, so pinning must not lock out the test driver.
    assert bridge_origin_allowed(None, ids) is True


PROFILE = """
# Taste profile

## The shape of the library

- French rap is the biggest shelf.

## Mood → seed directions

- **energized / shipping something** → French touch and funk-house:
  Folamour, Bellaire, Dabeull, Polo & Pan;
  or upbeat soul — Earth, Wind & Fire, Curtis Harding.
- **tense / debugging / frustrated** → warm slow soul and blues, never
  aggressive: Bill Withers, Donny Hathaway, Al Green.
- **locked in / deep focus** → low-vocal and instrumental-leaning:
  Daft Punk (*Veridis Quo* register), Sofiane Pamart.
- **mellow / writing / reflective** → chanson: Francis Cabrel, Barbara.
- **loose / late night / celebrating** → feel-good: Bon Entendeur, Gipsy Kings.

## Don'ts

- Don't reach for metal.
"""


# ------------------------------------------------------------------- moods

def test_plugin_moods_map_onto_profile_lanes():
    assert moods.lane_for("building") == "energized"
    assert moods.lane_for("debugging") == "tense"
    assert moods.lane_for("coding") == "focus"
    assert moods.lane_for("writing") == "mellow"
    assert moods.lane_for("research") == "focus"


def test_lane_names_pass_through_and_unknowns_fall_back():
    assert moods.lane_for("loose") == "loose"
    assert moods.lane_for("nonsense") == moods.DEFAULT_LANE
    assert moods.lane_for(None) == moods.DEFAULT_LANE
    assert moods.lane_for("") == moods.DEFAULT_LANE


def test_parse_seeds_finds_every_lane():
    seeds = moods.parse_seeds(PROFILE)
    assert set(seeds) == {"energized", "tense", "focus", "mellow", "loose"}


def test_parse_seeds_strips_prose_that_wraps_across_lines():
    # "warm slow soul and blues, never / aggressive:" straddles a line break.
    # Parsing line by line used to leak it in as an artist name.
    assert moods.parse_seeds(PROFILE)["tense"] == [
        "Bill Withers", "Donny Hathaway", "Al Green"]


def test_parse_seeds_keeps_bands_whose_name_contains_a_comma():
    energized = moods.parse_seeds(PROFILE)["energized"]
    assert "Earth, Wind & Fire" in energized
    # ...without fusing two genuinely separate artists that look the same.
    assert "Dabeull" in energized and "Polo & Pan" in energized


def test_parse_seeds_drops_parenthetical_asides():
    assert moods.parse_seeds(PROFILE)["focus"] == ["Daft Punk", "Sofiane Pamart"]


def test_parse_seeds_survives_a_profile_it_cannot_read():
    assert moods.parse_seeds("") == {}
    assert moods.parse_seeds("# nothing here") == {}
    assert moods.parse_seeds("## Mood → seed directions\n\nfreeform prose") == {}


# ------------------------------------------------------------------ history

def test_history_records_newest_first_and_trims():
    hist = {}
    for i in range(library.HISTORY_LIMIT + 25):
        hist = library.remember_play(hist, {"catalogId": str(i)}, now=i)
    assert len(hist["plays"]) == library.HISTORY_LIMIT
    assert hist["plays"][0]["catalogId"] == str(library.HISTORY_LIMIT + 24)


def test_recent_ids_respects_the_window():
    hist = {}
    for i in range(50):
        hist = library.remember_play(hist, {"catalogId": str(i)}, now=i)
    assert len(library.recent_ids(hist, window=30)) == 30
    assert library.recent_ids(hist, window=30)[0] == "49"


# -------------------------------------------------------------------- queue

def test_dedupe_drops_recently_played():
    hist = library.remember_play({}, {"catalogId": "a"}, now=1)
    picks = [{"catalogId": "a"}, {"catalogId": "b"}]
    assert library.dedupe_picks(picks, hist) == [{"catalogId": "b"}]


def test_dedupe_drops_repeats_within_the_same_batch():
    picks = [{"catalogId": "x"}, {"catalogId": "x"}, {"catalogId": "y"}]
    assert [p["catalogId"] for p in library.dedupe_picks(picks, {})] == ["x", "y"]


def test_dedupe_drops_tracks_already_queued():
    picks = [{"catalogId": "a"}, {"catalogId": "b"}]
    queued = [{"catalogId": "a"}]
    assert library.dedupe_picks(picks, {}, already_queued=queued) == [{"catalogId": "b"}]


def test_dedupe_drops_unresolved_picks():
    assert library.dedupe_picks([{"title": "x"}, {"catalogId": None}], {}) == []


def test_a_track_played_long_ago_is_allowed_back():
    hist = {}
    hist = library.remember_play(hist, {"catalogId": "old"}, now=0)
    for i in range(40):
        hist = library.remember_play(hist, {"catalogId": "f%d" % i}, now=i + 1)
    assert library.dedupe_picks([{"catalogId": "old"}], hist, window=30) != []


def test_advance_pops_the_head():
    q = library.make_queue([{"catalogId": "1"}, {"catalogId": "2"}],
                           "coding", "focus", "profile", now=0)
    track, rest = library.advance(q)
    assert track["catalogId"] == "1"
    assert [t["catalogId"] for t in library.queue_tracks(rest)] == ["2"]


def test_advance_on_an_empty_queue_is_not_an_error():
    track, rest = library.advance(library.make_queue([], "coding", "focus", "profile", 0))
    assert track is None and library.queue_tracks(rest) == []


def test_refill_triggers_at_three_remaining():
    def q(n):
        return library.make_queue([{"catalogId": str(i)} for i in range(n)],
                                  "coding", "focus", "profile", 0)
    assert library.needs_refill(q(4)) is False
    assert library.needs_refill(q(3)) is True
    assert library.needs_refill(q(0)) is True


def test_queue_keeps_the_why_attached_to_each_track():
    tracks = [{"catalogId": "1", "why": "French touch — you rated Bellaire 5 here"}]
    q = library.make_queue(tracks, "building", "energized", "claude", now=0)
    assert library.advance(q)[0]["why"] == "French touch — you rated Bellaire 5 here"


# ------------------------------------------------------------------ ratings

TRACK = {"catalogId": "1556503755", "title": "The Journey", "artist": "Folamour"}


def test_ratings_are_scoped_per_mood():
    r = library.rate({}, TRACK, "debugging", 1)
    r = library.rate(r, TRACK, "energized", 5)
    assert r["1556503755"]["byMood"] == {"debugging": 1, "energized": 5}


def test_one_star_in_one_lane_does_not_ban_a_track_elsewhere():
    r = library.rate({}, TRACK, "debugging", 1)
    here = library.taste(r, {}, "tense", NOW)
    elsewhere = library.taste(r, {}, "energized", NOW)
    assert library.rank_by_taste(here, [TRACK]) == []
    assert library.rank_by_taste(elsewhere, [TRACK]) == [TRACK]


def test_rating_stores_title_and_artist_for_the_claude_prompt():
    r = library.rate({}, TRACK, "energized", 5)
    assert library.rated_in_lane(r, "energized", 5)[0]["artist"] == "Folamour"
    assert library.rated_in_lane(r, "energized", 1) == []


def test_rerating_replaces_within_that_mood_only():
    r = library.rate({}, TRACK, "energized", 5)
    r = library.rate(r, TRACK, "debugging", 2)
    r = library.rate(r, TRACK, "energized", 3)
    assert r["1556503755"]["byMood"] == {"energized": 3, "debugging": 2}


def test_zero_stars_clears_the_rating_for_that_mood():
    r = library.rate({}, TRACK, "energized", 5)
    r = library.rate(r, TRACK, "energized", 0)
    assert library.rating_for(r, "1556503755", "energized") == 0
    assert "1556503755" not in r          # no empty husk left behind


def test_rating_is_clamped_and_unresolved_tracks_are_ignored():
    assert library.rate({}, TRACK, "energized", 99)["1556503755"]["byMood"]["energized"] == 5
    assert library.rate({}, {"title": "no id"}, "energized", 5) == {}


# ------------------------------------------------------------------- picker

SEEDS = moods.parse_seeds(PROFILE)


def test_profile_batch_fills_the_batch_from_the_right_lane():
    picks = picker.profile_batch(SEEDS, "tense", count=3, rng=random.Random(0))
    assert len(picks) == 3
    assert {p["artist"] for p in picks} <= set(SEEDS["tense"])


def test_profile_batch_states_an_honest_reason():
    why = picker.profile_batch(SEEDS, "tense", count=1, rng=random.Random(0))[0]["why"]
    assert "profile" in why and "tense" in why


def test_profile_batch_prefers_artists_not_heard_recently():
    picks = picker.profile_batch(SEEDS, "tense", count=2,
                                 avoid_artists=["Bill Withers", "Al Green"],
                                 rng=random.Random(1))
    assert picks[0]["artist"] == "Donny Hathaway"


def test_profile_batch_cycles_rather_than_running_dry():
    # The lane has 2 artists but the batch wants 12; it must still fill.
    picks = picker.profile_batch(SEEDS, "mellow", count=12, rng=random.Random(0))
    assert len(picks) == 12


def test_profile_batch_falls_back_to_any_lane_when_the_lane_is_unknown():
    assert picker.profile_batch(SEEDS, "nonexistent", count=2, rng=random.Random(0))


def test_profile_batch_with_no_seeds_returns_nothing_rather_than_crashing():
    assert picker.profile_batch({}, "tense", count=5) == []


def test_search_term_prefers_artist_and_title_together():
    assert picker.pick_term({"artist": "Folamour", "title": "The Journey"}) == \
        "Folamour The Journey"
    assert picker.pick_term({"artist": "Folamour", "title": None}) == "Folamour"


# ----------------------------------------------------- claude output guarding

def test_valid_claude_output_is_accepted():
    picks = picker.validate_claude_picks(
        {"picks": [{"title": "The Journey", "artist": "Folamour", "why": "French touch"}]})
    assert picks == [{"title": "The Journey", "artist": "Folamour",
                      "why": "French touch", "source": "claude"}]


@pytest.mark.parametrize("payload", [
    None, {}, [], "not json", {"picks": "nope"}, {"picks": None},
    {"picks": [{"title": "only a title"}]},
    {"picks": [{"artist": "only an artist"}]},
    {"picks": [{"title": "", "artist": ""}]},
    {"picks": [{"title": 5, "artist": 6}]},
    {"wrong_key": [{"title": "a", "artist": "b"}]},
])
def test_malformed_claude_output_yields_no_picks(payload):
    assert picker.validate_claude_picks(payload) == []


def test_partial_claude_output_keeps_the_usable_picks():
    picks = picker.validate_claude_picks({"picks": [
        {"title": "Good", "artist": "Artist", "why": "because"},
        {"title": "Bad"},
        {"title": "Also Good", "artist": "Other"},
    ]})
    assert [p["title"] for p in picks] == ["Good", "Also Good"]


def test_a_pick_with_no_reason_says_so_rather_than_inventing_one():
    picks = picker.validate_claude_picks({"picks": [{"title": "T", "artist": "A"}]})
    assert picks[0]["why"] == "picked for this mood"


def test_an_overlong_reason_is_truncated_for_the_strip():
    picks = picker.validate_claude_picks(
        {"picks": [{"title": "T", "artist": "A", "why": "x" * 300}]})
    assert len(picks[0]["why"]) <= 80


# ------------------------------------------------------ resolving to a track

# --------------------------------------------------------------- taste
#
# The point of all of it: a star given today has to change what gets offered
# tomorrow, including songs that have never come up before.

NOW = 1_700_000_000.0
DAY = 86400.0


def rated(cid, title, artist, mood, stars):
    return library.rate({}, {"catalogId": cid, "title": title,
                             "artist": artist}, mood, stars)


def skipped(cid, title, artist, mood, times, at=NOW):
    signals = {}
    for _ in range(times):
        signals = library.record_signal(
            signals, {"catalogId": cid, "title": title, "artist": artist},
            mood, "skip", 5000, 200000, at)
    return signals


def test_a_star_given_while_coding_counts_while_researching():
    # Both are the focus lane, and the queue is built per lane. Kept apart,
    # five labels shared out the evidence until no batch ever saw any.
    view = library.taste(rated("c1", "T", "A", "coding", 5), {}, "focus", NOW)
    assert view["tracks"]["c1"] > 0
    assert library.score_track(view, {"catalogId": "c1", "artist": "A"}) > 0


def test_a_star_given_while_coding_says_nothing_about_a_late_night():
    view = library.taste(rated("c1", "T", "A", "coding", 5), {}, "loose", NOW)
    assert view["tracks"] == {} and view["artists"] == {}


def test_a_verdict_carries_to_the_artists_other_songs():
    # The whole reason this exists: you meet the same track twice a year, so a
    # per-track verdict is spent on a track that never comes back.
    view = library.taste(rated("c1", "Known", "Al Green", "coding", 5), {},
                         "focus", NOW)
    unheard = {"catalogId": "never-played", "artist": "Al Green"}
    assert library.score_track(view, unheard) > 0


def test_the_artists_claim_is_weaker_than_a_verdict_on_the_song():
    view = library.taste(rated("c1", "Known", "Al Green", "coding", 5), {},
                         "focus", NOW)
    known = library.score_track(view, {"catalogId": "c1", "artist": "Al Green"})
    unheard = library.score_track(view, {"catalogId": "x", "artist": "Al Green"})
    assert known > unheard > 0


def test_two_one_stars_stop_the_artist_being_offered_in_that_lane():
    ratings = rated("c1", "One", "Someone", "coding", 1)
    ratings = library.rate(ratings, {"catalogId": "c2", "title": "Two",
                                     "artist": "Someone"}, "coding", 1)
    view = library.taste(ratings, {}, "focus", NOW)
    fresh = [{"catalogId": "c3", "artist": "Someone", "title": "Three"}]
    assert library.rank_by_taste(view, fresh) == []


def test_one_bad_afternoon_is_not_a_verdict_on_the_artist():
    view = library.taste(rated("c1", "One", "Someone", "coding", 1), {},
                         "focus", NOW)
    fresh = [{"catalogId": "c3", "artist": "Someone"}]
    assert library.rank_by_taste(view, fresh) == fresh, "banned on one rating"


def test_one_songs_own_record_is_not_also_evidence_about_its_artist():
    # The artist term exists to say something about the songs we have NOT
    # heard a verdict on. Counting the song's own record inside it too made a
    # single track's bad run read as a pattern across the artist -- enough to
    # bury a track that its own full listen should have redeemed.
    signals = skipped("c1", "T", "A", "coding", 2)
    signals = library.record_signal(signals, {"catalogId": "c1", "title": "T",
                                              "artist": "A"},
                                    "coding", "complete", 200000, 200000, NOW)
    view = library.taste({}, signals, "focus", NOW)
    track = {"catalogId": "c1", "artist": "A"}
    assert library.score_track(view, track) == view["tracks"]["c1"], \
        "the song's own record was counted twice"
    assert library.rank_by_taste(view, [track]) == [track]


def test_an_old_skip_weighs_less_than_a_recent_one():
    recent = library.taste({}, skipped("c1", "T", "A", "coding", 2, NOW),
                           "focus", NOW)
    stale = library.taste({}, skipped("c1", "T", "A", "coding", 2,
                                      NOW - 180 * DAY), "focus", NOW)
    assert stale["tracks"]["c1"] > recent["tracks"]["c1"]
    assert stale["tracks"]["c1"] < 0, "age turned a verdict into approval"


def test_a_verdict_with_no_date_stands_still_rather_than_vanishing():
    # Rows written before signals carried a timestamp. Discarding them would
    # throw away somebody's history on an upgrade.
    assert library.decayed(-2.0, None, NOW) == -2.0


def test_silence_is_neutral_and_leaves_the_pickers_order_alone():
    view = library.taste({}, {}, "focus", NOW)
    picks = [{"catalogId": "a", "artist": "One"},
             {"catalogId": "b", "artist": "Two"},
             {"catalogId": "c", "artist": "Three"}]
    assert library.rank_by_taste(view, picks) == picks


def test_what_they_rated_highest_is_offered_first():
    ratings = rated("b", "Loved", "Two", "coding", 5)
    view = library.taste(ratings, {}, "focus", NOW)
    picks = [{"catalogId": "a", "artist": "One"},
             {"catalogId": "b", "artist": "Two"}]
    assert [t["catalogId"] for t in library.rank_by_taste(view, picks)] == ["b", "a"]


def test_a_full_listen_counts_for_less_than_a_star():
    listened = library.record_signal({}, {"catalogId": "c1", "title": "T",
                                          "artist": "A"},
                                     "coding", "complete", 200000, 200000, NOW)
    played = library.taste({}, listened, "focus", NOW)["tracks"]["c1"]
    starred = library.taste(rated("c1", "T", "A", "coding", 5), {},
                            "focus", NOW)["tracks"]["c1"]
    assert 0 < played < starred, "leaving it on read as loving it"


def test_a_verdict_carries_across_how_apple_spells_the_credit():
    # The rated track came back as "Daft Punk" and the next one as "Daft Punk
    # feat. Julian Casablancas". Matched literally the star would not carry,
    # which is the one thing it is for.
    view = library.taste(rated("c1", "T", "Daft Punk", "coding", 5), {},
                         "focus", NOW)
    for spelling in ["Daft Punk feat. Julian Casablancas",
                     "Daft Punk & Pharrell Williams", "daft punk"]:
        assert library.score_track(view, {"catalogId": "x",
                                          "artist": spelling}) > 0, spelling


def test_a_short_name_is_not_matched_by_coincidence():
    # "Air" sits inside "Airbourne" and a hundred other strings.
    view = library.taste(rated("c1", "T", "Air", "coding", 1), {}, "focus", NOW)
    assert library.score_track(view, {"catalogId": "x",
                                      "artist": "Airbourne"}) == 0


def test_the_definite_article_is_not_a_different_band():
    view = library.taste(rated("c1", "T", "The Beatles", "coding", 5), {},
                         "focus", NOW)
    assert library.score_track(view, {"catalogId": "x", "artist": "Beatles"}) > 0


def test_the_artist_lists_name_who_lands_and_who_does_not():
    ratings = rated("c1", "Good", "Bill Withers", "coding", 5)
    ratings = library.rate(ratings, {"catalogId": "c2", "title": "Bad",
                                     "artist": "Noisy Band"}, "coding", 1)
    liked, disliked = library.artists_by_verdict(
        library.taste(ratings, {}, "focus", NOW))
    # Spelled as the store has them, not flattened into the match key.
    assert "Bill Withers" in liked and "Noisy Band" not in liked
    assert "Noisy Band" in disliked and "Bill Withers" not in disliked


# ------------------------------------------------- a swap echo vs a real end

def test_an_end_seconds_into_a_long_track_is_an_echo():
    assert library.ended_too_early({"position": 5000, "duration": 200000})


def test_an_end_at_the_end_of_a_track_is_real():
    assert not library.ended_too_early({"position": 199000, "duration": 200000})


def test_a_thirty_second_preview_ending_at_thirty_seconds_is_real():
    # Small in absolute terms, but the item had nowhere further to go.
    assert not library.ended_too_early({"position": 29500, "duration": 30000})


def test_an_end_from_a_page_that_does_not_report_the_playhead_is_trusted():
    # Older page scripts send no position at all. Guessing "echo" there would
    # stop the music for good; guessing "real" only risks the old symptom.
    assert not library.ended_too_early({"catalogId": "c1"})


def test_a_long_way_in_with_no_duration_known_is_real():
    assert not library.ended_too_early({"position": 120000})


# ------------------------------------------- the floor learns as well

SEED_LANE = {"focus": ["Loved Act", "Plain Act", "Rejected Act"]}


def taste_for(pairs):
    """A taste view built straight from (artist, stars) pairs."""
    ratings = {}
    for i, (artist, stars) in enumerate(pairs):
        ratings = library.rate(ratings, {"catalogId": "c%d" % i,
                                         "title": "T%d" % i, "artist": artist},
                               "coding", stars)
    return library.taste(ratings, {}, "focus", NOW)


def test_the_profile_floor_leads_with_an_artist_they_rate_here():
    view = taste_for([("Loved Act", 5)])
    picks = picker.profile_batch(SEED_LANE, "focus", count=3,
                                 rng=random.Random(0), taste=view)
    assert picks[0]["artist"] == "Loved Act"


def test_the_profile_floor_drops_a_seed_the_lane_has_ruled_out():
    view = taste_for([("Rejected Act", 1), ("Rejected Act", 1)])
    picks = picker.profile_batch(SEED_LANE, "focus", count=6,
                                 rng=random.Random(0), taste=view)
    assert picks, "the floor went silent"
    assert "Rejected Act" not in [p["artist"] for p in picks]


def test_the_floor_would_rather_play_an_unloved_seed_than_nothing():
    # Every seed in the lane ruled out. Silence is worse than an imperfect
    # song, and the profile is all there is to fall back on.
    view = taste_for([(a, 1) for a in SEED_LANE["focus"] for _ in (0, 1)])
    picks = picker.profile_batch(SEED_LANE, "focus", count=3,
                                 rng=random.Random(0), taste=view)
    assert picks, "ruled out every seed and played nothing"


def test_the_floor_still_varies_when_it_has_learned_nothing():
    empty = library.taste({}, {}, "focus", NOW)
    first = [p["artist"] for p in picker.profile_batch(
        SEED_LANE, "focus", count=3, rng=random.Random(1), taste=empty)]
    second = [p["artist"] for p in picker.profile_batch(
        SEED_LANE, "focus", count=3, rng=random.Random(7), taste=empty)]
    assert first != second, "the floor became deterministic"


def test_resolution_prefers_the_artist_we_asked_for():
    songs = [{"catalogId": "1", "artist": "Tribute Band", "title": "Veridis Quo"},
             {"catalogId": "2", "artist": "Daft Punk", "title": "Veridis Quo"}]
    assert picker.choose_resolution(songs, preferred_artist="Daft Punk")["catalogId"] == "2"


def test_resolution_skips_tracks_played_recently():
    songs = [{"catalogId": "1", "artist": "A"}, {"catalogId": "2", "artist": "A"}]
    assert picker.choose_resolution(songs, exclude_ids=["1"])["catalogId"] == "2"


def test_resolution_returns_nothing_when_everything_is_excluded():
    assert picker.choose_resolution([{"catalogId": "1"}], exclude_ids=["1"]) is None
    assert picker.choose_resolution([]) is None
