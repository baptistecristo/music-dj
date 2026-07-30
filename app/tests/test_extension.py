"""The extension manifest and scripts, checked for the things that only fail
in a browser this machine may not have.

None of it can prove the extension loads in Firefox -- nothing in CI installs
Firefox and side-loads an unsigned add-on. What it can do is hold the shape
that Firefox needs, because every one of these mistakes shows up as silence:
a missing background key means the worker never runs, and a missing gecko id
means native messaging refuses with "host not found".
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host import register  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION = os.path.join(HERE, "extension")


def manifest():
    with open(os.path.join(EXTENSION, "manifest.json"), encoding="utf-8-sig") as fh:
        return json.load(fh)


def script(name):
    with open(os.path.join(EXTENSION, name), encoding="utf-8") as fh:
        return fh.read()


def code(name):
    """The script with its comments stripped.

    These tests are about what the scripts *call*. Prose explaining why an
    API is avoided otherwise reads as a use of it.
    """
    body = re.sub(r"/\*.*?\*/", "", script(name), flags=re.S)
    return "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("//"))


def test_the_background_is_declared_for_both_engines():
    # Chrome MV3 wants service_worker; Firefox MV3 runs an event page and
    # wants scripts. Each ignores the other's key, so both are given.
    background = manifest()["background"]
    assert background["service_worker"] == "background.js"
    assert background["scripts"] == ["background.js"]


def test_firefox_gets_the_stable_add_on_id_native_messaging_needs():
    # Must match allowed_extensions in the host manifest register.py writes,
    # or the launcher is invisible to Firefox -- reported as "host not found",
    # which sounds like the launcher is missing rather than unauthorised.
    gecko = manifest()["browser_specific_settings"]["gecko"]
    assert gecko["id"] == register.DEFAULT_GECKO_ID


def test_firefox_is_pinned_past_the_version_that_learned_main_world():
    # Content scripts in the MAIN world, which is the only way to reach
    # MusicKit, landed in Firefox 128.
    gecko = manifest()["browser_specific_settings"]["gecko"]
    assert float(gecko["strict_min_version"].split(".")[0]) >= 128
    worlds = {s.get("world") for s in manifest()["content_scripts"]}
    assert "MAIN" in worlds


def test_the_scripts_go_through_one_cross_browser_namespace():
    # Firefox's promise API is on `browser`; Chrome has only `chrome`. The
    # shim picks one, and everything after it has to use the shim -- a stray
    # chrome.* call returns undefined in Firefox and the await never settles.
    for name in ("background.js", "bridge-iso.js"):
        body = code(name)
        assert 'typeof browser !== "undefined" ? browser : chrome' in body, name
        assert not re.findall(r"(?<![.\w])chrome\.\w+", body), \
            "%s still calls chrome.* directly" % name


def test_the_launcher_call_does_not_rely_on_chrome_only_error_reporting():
    # runtime.lastError does not exist in Firefox's browser API: a failure
    # there is a rejected promise, and a callback would never be told.
    body = code("background.js")
    assert "lastError" not in body
    assert "await api.runtime.sendNativeMessage" in body


def test_the_page_world_bridge_touches_no_extension_api():
    # It runs in the page, where neither namespace exists on any browser.
    body = code("bridge-main.js")
    assert not re.findall(r"(?<![.\w])(chrome|browser)\.\w+", body)


def test_the_icons_are_declared_at_every_size_both_engines_ask_for():
    m = manifest()
    for block in (m["icons"], m["action"]["default_icon"]):
        assert set(block) == {"16", "32", "48", "128"}
        for rel in block.values():
            assert os.path.exists(os.path.join(EXTENSION, rel)), rel
