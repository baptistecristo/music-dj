"""The overlay off Windows.

Two kinds of test here. The static ones read overlay/app.py as source and run
everywhere, including CI, where pywebview is not installed and could not be
imported anyway on a headless Linux runner. The live ones need pywebview and
skip without it.

The static ones matter more than they look. The bug they pin was not the
overlay looking wrong off Windows: it was `import ctypes.wintypes` at module
level, which *raises* on macOS and Linux rather than coming up empty, so the
overlay died before it drew anything. Nothing about that is visible from a
Windows machine, and nothing in CI can launch a window to find it.
"""

import ast
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(HERE, "overlay", "app.py")
PAGE = os.path.join(HERE, "overlay", "index.html")


def source():
    return open(SOURCE, encoding="utf-8").read()


def page():
    return open(PAGE, encoding="utf-8").read()


def overlay():
    """The module, or a skip if this machine cannot import pywebview."""
    pytest.importorskip("webview", reason="pywebview is not installed here")
    from overlay import app
    return app


# ---------------------------------------------------- read as source, anywhere

def test_the_windows_only_imports_sit_behind_a_platform_check():
    tree = ast.parse(source())
    top_level = set()
    for node in tree.body:                       # module level only
        if isinstance(node, ast.Import):
            top_level.update(alias.name for alias in node.names)
    assert "ctypes.wintypes" not in top_level, (
        "ctypes.wintypes raises on macOS and Linux; importing it at module "
        "level takes the overlay down before it draws anything")
    assert "ctypes" not in top_level


def test_every_win32_helper_checks_the_platform_before_calling_out():
    # A helper that reaches for ctypes.windll without a WINDOWS guard raises
    # AttributeError off Windows -- ctypes has no windll there.
    # Top-level functions only: those are the entry points, and a guard on one
    # covers the helpers nested inside it. Anything reached from outside the
    # module goes through one of these.
    body = source()
    tree = ast.parse(body)
    missing = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        fn = ast.get_source_segment(body, node) or ""
        if "windll" not in fn:
            continue
        # Either it checks the platform, or it checks for the window handle,
        # which is only ever set on Windows.
        if "WINDOWS" not in fn and "_hwnd" not in fn:
            missing.append(node.name)
    assert not missing, "unguarded Win32 calls in: %s" % ", ".join(missing)


def test_the_structures_that_need_windows_types_are_built_conditionally():
    body = source()
    for name in ("class _Margins", "class _GUID"):
        at = body.index(name)
        # Indented, which in this module means inside the `if WINDOWS:` block.
        assert body[at - 4:at] == "    ", "%s is defined unconditionally" % name


# ------------------------------------------------------------------- the page

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


# -------------------------------------------------- run it, where we can

def test_the_module_imports_on_this_platform():
    app = overlay()
    assert app.WINDOWS == (sys.platform == "win32")


def test_the_win32_helpers_answer_falsely_rather_than_raising(monkeypatch):
    app = overlay()
    # Forced, so the guards are exercised on whatever platform this is.
    monkeypatch.setattr(app, "WINDOWS", False)
    assert app.hide_from_taskbar(None) is False
    assert app.find_windows("music-dj") == []
    assert app.find_visible_window("music-dj") is None
    app.make_toolwindow(None)
    app.hide_from_switchers_early()
    app.apply_glass(None)
    app.adopt_solid_window()


def test_resizing_reports_failure_so_the_caller_falls_back(monkeypatch):
    # Api.expand falls back to pywebview's own resize when this returns False,
    # which is the whole plan off Windows.
    app = overlay()
    monkeypatch.setattr(app, "_hwnd", None)
    assert app.set_size(368, 192) is False


def test_alpha_changes_are_a_no_op_without_a_window_handle(monkeypatch):
    app = overlay()
    monkeypatch.setattr(app, "_hwnd", None)
    app.set_alpha(200)                   # must not raise
    app.fade_alpha(120)


def test_the_page_shows_what_it_heard_you_say():
    # Picking takes seventeen seconds. Without this, holding the key looks
    # like it did nothing at all. It is an input now rather than a chip, so
    # a transcript that came back wrong can be corrected instead of redone.
    body = page()
    assert 'id="asktext"' in body
    assert "state.steer" in body


def test_the_steer_chip_can_be_cleared():
    assert 'send({action: "clearSteer"})' in page()


def test_the_page_shows_when_the_microphone_is_open():
    # The cover is the only part visible while the overlay is closed, so the
    # class has to reach it, not just exist somewhere in the file.
    body = page()
    assert 'classList.toggle("listening"' in body
    assert re.search(r"body\.listening\s+#art", body)


def test_the_box_is_there_before_you_have_said_anything():
    # This used to hide itself when empty, the same rule the notice follows.
    # It stays now: a hotkey that will not fire, a package that will not load
    # and a microphone that hears nothing all still leave you a way to steer.
    body = page()
    assert 'placeholder="ask for something"' in body
    assert "#asktext:empty" not in body and "#ask:empty" not in body


def test_the_box_does_not_close_under_you_while_you_type():
    # The panel shuts on mouseleave. Focus has to outrank that, or moving the
    # mouse mid-sentence takes the window and the sentence with it.
    body = page()
    collapse = body.split("function collapse()", 1)[1].split("function ", 1)[0]
    assert "typingInBox()" in collapse


def test_a_transcript_never_overwrites_what_you_are_typing():
    body = page()
    assert "document.activeElement !== box" in body


def test_the_meter_falls_back_to_zero_if_the_daemon_goes_quiet():
    # The daemon sends a zero when the key comes up. One that dies mid-
    # sentence sends nothing, and a bar frozen half full reads as listening.
    body = page()
    assert "levelAt" in body and "> 500" in body


def test_the_mic_badge_is_drawn_not_typed():
    # The transport icons were a Windows-only font once and came out as empty
    # boxes on macOS and Linux.
    body = page()
    mic = body.split('id="mic"', 1)[1].split("</div>", 1)[0]
    assert "<svg" in mic


# ------------------------------------------------- settling back to collapsed

def test_the_window_eases_shut_rather_than_snapping():
    # The alpha already faded on the way out; the geometry jumped in one
    # SetWindowPos, which is the jolt you feel when the pointer leaves.
    body = source()
    assert "def slide_size(" in body
    collapse = body.split("def collapse(self):", 1)[1].split("def ", 1)[0]
    assert "slide_size(" in collapse, "collapse must ease, not snap"
    assert "set_size(*COLLAPSED)" in collapse, (
        "keep the plain resize as the fallback for a window we cannot measure")


def test_opening_abandons_a_shrink_still_in_flight():
    # Come back to the cover mid-collapse and the shrink would keep stepping
    # the window down on its thread, fighting the expand and winning.
    body = source()
    expand = body.split("def expand(self, height=None):", 1)[1].split("def ", 1)[0]
    assert "_size_gen[0] += 1" in expand


def test_the_panel_fades_before_the_window_moves():
    body = page()
    assert "body.open.closing .panel" in body, "no fade-out state on the panel"
    closing = body.index('classList.add("closing")')
    shrink = body.index("pywebview.api.collapse()")
    assert closing < shrink, "the content has to be gone before the window moves"


def test_coming_back_cancels_the_fade_out():
    # Cross back over the cover during the fade and the panel must return to
    # full ink rather than finishing its disappearance under the pointer.
    body = page()
    expand = body.split("function expand()", 1)[1].split("function ", 1)[0]
    assert 'classList.remove("closing")' in expand
