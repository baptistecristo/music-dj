"""One global key, held down.

Windows swallows a RegisterHotKey combo before any application sees it, which
is the whole reason the key is a modifier plus one letter. A chord like
Alt+D+J cannot go through that API at all, and pressing it would send a real
Alt+D to whatever window was focused on the way to the J.

WM_HOTKEY only fires on the press, so the release is found by polling the key
itself. That is the part with no tidier option.
"""

import logging
import sys
import threading
import time

log = logging.getLogger("music-dj")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# One press must read as one press. Without this Windows re-fires WM_HOTKEY at
# the keyboard's repeat rate while the key is down.
MOD_NOREPEAT = 0x4000

_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
         "shift": MOD_SHIFT, "win": MOD_WIN, "cmd": MOD_WIN,
         "super": MOD_WIN}

_NAMED_KEYS = {"space": 0x20, "pause": 0x13, "scrolllock": 0x91}

# pynput's own name for each of the keys above -- not the same word as the
# spec above. chr(vk) would give a space or an unprintable control character
# for these, and the pynput side of the combo would then never match, with
# no error to say why.
_NAMED_KEY_NAMES = {0x20: "space", 0x13: "pause", 0x91: "scroll_lock"}

POLL = 0.03               # how often the release is checked, in seconds


def parse(spec):
    """'alt+j' -> (modifier flags, virtual-key code), or None."""
    if not spec:
        return None
    mods, key = 0, None
    for part in str(spec).lower().split("+"):
        part = part.strip()
        if not part:
            continue
        if part in _MODS:
            mods |= _MODS[part]
        elif key is not None:
            # Two non-modifier keys. See the module docstring: this API
            # cannot express it, and the chord leaks on the way in.
            return None
        elif part in _NAMED_KEYS:
            key = _NAMED_KEYS[part]
        elif len(part) == 1:
            key = ord(part.upper())
        else:
            return None
    if key is None:
        return None
    return mods | MOD_NOREPEAT, key


class _WindowsHotkey(threading.Thread):
    """RegisterHotKey on its own thread, with its own message queue.

    The queue belongs to the thread that registered the key, so this cannot be
    folded into the daemon's loop without blocking it.
    """

    WM_HOTKEY = 0x0312
    PM_REMOVE = 0x0001
    # Virtual-key codes for the modifiers themselves, polled in _held() --
    # RegisterHotKey's MOD_* flags say what the combo requires, but only
    # GetAsyncKeyState says whether that particular key is still down.
    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12
    VK_LWIN = 0x5B
    VK_RWIN = 0x5C

    def __init__(self, mods, vk, on_press, on_release):
        super().__init__(daemon=True, name="music-dj-hotkey")
        self.mods, self.vk = mods, vk
        self.on_press, self.on_release = on_press, on_release
        self._stop = threading.Event()
        self.registered = threading.Event()
        self.failed = False

    def run(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        # GetAsyncKeyState returns a SHORT. Left at the default int restype,
        # whatever the API left in the upper half of the register reads as
        # part of the answer, and the release check goes random.
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                          ctypes.c_uint, ctypes.c_uint]
        if not user32.RegisterHotKey(None, 1, self.mods, self.vk):
            # Taken by another application. Say which key, because a feature
            # that never works and never explains itself costs an evening.
            log.warning("could not grab the voice key (%s); another program "
                        "already holds it. Set voice.hotkey in config.json.",
                        _spell(self.mods, self.vk))
            self.failed = True
            self.registered.set()
            return
        self.registered.set()
        msg = wintypes.MSG()
        try:
            while not self._stop.is_set():
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0,
                                       self.PM_REMOVE):
                    if msg.message == self.WM_HOTKEY:
                        self._held(user32)
                time.sleep(POLL)
        finally:
            user32.UnregisterHotKey(None, 1)

    def _held(self, user32):
        self.on_press()
        try:
            # The high bit of GetAsyncKeyState is "down now". Releasing the
            # letter or any modifier the combo actually carries ends the
            # phrase, because letting go of one of them first is a normal way
            # to stop talking. Checking only Alt here would leave ctrl+shift+j
            # -- the combo someone reaches for once Alt+J collides with
            # another program -- capturing forever after Alt, which was never
            # part of it.
            while not self._stop.is_set():
                if not user32.GetAsyncKeyState(self.vk) & 0x8000:
                    break
                if self.mods & MOD_ALT and \
                        not user32.GetAsyncKeyState(self.VK_MENU) & 0x8000:
                    break
                if self.mods & MOD_CONTROL and \
                        not user32.GetAsyncKeyState(self.VK_CONTROL) & 0x8000:
                    break
                if self.mods & MOD_SHIFT and \
                        not user32.GetAsyncKeyState(self.VK_SHIFT) & 0x8000:
                    break
                if self.mods & MOD_WIN and \
                        not (user32.GetAsyncKeyState(self.VK_LWIN) & 0x8000 or
                             user32.GetAsyncKeyState(self.VK_RWIN) & 0x8000):
                    break
                time.sleep(POLL)
        finally:
            self.on_release()

    def stop(self):
        self._stop.set()


class _PynputHotkey:
    """macOS and Linux, where there is no RegisterHotKey.

    pynput sees the keys rather than grabbing them, so the combo also reaches
    whatever is focused. macOS needs an accessibility grant before it sees
    anything at all, and Linux needs X11.
    """

    def __init__(self, mods, vk, on_press, on_release):
        self.mods, self.vk = mods, vk
        self.on_press, self.on_release = on_press, on_release
        self._down = set()
        self._active = False
        self._listener = None
        self.failed = False

    def start(self):
        try:
            from pynput import keyboard
        except Exception:
            log.info("voice needs pynput on this platform: "
                     "pip install -r app/requirements-voice.txt")
            self.failed = True
            return self
        self._listener = keyboard.Listener(on_press=self._pressed,
                                           on_release=self._released)
        self._listener.daemon = True
        self._listener.start()
        return self

    def _wanted(self):
        # A letter matches chr(vk); a named key does not -- see
        # _NAMED_KEY_NAMES for why chr(vk) alone would never match pynput's
        # own name for it.
        keys = {_NAMED_KEY_NAMES.get(self.vk, chr(self.vk).lower())}
        if self.mods & MOD_ALT:
            keys |= {"alt"}
        if self.mods & MOD_CONTROL:
            keys |= {"ctrl"}
        if self.mods & MOD_SHIFT:
            keys |= {"shift"}
        if self.mods & MOD_WIN:
            keys |= {"cmd"}
        return keys

    def _name(self, key):
        char = getattr(key, "char", None)
        if char:
            return char.lower()
        name = getattr(key, "name", "") or ""
        # "win"/"super" in a spec and "cmd" from pynput must land on the same
        # token, or a combo parsed from "win+j" needs no modifier at all on
        # macOS and Linux -- plain j would open the microphone.
        for stem in ("alt", "ctrl", "shift", "cmd", "super"):
            if name.startswith(stem):
                return "cmd" if stem == "super" else stem
        return name

    def _pressed(self, key):
        self._down.add(self._name(key))
        if not self._active and self._wanted() <= self._down:
            self._active = True
            self.on_press()

    def _released(self, key):
        self._down.discard(self._name(key))
        if self._active and not self._wanted() <= self._down:
            self._active = False
            self.on_release()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()


def _spell(mods, vk):
    names = [n for n, bit in (("Alt", MOD_ALT), ("Ctrl", MOD_CONTROL),
                              ("Shift", MOD_SHIFT), ("Win", MOD_WIN))
             if mods & bit]
    return "+".join(names + [chr(vk)])


def start(spec, on_press, on_release):
    """Grab the key. Returns something with .stop(), or None."""
    parsed = parse(spec)
    if not parsed:
        log.warning("voice.hotkey %r is not a modifier plus one key; "
                    "voice is off", spec)
        return None
    mods, vk = parsed
    if sys.platform == "win32":
        key = _WindowsHotkey(mods, vk, on_press, on_release)
        key.start()
        key.registered.wait(timeout=2)
        if key.failed:
            return None
    else:
        key = _PynputHotkey(mods, vk, on_press, on_release).start()
        if key.failed:
            return None
    log.info("hold %s and talk to the DJ", _spell(mods, vk))
    return key
