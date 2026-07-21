"""The overlay strip.

A frameless, always-on-top window. The HTML talks to the daemon over /ui by
itself; Python here only owns the things a web page cannot do -- growing the OS
window on hover and remembering where you left it.

    python -m overlay.app
"""

import logging
import os
import threading

import webview

from daemon import store

COLLAPSED = (260, 40)
EXPANDED = (260, 130)

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")

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
        win = self._window()
        if win:
            win.resize(*EXPANDED)

    def collapse(self):
        win = self._window()
        if win:
            win.resize(*COLLAPSED)


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


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    config = store.read_json(store.CONFIG, {})
    x, y = saved_position(config)

    # easy_drag drags on any mousedown and ignores CSS app-region, so clicking
    # a star would also shove the window. Instead: drag only when the click
    # lands directly on an element tagged .pywebview-drag-region.
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

    api = Api()
    window = webview.create_window(
        "music-dj",
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
        background_color="#1b1b1f",
        shadow=False,
    )
    save_now = remember_position(window)
    window.events.closing += lambda *_a: save_now()

    webview.start()


if __name__ == "__main__":
    main()
