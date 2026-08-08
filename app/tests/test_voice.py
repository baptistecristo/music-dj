"""Voice steering, with the microphone and the model both absent.

Nothing here opens an input device or loads Whisper. The parts worth testing
are the decisions -- what counts as speech, what the key spells, what happens
when transcription raises -- and every one of those is reachable with the
audio faked.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import listen  # noqa: E402


def test_silence_is_not_worth_acting_on():
    assert not listen.usable("", 0.0)
    assert not listen.usable("   ", 0.0)
    assert not listen.usable(None, 0.0)


def test_a_confident_phrase_is_worth_acting_on():
    assert listen.usable("un truc plus calme", 0.1)


def test_a_cough_is_not_worth_acting_on():
    # Whisper answers a knocked desk with a plausible-looking phrase and a
    # high no-speech probability. Acting on it advances the track for nothing.
    assert not listen.usable("Sous-titres réalisés par", 0.9)


def test_a_missing_probability_is_taken_at_face_value():
    # Some backends do not report one. Refusing everything then would make
    # the feature look broken rather than cautious.
    assert listen.usable("plus calme", None)


def test_voice_is_off_when_the_packages_are_absent(monkeypatch):
    monkeypatch.setattr(listen, "_import_audio", lambda: None)
    assert listen.available() is False
