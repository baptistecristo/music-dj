"""The overlay strip.

A frameless, always-on-top window. The HTML talks to the daemon over /ui by
itself; Python here only owns the things a web page cannot do -- growing the OS
window on hover and remembering where you left it.

    python -m overlay.app
"""

import ctypes
import ctypes.wintypes
import logging
import os
import threading
import time

import webview

from daemon import store

# Collapsed is just the album art, so the window is a sleeve-sized tile rather
# than a strip. It opens on hover into a mini player shaped like Apple Music's:
# art and naming on top, the reason this track was picked, a full-width
# scrubber with elapsed/remaining times, then hearts, transport and mood.
# The expanded height is the sum of those rows plus padding, with a little
# slack -- shrink it and the notice line clips.
COLLAPSED = (95, 95)
EXPANDED = (368, 210)

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")

TITLE = "music-dj"

log = logging.getLogger("music-dj.overlay")


class Api:
    """Called from the page. Keep these cheap: they run on the UI thread.

    Deliberately holds no reference to the window. pywebview introspects this
    object's attributes to expose them to JavaScript, and a Window here sends it
    walking into the native .NET form -- AccessibilityObject.Bounds.Empty.Empty
    forever, until the stack gives out. Reach the window through the module
    instead.
    """

    def _window(self):
        return webview.windows[0] if webview.windows else None

    def expand(self):
        if not set_size(*EXPANDED):
            win = self._window()
            if win:
                win.resize(*EXPANDED)
        fade_alpha(ACTIVE_ALPHA)

    def collapse(self):
        if not set_size(*COLLAPSED):
            win = self._window()
            if win:
                win.resize(*COLLAPSED)
        fade_alpha(IDLE_ALPHA)

    # The page owns the when (it is the one watching playback state); Python
    # owns the how, because a web page cannot fade an OS window.
    def vanish(self):
        vanish()

    def reappear(self):
        reappear()


# --------------------------------------------------------------------- glass

# Desktop Window Manager attributes. backdrop-filter in CSS only blurs the
# page's own content, so a genuinely frosted window has to come from DWM.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWMWCP_ROUND = 2
DWMSBT_TRANSIENTWINDOW = 3      # acrylic: the blur used for flyouts

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
# A tool window is furniture, not a document: Windows leaves it out of the
# taskbar and out of the Alt+Tab list, which is what you want from something
# that just sits on top all day.
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
# Mouse input falls straight through to whatever is underneath. Paired with
# zero alpha this is how the overlay leaves the screen: an invisible window
# that still swallowed hovers would be a haunted patch of desktop.
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x00000002

SW_HIDE = 0

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
# Layered alpha dims everything, text included, so the open panel is fully
# opaque -- a see-through title over a busy desktop is unreadable. Collapsed
# there is nothing but album art, which is exactly where translucency belongs.
ACTIVE_ALPHA = 255
IDLE_ALPHA = 140

_hwnd = None                    # found once, reused for every alpha change


class _Margins(ctypes.Structure):
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(d1, d2, d3, rest):
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*rest))


CLSID_TASKBARLIST = _guid(0x56FDF344, 0xFD6D, 0x11D0,
                          (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
IID_ITASKBARLIST = _guid(0x56FDF342, 0xFD6D, 0x11D0,
                         (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
CLSCTX_INPROC_SERVER = 1
# ITaskbarList vtable: QueryInterface, AddRef, Release, HrInit, AddTab,
# DeleteTab, ActivateTab, SetActiveAlt.
VT_HRINIT, VT_DELETETAB = 3, 5


def hide_from_taskbar(hwnd):
    """Drop the taskbar button without restyling the window.

    The obvious route -- adding WS_EX_TOOLWINDOW -- needs the window hidden and
    shown again for Windows to re-read it, and doing that to a window pywebview
    owns loses it for good. The shell exposes this instead, and it works on a
    window that is already up.
    """
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    ptr = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_TASKBARLIST), None,
                                CLSCTX_INPROC_SERVER,
                                ctypes.byref(IID_ITASKBARLIST),
                                ctypes.byref(ptr))
    if hr != 0 or not ptr:
        log.info("taskbar list unavailable (0x%08x); the button stays", hr & 0xFFFFFFFF)
        return False

    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)
    init = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtable[VT_HRINIT])
    delete = proto(vtable[VT_DELETETAB])

    if init(ptr) != 0:
        log.info("could not initialise the taskbar list; the button stays")
        return False
    ok = delete(ptr, hwnd) == 0
    log.info("taskbar button removed" if ok else "taskbar button could not be removed")
    return ok


def find_windows(title, visible_only=False):
    """This process's top-level windows with this title, in z-order.

    There are two of them: pywebview leaves a hidden one behind, and it is the
    one FindWindowW hands back early in startup. Callers that want the one on
    screen pass visible_only; callers styling ahead of the first show want
    every match, hidden included. Filtering by our own pid matters: a second
    overlay instance would otherwise find -- and restyle, and resize, across
    DPI contexts -- the first one's window.
    """
    user32 = ctypes.windll.user32
    our_pid = os.getpid()
    found = []

    def visit(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != our_pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == title:
            found.append(hwnd)
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                              ctypes.c_void_p)(visit)
    user32.EnumWindows(proc, 0)
    return found


def find_visible_window(title):
    """The shown top-level window with this title, or None."""
    found = find_windows(title, visible_only=True)
    return found[0] if found else None


def make_toolwindow(hwnd):
    """Mark the window as furniture: out of Alt+Tab, out of the taskbar."""
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    wanted = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if wanted != style:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, wanted)


def hide_from_switchers_early():
    """Set WS_EX_TOOLWINDOW before pywebview first shows the window.

    The taskbar decides whether a window gets a button when it is shown, and
    hiding an already-shown pywebview window to make it re-read the style has
    lost the overlay for good before. Setting the style while the window is
    still hidden sidesteps both problems, so this watches for the window from
    a thread started ahead of webview.start() and styles every match -- the
    hidden leftover included, where it is harmless. Alt+Tab reads the style
    each time it opens, so even a lost race still keeps the overlay out of the
    switcher; only the taskbar button needs the head start.
    """
    def watch():
        user32 = ctypes.windll.user32
        deadline = time.time() + 10
        while time.time() < deadline:
            shown = False
            for hwnd in find_windows(TITLE):
                make_toolwindow(hwnd)
                if user32.IsWindowVisible(hwnd):
                    shown = True
            if shown:
                log.debug("tool-window style set")
                return
            time.sleep(0.01)
        log.debug("window never appeared; tool-window style not confirmed")

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()


def apply_glass(window):
    """Frost the window using Windows 11 acrylic.

    Needs Windows 11 22H2 or newer; older builds ignore the attribute and you
    simply get a flat dark window, which is why nothing here is fatal.
    """
    # The start callback fires before the native form is attached, and on the
    # EdgeChromium backend window.native never appears at all -- so fall back
    # to asking Windows for the window by its title.
    global _hwnd
    hwnd = None
    for _ in range(100):
        hwnd = find_visible_window(TITLE)
        if hwnd:
            break
        time.sleep(0.05)

    if not hwnd:
        log.info("could not find the window; leaving it opaque")
        return

    try:
        dwm = ctypes.windll.dwmapi
        for attribute, value in (
            (DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
            (DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW),
        ):
            val = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(val),
                                      ctypes.sizeof(val))
        # The backdrop is only drawn where the frame extends into the client
        # area, and -1 means "the whole thing".
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(-1, -1, -1, -1)))
        log.debug("acrylic backdrop requested")
    except Exception:
        log.debug("could not request acrylic", exc_info=True)

    # WebView2 paints an opaque surface across the client area, so the acrylic
    # above usually ends up hidden behind it. Layered-window alpha is applied
    # by the compositor to the finished window, so it shows through regardless:
    # translucency without blur, which is the honest ceiling here.
    user32 = ctypes.windll.user32

    # WS_EX_TOOLWINDOW is normally set by hide_from_switchers_early before the
    # first show; re-asserting it here is free and covers a lost race. Never
    # hide-and-reshow to force it -- that has lost the overlay for good.
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        make_toolwindow(hwnd)
        _hwnd = hwnd
        set_alpha(IDLE_ALPHA)      # starts collapsed, so start faded
        set_size(*COLLAPSED)
        # Belt and braces: if the style landed after the first show, the
        # button is already up and only the shell API takes it down.
        hide_from_taskbar(hwnd)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        log.info("translucency on: %d idle, %d under the pointer; "
                 "window is %dx%d, asked for %dx%d",
                 IDLE_ALPHA, ACTIVE_ALPHA,
                 rect.right - rect.left, rect.bottom - rect.top, *COLLAPSED)
    except Exception:
        log.info("could not restyle the window; it stays opaque")


def adopt_solid_window():
    """Solid mode skips apply_glass, but vanish() still needs the handle and
    a layered style to fade. Alpha stays pinned at 255, so 'solid' keeps
    meaning exactly that -- the only fade this enables is the one to zero
    while the music is paused."""
    global _hwnd
    hwnd = None
    for _ in range(100):
        hwnd = find_visible_window(TITLE)
        if hwnd:
            break
        time.sleep(0.05)
    if not hwnd:
        log.info("could not find the window; pausing will not hide it")
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        _hwnd = hwnd
        set_alpha(255)
    except Exception:
        log.debug("could not restyle the solid window", exc_info=True)


def set_size(width, height):
    """Resize past the WinForms minimum.

    pywebview's resize() goes through WinForms, which refuses to go below its
    own minimum tracking width -- ask for 72 wide and you get 232. SetWindowPos
    talks to the window manager directly and is not second-guessed.
    """
    if not _hwnd:
        return False
    try:
        ctypes.windll.user32.SetWindowPos(
            _hwnd, 0, 0, 0, int(width), int(height),
            SWP_NOMOVE | SWP_NOACTIVATE)
        return True
    except Exception:
        log.debug("could not resize", exc_info=True)
        return False


def set_alpha(value, floor=40):
    """Change how much of the desktop shows through. Cheap enough to call on
    every hover. The floor keeps hover fades from ever losing the window by
    mistake; only vanish() passes 0, and does so on purpose."""
    if not _hwnd:
        return
    try:
        ctypes.windll.user32.SetLayeredWindowAttributes(
            _hwnd, 0, max(floor, min(255, int(value))), LWA_ALPHA)
    except Exception:
        log.debug("could not change alpha", exc_info=True)


_fade_gen = [0]                 # bumping this abandons any fade in flight


def fade_alpha(target, duration=0.15, floor=40):
    """Ease the layered alpha to target. The alpha is an OS property CSS
    cannot transition, so the snap is smoothed here instead."""
    if not _hwnd:
        return
    _fade_gen[0] += 1
    gen = _fade_gen[0]
    target = max(floor, min(255, int(target)))

    def run():
        user32 = ctypes.windll.user32
        key = ctypes.wintypes.DWORD()
        alpha = ctypes.c_ubyte()
        flags = ctypes.wintypes.DWORD()
        start = target
        try:
            if user32.GetLayeredWindowAttributes(
                    _hwnd, ctypes.byref(key), ctypes.byref(alpha),
                    ctypes.byref(flags)):
                start = alpha.value
        except Exception:
            pass
        if start == target:
            return
        steps = 8
        for i in range(1, steps + 1):
            if _fade_gen[0] != gen:
                return
            set_alpha(start + (target - start) * i / steps, floor=0)
            time.sleep(duration / steps)

    threading.Thread(target=run, daemon=True).start()


def vanish():
    """Take the overlay off the screen without ever hiding the window.

    The obvious ShowWindow(SW_HIDE) is exactly the hide-and-reshow that has
    lost pywebview's window for good before (see apply_glass), so the overlay
    disappears while staying shown: alpha fades to zero, and
    WS_EX_TRANSPARENT stops the invisible rectangle from eating clicks and
    hovers meant for whatever is behind it.
    """
    if not _hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(_hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(_hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
    except Exception:
        log.debug("could not make the window click-through", exc_info=True)
    fade_alpha(0, floor=0)


def reappear():
    """Undo vanish(): catch the pointer again and fade back in, collapsed."""
    if not _hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(_hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(_hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT)
    except Exception:
        log.debug("could not restore clicks", exc_info=True)
    fade_alpha(IDLE_ALPHA, floor=0)


def saved_position(config):
    pos = (config.get("overlay") or {}).get("position") or {}
    x, y = pos.get("x"), pos.get("y")
    if isinstance(x, int) and isinstance(y, int):
        return x, y
    return None, None


def remember_position(window):
    """Persist where the window ended up, without clobbering plugin config.

    pywebview fires `moved` continuously during a drag, so write on a timer
    rather than on every event.
    """
    pending = {"timer": None}

    def store_it():
        try:
            cfg = store.read_json(store.CONFIG, {})
            overlay = dict(cfg.get("overlay") or {})
            overlay["position"] = {"x": int(window.x), "y": int(window.y)}
            store.merge_config({"overlay": overlay})
        except Exception:
            log.debug("could not save overlay position", exc_info=True)

    def on_move(*_args):
        # pywebview passes coordinates on some backends and nothing on others,
        # so swallow whatever arrives and read the window instead.
        if pending["timer"]:
            pending["timer"].cancel()
        pending["timer"] = threading.Timer(0.6, store_it)
        pending["timer"].daemon = True
        pending["timer"].start()

    try:
        window.events.moved += on_move
    except Exception:
        log.debug("this pywebview build has no moved event", exc_info=True)
    return store_it


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="music-dj overlay")
    parser.add_argument("--solid", action="store_true",
                        help="opaque window; use if the glass looks wrong")
    parser.add_argument("--alpha", type=int, default=ACTIVE_ALPHA,
                        metavar="0-255",
                        help="opacity under the pointer (default %d)" % ACTIVE_ALPHA)
    parser.add_argument("--idle-alpha", type=int, default=IDLE_ALPHA,
                        metavar="0-255",
                        help="opacity when idle (default %d)" % IDLE_ALPHA)
    args = parser.parse_args(argv)
    return _run(args)


def _run(args):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    config = store.read_json(store.CONFIG, {})
    x, y = saved_position(config)

    # easy_drag drags on any mousedown and ignores CSS app-region, so clicking
    # a star would also shove the window. Instead: drag only when the click
    # lands directly on an element tagged .pywebview-drag-region.
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

    global ACTIVE_ALPHA, IDLE_ALPHA
    ACTIVE_ALPHA = max(40, min(255, args.alpha))
    IDLE_ALPHA = max(40, min(255, args.idle_alpha))
    if args.solid:
        # Solid promises an opaque window; with the handle adopted below the
        # hover fades would otherwise start dimming it like the glass one.
        ACTIVE_ALPHA = IDLE_ALPHA = 255

    api = Api()
    window = webview.create_window(
        TITLE,
        PAGE,
        js_api=api,
        width=COLLAPSED[0], height=COLLAPSED[1],
        x=x, y=y,
        frameless=True,
        easy_drag=False,         # see DRAG_REGION_DIRECT_TARGET_ONLY above
        on_top=True,
        resizable=False,
        # The default (200, 100) floor would refuse the 40px collapsed height.
        min_size=(1, 1),
        # Transparent so the acrylic backdrop shows through instead of the
        # webview painting a flat colour over it.
        transparent=not args.solid,
        background_color="#1b1b1f",
        shadow=False,
    )
    save_now = remember_position(window)
    window.events.closing += lambda *_a: save_now()
    hide_from_switchers_early()

    def on_start(win):
        if args.solid:
            win.evaluate_js("document.body.classList.add('solid')")
            adopt_solid_window()
            return
        apply_glass(win)

    # Runs once the native window exists; the handle does not before that.
    webview.start(on_start, window)


if __name__ == "__main__":
    main()
