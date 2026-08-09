"""Voice steering, with the microphone and the model both absent.

Nothing here opens an input device or loads Whisper. The parts worth testing
are the decisions -- what counts as speech, what the key spells, what happens
when transcription raises -- and every one of those is reachable with the
audio faked.
"""

import asyncio
import logging
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import listen  # noqa: E402
from daemon import hotkey  # noqa: E402
from daemon import voice  # noqa: E402


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


class FakeKey:
    """A pynput KeyCode: the character the layout made, and the key that
    made it. No name, which is how pynput tells letters from Key members."""

    name = None

    def __init__(self, char, vk):
        self.char, self.vk = char, vk


class FakeModifier:
    """A pynput Key member, which carries a name and no character."""

    char = None

    def __init__(self, name):
        self.name = name


def test_option_j_still_matches_the_letter_on_macos():
    # macOS composes Option+J into a Greek delta and reports that as the
    # character, so matching the character alone meant the default combo
    # could never fire there. It reports the key's position too, and that
    # does not move when a modifier changes what the key prints.
    heard = []
    mods, vk = hotkey.parse("alt+j")
    key = hotkey._PynputHotkey(mods, vk, lambda: heard.append("down"),
                               lambda: heard.append("up"),
                               keycodes=hotkey.MAC_KEYCODES)
    key._pressed(FakeModifier("alt"))
    key._pressed(FakeKey("∆", 38))
    assert heard == ["down"]
    key._released(FakeKey("∆", 38))
    assert heard == ["down", "up"]


def test_the_letter_itself_still_matches_where_the_layout_gives_it():
    # Linux, and any macOS combo whose modifier leaves the character alone.
    heard = []
    mods, vk = hotkey.parse("alt+j")
    key = hotkey._PynputHotkey(mods, vk, lambda: heard.append("down"),
                               lambda: heard.append("up"),
                               keycodes=hotkey.MAC_KEYCODES)
    key._pressed(FakeModifier("alt"))
    key._pressed(FakeKey("j", 38))
    assert heard == ["down"]


def test_a_mac_key_position_is_not_read_off_macos():
    # X11 puts the character's own code in vk, where 38 is an ampersand.
    # Read through Apple's table that would open the microphone on a key
    # nobody bound.
    heard = []
    mods, vk = hotkey.parse("alt+j")
    key = hotkey._PynputHotkey(mods, vk, lambda: heard.append("down"),
                               lambda: heard.append("up"), keycodes={})
    key._pressed(FakeModifier("alt"))
    key._pressed(FakeKey("&", 38))
    assert heard == []


def test_the_mac_table_is_loaded_on_macos_and_nowhere_else(monkeypatch):
    mods, vk = hotkey.parse("alt+j")
    monkeypatch.setattr(hotkey.sys, "platform", "darwin")
    mac = hotkey._PynputHotkey(mods, vk, None, None)
    assert mac.keycodes == hotkey.MAC_KEYCODES
    monkeypatch.setattr(hotkey.sys, "platform", "linux")
    assert hotkey._PynputHotkey(mods, vk, None, None).keycodes == {}


def test_a_named_key_still_matches_by_its_name():
    # The name branch runs first, so the space bar never reaches the table
    # of positions -- where 49 would have made it a plain space.
    heard = []
    mods, vk = hotkey.parse("ctrl+space")
    key = hotkey._PynputHotkey(mods, vk, lambda: heard.append("down"),
                               lambda: heard.append("up"),
                               keycodes=hotkey.MAC_KEYCODES)
    key._pressed(FakeModifier("ctrl_l"))
    key._pressed(FakeModifier("space"))
    assert heard == ["down"]


def test_a_named_key_is_spelled_with_its_name():
    # chr(0x91) is an unprintable control character, printed in the very
    # warning whose job is to say which key another program is holding.
    mods, vk = hotkey.parse("ctrl+scrolllock")
    assert hotkey._spell(mods, vk) == "Ctrl+scroll_lock"


def test_a_letter_key_is_still_spelled_as_the_letter():
    mods, vk = hotkey.parse("alt+j")
    assert hotkey._spell(mods, vk) == "Alt+J"


class FakeRecorder:
    """listen.Recorder's own behaviour, including the part that bites.

    start() returns early while a stream is already open, so a microphone
    nobody closed can never be reopened -- that is what turns a missed stop()
    into voice being dead for the rest of the session.
    """

    def __init__(self, clip="audio"):
        self.clip, self.started, self.stopped = clip, 0, 0
        self.recording = False

    def start(self):
        if self.recording:
            return
        self.recording = True
        self.started += 1

    def stop(self):
        self.stopped += 1
        if not self.recording:
            return None
        self.recording = False
        return self.clip


class BlockingTranscriber:
    """Whisper, held mid-sentence until the test lets it answer."""

    def __init__(self, text="plus calme"):
        self.text, self.go, self.calls = text, threading.Event(), 0

    def __call__(self, audio):
        self.calls += 1
        self.go.wait(5)
        return self.text


class FakeDJ:
    """Only the surface voice.py touches."""

    def __init__(self):
        self.ducked, self.unducked, self.steers, self.listening = [], 0, [], []

    async def duck(self, level):
        self.ducked.append(level)

    async def unduck(self):
        self.unducked += 1

    async def on_steer(self, text):
        self.steers.append(text)

    def set_listening(self, flag):
        self.listening.append(bool(flag))

    def heard_nothing(self):
        self.missed = getattr(self, "missed", 0) + 1


async def settle():
    """Let every spawned task get as far as it can, executor round-trip and all."""
    for _ in range(20):
        await asyncio.sleep(0.01)


async def hold_and_release(v):
    """One press, one release, and the work that follows."""
    v.pressed()
    await asyncio.sleep(0)
    v.released()
    for _ in range(6):        # let the finish task run to completion
        await asyncio.sleep(0)
    while v.busy:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_held_key_ducks_records_and_steers():
    dj, rec = FakeDJ(), FakeRecorder()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=rec,
                    transcriber=lambda audio: "un truc plus calme")
    await hold_and_release(v)
    assert dj.ducked == [0.15]
    assert rec.started == 1 and rec.stopped == 1
    assert dj.steers == ["un truc plus calme"]
    assert dj.unducked == 1


@pytest.mark.asyncio
async def test_the_volume_comes_back_when_transcription_raises():
    # Otherwise the music sits at 15% for the rest of the session and reads
    # as a mixing problem nobody would connect to having spoken.
    def boom(audio):
        raise RuntimeError("model exploded")

    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=boom)
    await hold_and_release(v)
    assert dj.unducked == 1
    assert dj.steers == []


@pytest.mark.asyncio
async def test_nothing_heard_means_nothing_happens():
    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=lambda audio: "")
    await hold_and_release(v)
    assert dj.steers == []
    assert dj.unducked == 1


@pytest.mark.asyncio
async def test_a_second_press_mid_transcription_is_ignored():
    dj, rec = FakeDJ(), FakeRecorder()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=rec,
                    transcriber=lambda audio: "plus calme")
    v.pressed()
    v.pressed()
    await asyncio.sleep(0)
    assert rec.started == 1
    v.released()
    while v.busy:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_second_release_does_not_strand_the_microphone():
    # The most natural thing anyone does: nothing visibly happens for two
    # seconds, so they let go again and press again. The second release used
    # to spawn a _finish for a recording that was never started, and that
    # phantom cleared the flag the real release depends on -- so the real
    # release closed nothing and the microphone stayed open with no key held.
    dj, rec = FakeDJ(), FakeRecorder()
    tr = BlockingTranscriber()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=rec,
                    transcriber=tr)
    v.pressed()
    await asyncio.sleep(0)
    v.released()
    v.released()
    await settle()
    v.pressed()
    await asyncio.sleep(0)
    tr.go.set()               # the first transcription finally comes back
    await settle()
    v.released()
    await settle()
    assert rec.recording is False, "the microphone was left open"

    started = rec.started
    await hold_and_release(v)
    assert rec.started == started + 1, "a later press no longer records"
    assert dj.steers[-1] == "plus calme"


@pytest.mark.asyncio
async def test_a_microphone_that_refuses_to_open_does_not_wedge_the_key():
    # recorder.start() raising left busy latched True and the overlay lit,
    # which is the same stuck state by another door: every later press is
    # then swallowed by the busy guard.
    class Refuses:
        def start(self):
            raise RuntimeError("no input device")

        def stop(self):
            return None

    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=Refuses(),
                    transcriber=lambda audio: "plus calme")
    v.pressed()
    await settle()
    assert v.busy is False
    assert dj.listening[-1:] in ([], [False]), "the overlay was left lit"

    # And the key works again once the microphone comes back.
    v.recorder = FakeRecorder()
    await hold_and_release(v)
    assert dj.steers == ["plus calme"]


@pytest.mark.asyncio
async def test_a_release_with_no_press_does_nothing():
    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=lambda audio: "plus calme")
    v.released()
    await asyncio.sleep(0)
    assert dj.steers == [] and dj.unducked == 0


@pytest.mark.asyncio
async def test_the_volume_comes_back_when_transcription_never_returns():
    # The finally survives an exception but not a transcriber that simply
    # hangs, and the music then sits at 15% with nothing to say why.
    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=BlockingTranscriber(), timeout=0.05)
    v.pressed()
    await asyncio.sleep(0)
    v.released()
    await settle()
    assert dj.unducked == 1
    assert dj.steers == []
    assert v.busy is False


@pytest.mark.asyncio
async def test_a_failure_inside_the_steer_is_logged_not_swallowed(caplog):
    # on_steer holds the seventeen-second Claude call, the queue rebuild and
    # play_next. Under pythonw even asyncio's GC warning goes nowhere, so a
    # failure in there was invisible.
    class Angry(FakeDJ):
        async def on_steer(self, text):
            raise RuntimeError("the picker fell over")

    dj = Angry()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=lambda audio: "plus calme")
    with caplog.at_level(logging.ERROR, logger="music-dj"):
        await hold_and_release(v)
        await settle()
    ours = [r for r in caplog.records
            if r.name == "music-dj" and r.levelno >= logging.ERROR]
    assert ours, "the failure was left to asyncio's garbage collector"
    assert "the picker fell over" in caplog.text


@pytest.mark.asyncio
async def test_the_overlay_is_told_when_the_mic_opens_and_closes():
    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=lambda audio: "plus calme")
    await hold_and_release(v)
    assert dj.listening == [True, False]


def test_voice_stays_off_without_the_packages(monkeypatch):
    monkeypatch.setattr(voice.listen, "available", lambda: False)
    assert voice.start(FakeDJ(), None, {}) is None


def test_voice_stays_off_when_it_is_switched_off(monkeypatch):
    monkeypatch.setattr(voice.listen, "available", lambda: True)
    assert voice.start(FakeDJ(), None, {"voice": {"enabled": False}}) is None


@pytest.mark.asyncio
async def test_the_pulse_stops_when_the_key_comes_up():
    # Not when transcription finishes a second or two later. The overlay was
    # still saying "listening" after you had let go, which is the one thing
    # the indicator exists to tell you.
    started = asyncio.Event()

    def slow(audio):
        started.set()
        time.sleep(0.3)
        return "plus calme"

    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=slow)
    v.pressed()
    await asyncio.sleep(0)
    v.released()
    await asyncio.sleep(0)
    assert dj.listening == [True, False]      # already false, mid-transcription
    assert v.busy                             # and the cycle is still running
    while v.busy:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_hearing_nothing_says_so():
    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=lambda audio: "")
    await hold_and_release(v)
    assert dj.steers == []
    assert getattr(dj, "missed", 0) == 1


@pytest.mark.asyncio
async def test_hearing_something_says_nothing_extra():
    dj = FakeDJ()
    v = voice.Voice(dj, asyncio.get_running_loop(), recorder=FakeRecorder(),
                    transcriber=lambda audio: "plus calme")
    await hold_and_release(v)
    assert dj.steers == ["plus calme"]
    assert getattr(dj, "missed", 0) == 0


def test_a_package_that_is_not_installed_says_so(monkeypatch):
    monkeypatch.setattr(listen, "_import_audio", lambda: None)
    monkeypatch.setattr(listen, "_import_whisper", lambda: None)
    monkeypatch.setitem(listen._IMPORT_ERRORS, "sounddevice",
                        ModuleNotFoundError("No module named 'sounddevice'"))
    reason = listen.unavailable_reason()
    assert "sounddevice is not installed" in reason


def test_a_package_that_will_not_load_does_not_say_install_it(monkeypatch):
    # The one that cost an evening: on ARM64 Windows, sounddevice installs
    # and then asks PortAudio for a DLL its own x64 wheel never ships. Being
    # told to install what you just installed sends you round the same loop.
    monkeypatch.setattr(listen, "_import_audio", lambda: None)
    monkeypatch.setattr(listen, "_import_whisper", lambda: object())
    monkeypatch.setitem(listen._IMPORT_ERRORS, "sounddevice",
                        OSError("cannot load library 'libportaudioarm64.dll'"))
    reason = listen.unavailable_reason()
    assert "not installed" not in reason
    assert "will not load" in reason
    assert "libportaudioarm64.dll" in reason


def test_nothing_is_wrong_when_both_packages_import(monkeypatch):
    monkeypatch.setattr(listen, "_import_audio", lambda: object())
    monkeypatch.setattr(listen, "_import_whisper", lambda: object())
    assert listen.unavailable_reason() is None


def test_nothing_reachable_from_the_ui_can_open_the_microphone():
    # server.py admits in its own comment that any page served from this
    # machine can reach /ui. Text arriving from one is annoying. A microphone
    # it can open is a different category of problem, and the guarantee is
    # the dependency direction: voice imports core, core knows nothing of the
    # microphone, so no UI action can reach it however the actions grow.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    body = open(os.path.join(here, "daemon", "core.py"), encoding="utf-8").read()
    imports = [line for line in body.splitlines()
               if line.startswith(("import ", "from "))]
    assert imports
    assert not [line for line in imports
                if "listen" in line or "voice" in line or "hotkey" in line]
