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
        set_alpha(ACTIVE_ALPHA)

    def collapse(self):
        if not set_size(*COLLAPSED):
            win = self._window()
            if win:
                win.resize(*COLLAPSED)
        set_alpha(IDLE_ALPHA)


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


def find_visible_window(title):
    """The shown top-level window with this title, or None.

    There are two of them: pywebview leaves a hidden one behind, and it is the
    one FindWindowW hands back early in startup. Styling that one changes
    nothing you can see, so walk the list and take the visible one.
    """
    user32 = ctypes.windll.user32
    found = []

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == title:
            found.append(hwnd)
            return False
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                              ctypes.c_void_p)(visit)
    user32.EnumWindows(proc, 0)
    return found[0] if found else None


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

    # Hiding the window to swap in WS_EX_TOOLWINDOW -- the flag that keeps it
    # out of the taskbar and Alt+Tab -- left it invisible and never brought it
    # back, whether shown again with ShowWindow or SetWindowPos. Windows only
    # re-reads that style on a fresh show, and pywebview owns this window's
    # show sequence. Losing the overlay is far worse than an extra taskbar
    # entry, so the flag stays off until it can be set before the first show.
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        _hwnd = hwnd
        set_alpha(IDLE_ALPHA)      # starts collapsed, so start faded
        set_size(*COLLAPSED)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        log.info("translucency on: %d idle, %d under the pointer; "
                 "window is %dx%d, asked for %dx%d",
                 IDLE_ALPHA, ACTIVE_ALPHA,
                 rect.right - rect.left, rect.bottom - rect.top, *COLLAPSED)
    except Exception:
        log.info("could not restyle the window; it stays opaque")


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


def set_alpha(value):
    """Change how much of the desktop shows through. Cheap enough to call on
    every hover."""
    if not _hwnd:
        return
    try:
        ctypes.windll.user32.SetLayeredWindowAttributes(
            _hwnd, 0, max(40, min(255, int(value))), LWA_ALPHA)
    except Exception:
        log.debug("could not change alpha", exc_info=True)


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

    def on_start(win):
        if args.solid:
            win.evaluate_js("document.body.classList.add('solid')")
            return
        apply_glass(win)

    # Runs once the native window exists; the handle does not before that.
    webview.start(on_start, window)


if __name__ == "__main__":
    main()
