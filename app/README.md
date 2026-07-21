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
python -m pytest tests/ -q      # 168 passing
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
| 3. Overlay | renders live state; drag, position memory and window styling verified live |
| 4. Setup, Claude picking, ratings | Claude picking verified live; first-run setup built and tested |

What used to be the gaps list, with how each one closed:

- **First-run setup now exists.** The first five-star with nothing configured
  opens a picker in the overlay, fed by the library's editable playlists
  (name and track count). Choosing one writes `starred_playlist` to
  `config.json` and immediately adds the track that prompted it; dismissing
  keeps the rating and asks again next time. If the extension is down or the
  listing fails, the old "no starred playlist configured" notice stands.
- **Reload recovery proven live.** A forced tab reload mid-track: in-flight
  commands failed cleanly ("the tab reloaded mid-command"), the fresh page
  re-announced, and music was audibly playing again about five seconds later
  with no human help.
- **35 minutes unattended, proven.** Seventeen transitions, the connection
  never dropped, no stalls, no autoplay blocks, no human input. The soak also
  caught a real bug the mocks never could: on most natural track ends the
  *next* track played ~3 seconds and was then skipped. Cause: swapping the
  queue makes MusicKit fire an ended state after `lastItemId` already points
  at the new song, so the echo wears the new track's id and walks through the
  foreign-trackEnded guard. Fixed in the bridge (`suppressEndedUntil`: ended
  states are gagged for 5s after a deliberate play). The fix rides in on the
  next DJ-tab reload -- success looks like tracks no longer dying at 3
  seconds after a natural end.
- **Out of the taskbar and Alt+Tab.** WS_EX_TOOLWINDOW is now set from a
  watcher thread started ahead of `webview.start()`, so the style lands
  before the first show and the button never appears. Verified on the live
  window. Setting it on an already-shown window is also safe -- what is NOT
  safe is hiding and re-showing to force a style re-read; that loses the
  window and stays banned.
- **Drag and remembered position verified.** Moving the window saves x/y
  (0.6 s debounce) and a restart restores them.
- `trackCount` fixed: library playlists have no documented trackCount
  attribute (checked against Apple's API docs), so the listing now asks for
  `include=tracks` and falls back to `relationships.tracks.meta.total`.
- `previewOnly` reviewed against the MusicKit v3 docs: the
  `musicUserToken`/`isAuthorized` check with the `previewOnly` override is
  correct as written. It still has never fired live, since the subscription
  is active.

Limitations rather than bugs: there is no frosted blur (WebView2 paints over
the acrylic, so uniform alpha is the ceiling -- though the alpha now eases
over ~150 ms instead of snapping), and a Claude batch takes ~17s (nothing
waits on it -- the queue refills mid-track).

## Things that cost real time

- Content scripts do not reach a tab that was already open when the extension
  loaded. The worker injects on demand instead.
- Chrome and Edge refuse audio until the tab has had a real click, and Edge's
  "Limit" autoplay default re-blocks it.
- Apple Music registers `beforeunload`, so reloading the DJ tab throws up a
  blocking "Reload site?" dialog first. While it is up the page's JS is
  frozen -- position stops, commands hang -- which reads as a mysterious hang
  rather than as a dialog. Earlier "killed by autoplay" reload attempts were
  probably this.
- `claude` on Windows is a `.CMD`, so it runs through `cmd.exe`, which cuts a
  multi-line argument at the first newline. The prompt goes over stdin.
- pywebview's `resize()` is clamped by WinForms' minimum width: ask for 72 and
  you get 232. `SetWindowPos` is not.
- Two top-level windows carry the overlay's title, and `FindWindowW` returns
  the hidden one during startup.
- The overlay is a browser too, so the daemon's origin guard has to let
  loopback through -- matched on the parsed hostname, since
  `127.0.0.1.example.com` is a registrable domain.
