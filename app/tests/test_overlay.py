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
