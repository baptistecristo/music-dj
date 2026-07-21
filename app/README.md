# music-dj overlay

An always-on-top DJ for Windows that plays Apple Music matched to your working
mood, independent of any Claude session.

Built alongside the `music-dj` Claude Code plugin, not replacing it. The
plugin's hook keeps classifying activity into `~/.music-dj/state.json`; this
app reads that file and plays the music.

```
extension/   Chrome/Edge extension -- the hands, inside music.apple.com
daemon/      Python -- the brain: mood, queue, history, ratings, Claude picking
overlay/     pywebview mini player
tools/       throwaway CLI driver + transport smoke test
tests/       161 tests, browser mocked
```

## Running it

Three things, in this order.

**1. The extension**, once. `edge://extensions` (or `chrome://extensions`) →
Developer mode → **Load unpacked** → pick `app/extension`. Then open
<https://music.apple.com> and sign in; the tab title becomes **DJ**.

Allow autoplay for the site, or playback stops between tracks: click the icon
left of the address bar → **Permissions for this site** → **Media autoplay** →
**Allow**.

**2. The daemon.**

```
cd app
python -m daemon.main --verbose
```

**3. The overlay**, in a second terminal.

```
cd app
pythonw -m overlay.app        # no console window
python -m overlay.app         # with logs, when something is wrong
```

Useful flags: `--no-claude` picks from the profile only; `--solid` and
`--idle-alpha N` control the overlay's translucency.

## What it does

The daemon watches `state.json` for mood changes, asks Claude for a batch of
twelve tracks, resolves each to a catalog id through the extension, and
advances the queue when a track ends. Apple's own autoplay is never used --
curating is the point.

**Picks come from Claude**, prompted with your taste profile, the current mood,
recent plays, and the tracks you rated 5 and 1 star *in that mood*. Each pick
carries the reason Claude gave, and the overlay shows it verbatim. The taste
profile is the floor underneath: a missing CLI, a timeout or an unparseable
answer all fall through to it, so the music never stops because the model was
unhelpful.

**The overlay** is a 95px album cover, translucent, always on top and absent
from the taskbar. Hover it and it opens into a mini player: title, artist, the
why-line, a scrubber, transport, five stars and the mood chip.

**Ratings are per mood.** One star while debugging says nothing about a Friday
night. Five stars adds the track to a playlist you choose during setup, and
checks membership first so re-rating cannot duplicate.

## Two vocabularies, on purpose

The plugin hook classifies into `coding / writing / debugging / building /
research`. `taste-profile.md` organises seeds by feel: `energized / tense /
focus / mellow / loose`. `daemon/moods.py` maps between them:

| hook mood | lane |
|---|---|
| building | energized |
| debugging | tense |
| coding | focus |
| research | focus |
| writing | mellow |

The overlay's mood chip offers both, so you can ask for a feel rather than
describe what you are doing. `research → focus` is a judgement call: reading
docs wants the same low-vocal register as coding.

## Tests

```
python -m pytest tests/ -q      # 161 passing
python tools/transport_smoke.py # transport, with a mock extension
```

They mock the extension, so they prove the daemon's logic -- mood mapping,
queue advance, history dedupe, per-mood ratings, the no-duplicate-star rule,
Claude's prompt contents, and every fallback path. They prove nothing about
MusicKit actually playing.

## State of it

| Milestone | State |
|---|---|
| 1. Extension + CLI driver | verified against the live player |
| 2. Daemon, profile picking | plays and advances tracks live |
| 3. Overlay | renders live state; drag and rating unchecked |
| 4. Setup, Claude picking, ratings | Claude picking verified live; **first-run setup not built** |

Known gaps, roughly by how much they matter:

- **First-run setup does not exist.** Nothing writes `starred_playlist` into
  `config.json`, so five stars stores the rating and then reports that no
  playlist is configured. Add it by hand to wire up the rest:
  `{"starred_playlist": {"id": "p.xxxx", "name": "..."}}`
- **Reload recovery is unproven live.** The code is written and tested against
  mocks; both live attempts were killed by autoplay blocking before it ran.
- **Never run 30 minutes unattended.**
- **Drag and remembered position are unverified.** The drag mechanism was
  configured but untagged for a while, so it did nothing at all.
- `trackCount` on library playlists may come back empty; the setup picker
  wants it. `previewOnly` detection has never fired, since the subscription is
  active.

Limitations rather than bugs: there is no frosted blur (WebView2 paints over
the acrylic, so uniform alpha is the ceiling), the alpha snaps rather than
fades, and a Claude batch takes ~17s (nothing waits on it -- the queue refills
mid-track).

## Things that cost real time

- Content scripts do not reach a tab that was already open when the extension
  loaded. The worker injects on demand instead.
- Chrome and Edge refuse audio until the tab has had a real click, and Edge's
  "Limit" autoplay default re-blocks it.
- `claude` on Windows is a `.CMD`, so it runs through `cmd.exe`, which cuts a
  multi-line argument at the first newline. The prompt goes over stdin.
- pywebview's `resize()` is clamped by WinForms' minimum width: ask for 72 and
  you get 232. `SetWindowPos` is not.
- Two top-level windows carry the overlay's title, and `FindWindowW` returns
  the hidden one during startup.
- The overlay is a browser too, so the daemon's origin guard has to let
  loopback through -- matched on the parsed hostname, since
  `127.0.0.1.example.com` is a registrable domain.
