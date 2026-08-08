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
from daemon import hotkey  # noqa: E402


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


def test_the_default_key_parses():
    mods, vk = hotkey.parse("alt+j")
    assert mods == hotkey.MOD_ALT | hotkey.MOD_NOREPEAT
    assert vk == ord("J")


def test_holding_the_key_is_one_press_not_thirty():
    # Without MOD_NOREPEAT, Windows re-fires WM_HOTKEY at the keyboard's
    # repeat rate for as long as you hold it, so one sentence reads as
    # dozens of presses.
    mods, _ = hotkey.parse("alt+j")
    assert mods & hotkey.MOD_NOREPEAT


def test_modifiers_combine_in_any_order():
    assert hotkey.parse("ctrl+shift+j") == hotkey.parse("shift+ctrl+j")


def test_a_spec_with_no_key_is_refused():
    assert hotkey.parse("alt") is None
    assert hotkey.parse("") is None
    assert hotkey.parse(None) is None


def test_a_spec_with_two_letters_is_refused():
    # RegisterHotKey takes modifiers plus one key. Two letters need a
    # low-level hook, and Alt+D would fire before the J arrived.
    assert hotkey.parse("alt+d+j") is None


def test_win_combo_parses():
    mods, vk = hotkey.parse("win+j")
    assert mods == hotkey.MOD_WIN | hotkey.MOD_NOREPEAT
    assert vk == ord("J")


def test_wanted_set_includes_win():
    # _wanted() once checked only alt/ctrl/shift, so a combo parsed from
    # "win+j" needed no modifier at all on macOS and Linux -- plain j would
    # open the microphone.
    mods, vk = hotkey.parse("win+j")
    wanted = hotkey._PynputHotkey(mods, vk, None, None)._wanted()
    assert wanted == {"j", "cmd"}


def test_wanted_set_for_a_named_key():
    # chr(vk) for scrolllock is an unprintable control character, not
    # pynput's own name for the key, so the two would never compare equal
    # and the hotkey would silently never fire.
    mods, vk = hotkey.parse("ctrl+scrolllock")
    wanted = hotkey._PynputHotkey(mods, vk, None, None)._wanted()
    assert wanted == {"scroll_lock", "ctrl"}


def test_wanted_set_for_a_multi_modifier_combo():
    mods, vk = hotkey.parse("ctrl+shift+j")
    wanted = hotkey._PynputHotkey(mods, vk, None, None)._wanted()
    assert wanted == {"j", "ctrl", "shift"}
