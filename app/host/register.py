"""One-time setup: tell the browser how to start the DJ.

    python app/host/register.py                    # every browser it can find
    python app/host/register.py --extension-id ID  # a differently-packed build
    python app/host/register.py --list             # where it would write

A native messaging host is a JSON manifest naming an executable the browser
may talk to. Where that manifest has to live is the one thing every platform
disagrees about:

- Windows: anywhere, with a registry value under HKCU pointing at it.
- macOS: a fixed directory per browser under ~/Library/Application Support.
- Linux: a fixed directory per browser under ~/.config (or ~/.mozilla).

And Firefox spells the permission differently from everyone else: Chromium
browsers list `allowed_origins` with a chrome-extension:// URL, Firefox lists
`allowed_extensions` with the add-on id.

Nothing here needs admin rights: every path is inside the user's own profile.
Safe to re-run, and worth re-running if this folder ever moves.
"""

import argparse
import json
import os
import stat
import sys

HOST_NAME = "com.music_dj.launcher"
HOST_DIR = os.path.dirname(os.path.abspath(__file__))

# The unpacked extension's id is pinned by the "key" in its manifest, so it is
# the same on every machine and can be baked in here.
DEFAULT_EXTENSION_ID = "leecnmkgmigdljlikhekgionlokgobna"
DEFAULT_GECKO_ID = "music-dj@baptistecristo.github.io"

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

# Where each browser reads native messaging manifests from. Relative to the
# user's home directory; the Windows entries are registry roots instead.
MAC_DIRS = {
    "Chrome": "Library/Application Support/Google/Chrome/NativeMessagingHosts",
    "Chrome Beta": "Library/Application Support/Google/Chrome Beta/NativeMessagingHosts",
    "Chromium": "Library/Application Support/Chromium/NativeMessagingHosts",
    "Edge": "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
    "Brave": ("Library/Application Support/BraveSoftware/Brave-Browser/"
              "NativeMessagingHosts"),
    "Vivaldi": "Library/Application Support/Vivaldi/NativeMessagingHosts",
    "Opera": "Library/Application Support/com.operasoftware.Opera/NativeMessagingHosts",
    "Firefox": "Library/Application Support/Mozilla/NativeMessagingHosts",
}

LINUX_DIRS = {
    "Chrome": ".config/google-chrome/NativeMessagingHosts",
    "Chrome Beta": ".config/google-chrome-beta/NativeMessagingHosts",
    "Chromium": ".config/chromium/NativeMessagingHosts",
    "Edge": ".config/microsoft-edge/NativeMessagingHosts",
    "Brave": ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
    "Vivaldi": ".config/vivaldi/NativeMessagingHosts",
    "Opera": ".config/opera/NativeMessagingHosts",
    "Firefox": ".mozilla/native-messaging-hosts",
}

WINDOWS_KEYS = {
    "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "Chromium": r"Software\Chromium\NativeMessagingHosts",
    "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    "Vivaldi": r"Software\Vivaldi\NativeMessagingHosts",
    "Firefox": r"Software\Mozilla\NativeMessagingHosts",
}

FIREFOX = "Firefox"


def target_dirs(platform=None, home=None):
    """{browser: directory} for this platform. Empty on Windows, which uses
    the registry rather than fixed directories."""
    platform = platform or sys.platform
    home = home or os.path.expanduser("~")
    if platform == "win32":
        return {}
    table = MAC_DIRS if platform == "darwin" else LINUX_DIRS
    return {name: os.path.join(home, *rel.split("/"))
            for name, rel in table.items()}


def manifest(browser, launcher_path, extension_id=DEFAULT_EXTENSION_ID,
             gecko_id=DEFAULT_GECKO_ID):
    """The host manifest, in the dialect this browser speaks."""
    body = {
        "name": HOST_NAME,
        "description": "Starts the music-dj daemon and overlay",
        "path": launcher_path,
        "type": "stdio",
    }
    if browser == FIREFOX:
        body["allowed_extensions"] = [gecko_id]
    else:
        body["allowed_origins"] = ["chrome-extension://%s/" % extension_id]
    return body


def write_launcher_shim():
    """A small executable the browser can run without knowing about python.

    Windows gets a .bat because the registry value has to name something
    CreateProcess accepts; POSIX gets a shell script marked executable, which
    is what the manifest's `path` must point at there.
    """
    python = sys.executable
    entry = os.path.join(HOST_DIR, "launcher.py")
    if WINDOWS:
        path = os.path.join(HOST_DIR, "launcher.bat")
        body = '@echo off\r\n"%s" "%s"\r\n' % (python, entry)
        with open(path, "w", encoding="ascii", newline="") as fh:
            fh.write(body)
        return path

    path = os.path.join(HOST_DIR, "launcher.sh")
    body = '#!/bin/sh\nexec "%s" "%s"\n' % (python, entry)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    # The browser execs this directly. Without the bit set it fails with a
    # permission error the browser reports only as "host not found".
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP |
             stat.S_IXOTH)
    return path


def write_manifest(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # No BOM: Chrome rejects a host manifest that carries one, and reports it
    # as the host simply not existing.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(body, fh, indent=2)
        fh.write("\n")


def register_windows(launcher_path, extension_id, gecko_id):
    import winreg

    installed = []
    shared = os.path.join(HOST_DIR, "%s.json" % HOST_NAME)
    firefox = os.path.join(HOST_DIR, "%s.firefox.json" % HOST_NAME)
    write_manifest(shared, manifest("Chrome", launcher_path, extension_id))
    write_manifest(firefox, manifest(FIREFOX, launcher_path, extension_id,
                                     gecko_id))

    for browser, key in WINDOWS_KEYS.items():
        target = firefox if browser == FIREFOX else shared
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  "%s\\%s" % (key, HOST_NAME)) as handle:
                winreg.SetValueEx(handle, None, 0, winreg.REG_SZ, target)
            installed.append((browser, target))
        except OSError as exc:
            print("  skipped %s (%s)" % (browser, exc))
    return installed


def register_posix(launcher_path, extension_id, gecko_id):
    installed = []
    for browser, directory in sorted(target_dirs().items()):
        # Only where the browser itself is installed. Creating the directory
        # for a browser that is not there would work, and would also be
        # litter in somebody's profile.
        parent = os.path.dirname(directory)
        if not os.path.isdir(parent):
            continue
        path = os.path.join(directory, "%s.json" % HOST_NAME)
        write_manifest(path, manifest(browser, launcher_path, extension_id,
                                      gecko_id))
        installed.append((browser, path))
    return installed


def main(argv=None):
    parser = argparse.ArgumentParser(prog="register.py", description=__doc__)
    parser.add_argument("--extension-id", default=DEFAULT_EXTENSION_ID)
    parser.add_argument("--gecko-id", default=DEFAULT_GECKO_ID)
    parser.add_argument("--list", action="store_true",
                        help="show where it would write, and change nothing")
    args = parser.parse_args(argv)

    if args.list:
        if WINDOWS:
            for browser, key in sorted(WINDOWS_KEYS.items()):
                print("%-12s HKCU\\%s\\%s" % (browser, key, HOST_NAME))
        else:
            for browser, directory in sorted(target_dirs().items()):
                mark = " " if os.path.isdir(os.path.dirname(directory)) else "-"
                print("%s %-12s %s" % (mark, browser, directory))
            print("\n('-' means that browser's profile is not on this machine)")
        return 0

    launcher_path = write_launcher_shim()
    print("Launcher: %s" % launcher_path)

    if WINDOWS:
        installed = register_windows(launcher_path, args.extension_id,
                                     args.gecko_id)
    else:
        installed = register_posix(launcher_path, args.extension_id,
                                   args.gecko_id)

    if not installed:
        print("No browser profiles found. Install one, then re-run this.")
        return 1
    for browser, where in installed:
        print("  %-12s %s" % (browser, where))
    print("\nRegistered %s for %d browser(s)." % (HOST_NAME, len(installed)))
    print("Chromium extension id: %s" % args.extension_id)
    print("Firefox add-on id:     %s" % args.gecko_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
