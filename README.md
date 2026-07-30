# 🎧 music-dj

**An AI DJ for Claude.** It learns your taste from your own library, reads
what kind of work you are doing, and picks songs to match. Tests start
failing and the music calms down. You ship and it finds momentum. There are
no playlists to maintain, because it chooses one track at a time.

Works with **Apple Music, Spotify, SoundCloud, YouTube Music, Deezer, Tidal,
Amazon Music, Qobuz, Bandcamp and Pandora**.

## How it works

**It learns your taste.** On first run the agent reads your library through
your service's web player — artists, genres, what you have been playing —
and writes a profile to `~/.music-dj/taste-profile.md`. Everything stays on
your machine.

**It reads the room.** Hooks classify what you are doing from your tool
calls: failing tests, passing builds, writing docs, reading around. Weighted
and debounced, so the music does not flap every time you save a file.

**It plays through your browser.** The DJ drives your service's web player in
a tab of its own. That is what lets it work on Windows, where the Apple Music
and Spotify desktop apps cannot be scripted at all. On macOS with Apple Music
there is also a native AppleScript mode.

**It takes requests.** "Play something", "calmer please", "skip", "put on
some French rap", "what's playing?", "stop DJ-ing today".

**It learns from what you do next.** Stars and skips feed back into the
picks — per lane, carried up to the artist, and faded with age. See
[app/README.md](app/README.md#how-it-learns) for the rules.

## What it runs on

| | Supported | Notes |
|---|---|---|
| **Windows 10/11** | ✅ | Acrylic glass overlay, no taskbar button |
| **macOS** | ✅ | Plain frameless overlay; the DWM glass is Windows-only |
| **Linux** | ✅ | Same; needs a GTK or Qt pywebview backend |
| **Chrome, Edge, Brave, Vivaldi, Opera** | ✅ | One Chromium build covers all of them |
| **Firefox** | ✅ | 128 or newer — that is when content scripts reached the MAIN world |
| **Safari** | ❌ | Would need repackaging as a Safari Web Extension through Xcode |
| **Phones, tablets** | ❌ | The DJ is a local process driving a browser on the same machine |

## Install

### Windows (PowerShell)

```powershell
git clone https://github.com/baptistecristo/music-dj.git
cd music-dj
.\install.ps1
```

### macOS / Linux

```bash
git clone https://github.com/baptistecristo/music-dj.git
cd music-dj
./install.sh
```

The installer asks which music service you use, saves it, installs the plugin
into [Claude Code](https://claude.com/claude-code), and prints a short guide
for that service. You can also install it from inside Claude Code:

```
/plugin marketplace add baptistecristo/music-dj
/plugin install music-dj@music-dj
```

### Prerequisites

- [Claude Code](https://claude.com/claude-code) (`npm install -g
  @anthropic-ai/claude-code`)
- Python 3, for the mood hooks. On Windows install it from
  [python.org](https://www.python.org/downloads/) or the Microsoft Store; the
  hooks try `python3` and fall back to `python`.
- A browser from the table above, with the **Claude in Chrome** extension
  installed *in that browser*. The installer asks which one to use.
- An account on your music service. Which tier you need depends on the
  service; the installer's guide says.

## First run

Open a terminal, run `claude`, and say:

```
set up my music DJ
```

The agent opens your service in its own tab, asks you to sign in — it never
touches your credentials — reads your library, writes the profile, and plays
something matched to what you are doing. After that it DJs from any Claude
session, including a cloud one from your phone, as long as the browser is
open on the machine with the speakers.

## The standalone app

`app/` is a self-contained version for Apple Music: a Python daemon, a
browser extension, and a small always-on-top overlay. It runs without Claude
Code once started. See [app/README.md](app/README.md) for how the picking and
the learning work.

Setting it up takes two steps beyond the clone:

```bash
python app/host/register.py       # teach the browser to start the DJ
python app/host/register.py --list  # or just see where that would write
```

Then load `app/extension/` as an unpacked extension:

- **Chromium** — `chrome://extensions` → Developer mode → Load unpacked
- **Firefox** — `about:debugging#/runtime/this-firefox` → Load Temporary
  Add-on → pick `manifest.json`

Clicking the toolbar icon starts the DJ, opens the player tab and brings it
to the front. Clicking it again stops everything.

## Repo layout

```
install.ps1 / install.sh          onboarding scripts
.claude-plugin/marketplace.json   makes this repo a Claude Code marketplace
plugins/music-dj/                 the plugin itself
  skills/music-dj/                the DJ brain + per-service control references
  hooks/                          activity → mood classification
  server/                         MCP server (config anywhere; AppleScript on macOS)
  lib/                            shared config/classifier/control library
app/                              the standalone app (see app/README.md)
  extension/                      the hands, inside the web player
  daemon/                         the brain: mood, queue, taste, Claude picking
  overlay/                        pywebview mini player
  host/                           native messaging host + registration
  tools/                          CLI driver + transport smoke test
  tests/                          app test suite (browser mocked)
tests/                            plugin test suite
.github/workflows/                CI: both suites on 3 OSes, installer lint
```

## Privacy

- The taste profile and all config live in `~/.music-dj/` on your machine.
- The DJ never handles your password. You sign in to your service yourself.
- Nothing from your library is sent anywhere by this plugin.

## License

MIT
