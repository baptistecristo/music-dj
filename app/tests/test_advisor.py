"""Claude picking, with the subprocess mocked.

The fallback paths matter more than the happy path here: the brief's rule is
that Claude makes the picks better but must never be why the music stops.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import advisor, library, moods  # noqa: E402

PROFILE = """
## Mood → seed directions

- **tense / debugging** → warm soul: Bill Withers, Al Green, Lee Fields.
- **loose / late night** → feel-good: Bon Entendeur, Gipsy Kings.
"""

SEEDS = moods.parse_seeds(PROFILE)

GOOD = json.dumps({"picks": [
    {"title": "Ain't No Sunshine", "artist": "Bill Withers", "why": "warm and slow"},
    {"title": "Cry To Me", "artist": "Solomon Burke", "why": "you rated this 5 here"},
]})


def reply(text):
    """A runner that returns whatever the CLI supposedly printed."""
    return lambda prompt, timeout: text


def raises(exc):
    def runner(prompt, timeout):
        raise exc
    return runner


# -------------------------------------------------------------------- prompt

def test_the_prompt_carries_the_profile_and_the_mood():
    prompt = advisor.build_prompt(PROFILE, "debugging", "tense", {}, {})
    assert "Bill Withers" in prompt          # the profile itself
    assert "debugging" in prompt and "tense" in prompt


def test_the_prompt_lists_recent_plays_so_they_are_not_repeated():
    history = library.remember_play(
        {}, {"catalogId": "1", "title": "Lovely Day", "artist": "Bill Withers"}, 1)
    prompt = advisor.build_prompt(PROFILE, "debugging", "tense", history, {})
    assert "Lovely Day — Bill Withers" in prompt
    assert "do not repeat" in prompt.lower()


def test_the_prompt_only_shows_ratings_from_this_mood():
    ratings = library.rate({}, {"catalogId": "1", "title": "Veridis Quo",
                                "artist": "Daft Punk"}, "debugging", 5)
    ratings = library.rate(ratings, {"catalogId": "2", "title": "Da Funk",
                                     "artist": "Daft Punk"}, "loose", 5)
    prompt = advisor.build_prompt(PROFILE, "debugging", "tense", {}, ratings)
    assert "Veridis Quo" in prompt
    assert "Da Funk" not in prompt, "leaked a rating from another mood"


def test_the_prompt_separates_loved_from_hated():
    ratings = library.rate({}, {"catalogId": "1", "title": "Good",
                                "artist": "A"}, "debugging", 5)
    ratings = library.rate(ratings, {"catalogId": "2", "title": "Bad",
                                     "artist": "B"}, "debugging", 1)
    prompt = advisor.build_prompt(PROFILE, "debugging", "tense", {}, ratings)
    loved = prompt.index("5 stars in this exact mood")
    hated = prompt.index("1 star in this exact mood")
    assert prompt.index("Good") > loved and prompt.index("Good") < hated
    assert prompt.index("Bad") > hated


def test_the_prompt_asks_for_json_only():
    prompt = advisor.build_prompt(PROFILE, "coding", "focus", {}, {})
    assert '{"picks":' in prompt
    assert "60 characters" in prompt


def test_the_prompt_survives_a_missing_profile():
    assert advisor.build_prompt("", "coding", "focus", {}, {})


# --------------------------------------------------------------- parsing

def test_plain_json_parses():
    assert advisor.extract_json(GOOD)["picks"][0]["artist"] == "Bill Withers"


def test_json_wrapped_in_chatter_parses():
    assert advisor.extract_json("Sure! Here you go:\n" + GOOD + "\nHope that helps")


def test_json_in_a_markdown_fence_parses():
    assert advisor.extract_json("```json\n" + GOOD + "\n```")


@pytest.mark.parametrize("text", ["", None, "no json here", "{broken", "}{"])
def test_unparseable_output_yields_nothing(text):
    assert advisor.extract_json(text) is None


# --------------------------------------------------------------- invocation

def test_the_prompt_goes_over_stdin_not_as_an_argument(monkeypatch):
    """On Windows `claude` is a .CMD, so it runs through cmd.exe, which cuts a
    multi-line argument off at the first newline. The model then answers a
    truncated question and the answer is unusable."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")

        class R:
            returncode = 0
            stdout = GOOD
        return R()

    monkeypatch.setattr(advisor.shutil, "which", lambda name: r"C:\claude.CMD")
    monkeypatch.setattr(advisor.subprocess, "run", fake_run)

    multiline = "line one\nline two"
    advisor._run_cli(multiline, 15)
    assert multiline not in seen["cmd"], "prompt passed as an argument"
    assert seen["input"] == multiline, "prompt did not go over stdin"


def test_a_missing_executable_is_reported_as_missing(monkeypatch):
    monkeypatch.setattr(advisor.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        advisor._run_cli("hello", 15)


def test_the_timeout_allows_for_a_real_call():
    # Measured at ~17s against the live CLI with the full profile. Anything at
    # or under that guarantees the fallback fires every time.
    assert advisor.TIMEOUT >= 30


# ------------------------------------------------------------- fallbacks

def test_claude_picks_are_used_when_they_arrive():
    picks = advisor.picks_for("debugging", "tense", seeds=SEEDS, history={},
                              ratings={}, profile=PROFILE, runner=reply(GOOD))
    assert [p["title"] for p in picks] == ["Ain't No Sunshine", "Cry To Me"]
    assert picks[0]["source"] == "claude"
    assert picks[0]["why"] == "warm and slow"


def test_a_missing_cli_falls_back_to_the_profile():
    picks = advisor.picks_for("debugging", "tense", seeds=SEEDS, history={},
                              ratings={}, profile=PROFILE,
                              runner=raises(FileNotFoundError()))
    assert picks and all(p["source"] == "profile" for p in picks)
    assert {p["artist"] for p in picks} <= set(SEEDS["tense"])


def test_a_timeout_falls_back_to_the_profile():
    picks = advisor.picks_for(
        "debugging", "tense", seeds=SEEDS, history={}, ratings={},
        profile=PROFILE, runner=raises(subprocess.TimeoutExpired("claude", 15)))
    assert picks and picks[0]["source"] == "profile"


def test_a_crash_falls_back_to_the_profile():
    picks = advisor.picks_for("debugging", "tense", seeds=SEEDS, history={},
                              ratings={}, profile=PROFILE,
                              runner=raises(RuntimeError("boom")))
    assert picks and picks[0]["source"] == "profile"


@pytest.mark.parametrize("output", [
    "", "I'd love to help but I need more context",
    '{"picks": []}', '{"picks": "nope"}', '{"wrong": []}',
    '{"picks":[{"title":"only a title"}]}',
])
def test_unusable_answers_fall_back_to_the_profile(output):
    picks = advisor.picks_for("debugging", "tense", seeds=SEEDS, history={},
                              ratings={}, profile=PROFILE, runner=reply(output))
    assert picks, "the music stopped because claude was unhelpful"
    assert picks[0]["source"] == "profile"


def test_the_fallback_states_an_honest_reason():
    picks = advisor.picks_for("debugging", "tense", seeds=SEEDS, history={},
                              ratings={}, profile=PROFILE, runner=reply(""))
    assert "profile" in picks[0]["why"]


def test_a_partly_usable_answer_keeps_what_it_can():
    mixed = json.dumps({"picks": [
        {"title": "Good", "artist": "Artist", "why": "fits"},
        {"title": "Bad"},
    ]})
    picks = advisor.picks_for("debugging", "tense", seeds=SEEDS, history={},
                              ratings={}, profile=PROFILE, runner=reply(mixed))
    assert [p["title"] for p in picks] == ["Good"]


def test_nothing_anywhere_returns_empty_rather_than_raising():
    # No profile seeds and no Claude: there is genuinely nothing to play, and
    # the caller has to hear about that as an empty list, not an exception.
    assert advisor.picks_for("debugging", "tense", seeds={}, history={},
                             ratings={}, profile="", runner=reply("")) == []
