"""The extension version is the DJ's version: it must not drift from the plugin."""

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXTENSION = os.path.join(REPO, "app", "extension", "manifest.json")
PLUGIN = os.path.join(REPO, "plugins", "music-dj", ".claude-plugin", "plugin.json")


def load(path):
    # utf-8-sig: the manifests are edited on Windows and may carry a BOM.
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def test_extension_version_matches_plugin_version():
    extension = load(EXTENSION)["version"]
    plugin = load(PLUGIN)["version"]
    assert extension == plugin, (
        "bump app/extension/manifest.json to %s: the version shown on the "
        "browser's extensions page is how you tell which build is loaded" % plugin
    )


def test_extension_version_is_three_numeric_parts():
    # Chrome refuses to load a manifest whose version is not 1-4 dot-separated
    # integers, so a typo here breaks the extension rather than a test.
    parts = load(EXTENSION)["version"].split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
