"""The overlay off Windows.

Everything here is about the module being importable and inert on a platform
that has no Win32 API. It cannot check that the window looks right on macOS --
nothing in CI can -- but it can check that the parts which would raise are the
parts that get skipped.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("webview", reason="pywebview is not installed here")

from overlay import app  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(HERE, "overlay", "index.html")


def test_the_module_imports_on_this_platform():
    # ctypes.wintypes raises on import off Windows rather than merely being
    # empty, so an unguarded import at the top took the overlay down before it
    # drew anything -- on every machine that was not this one.
    assert app.WINDOWS == (sys.platform == "win32")


@pytest.mark.skipif(sys.platform == "win32", reason="the Win32 path is live here")
def test_the_win32_helpers_are_inert_off_windows():
    assert app.hide_from_taskbar(None) is False
    assert app.find_windows("music-dj") == []
    assert app.find_visible_window("music-dj") is None
    app.make_toolwindow(None)            # must not raise
    app.hide_from_switchers_early()
    app.apply_glass(None)
    app.adopt_solid_window()


def test_the_win32_helpers_answer_falsely_rather_than_raising(monkeypatch):
    # Forced, so the guards are exercised on every platform CI runs on and
    # not only on the two that would skip the test above.
    monkeypatch.setattr(app, "WINDOWS", False)
    assert app.hide_from_taskbar(None) is False
    assert app.find_windows("music-dj") == []
    app.make_toolwindow(None)
    app.apply_glass(None)


def test_resizing_reports_failure_so_the_caller_falls_back(monkeypatch):
    # Api.expand falls back to pywebview's own resize when this returns False,
    # which is the whole plan off Windows.
    monkeypatch.setattr(app, "_hwnd", None)
    assert app.set_size(368, 192) is False


def test_alpha_changes_are_a_no_op_without_a_window_handle(monkeypatch):
    monkeypatch.setattr(app, "_hwnd", None)
    app.set_alpha(200)                   # must not raise
    app.fade_alpha(120)


# ------------------------------------------------------------------- the page

def page():
    return open(PAGE, encoding="utf-8").read()


def test_the_transport_uses_svg_not_a_windows_only_icon_font():
    # Segoe Fluent Icons ships with Windows alone, and its glyphs live in the
    # private use area: everywhere else those buttons were empty boxes.
    html = page()
    assert "var(--icons)" not in html
    assert not re.search(r"&#xE[0-9A-Fa-f]{3};", html), "a PUA glyph is left"
    assert html.count("<svg") >= 4


def test_the_font_stacks_reach_past_windows():
    html = page()
    display = re.search(r"--display:(.+?);", html, re.S).group(1)
    assert "Segoe UI" in display, "Windows should still get its own font"
    assert "-apple-system" in display and "Cantarell" in display


def test_the_page_can_drop_the_glass_when_the_platform_has_none():
    assert "body.noglass #shell" in page()
