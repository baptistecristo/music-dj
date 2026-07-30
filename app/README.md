# music-dj overlay

An always-on-top DJ that plays Apple Music matched to your working mood,
independent of any Claude session. Runs on Windows, macOS and Linux, in any
Chromium browser or in Firefox 128+.

Built alongside the `music-dj` Claude Code plugin, not replacing it. The
plugin's hook keeps classifying activity into `~/.music-dj/state.json`; this
app reads that file and plays the music.

```
extension/   browser extension -- the hands, inside music.apple.com
daemon/      Python -- the brain: mood, queue, history, taste, Claude picking
overlay/     pywebview mini player
host/        native messaging host + registration
tools/       throwaway CLI driver + transport smoke test
tests/       the test suite, browser mocked
```

## Running it

Two one-time steps, then it lives in the toolbar.

**1. The extension**, once.

- Chromium (`chrome://extensions`, `edge://extensions`, `brave://extensions`)
  → Developer mode → **Load unpacked** → pick `app/extension`.
- Firefox (`about:debugging#/runtime/this-firefox`) → **Load Temporary
  Add-on** → pick `app/extension/manifest.json`. Firefox drops temporary
  add-ons when it closes; signing it through addons.mozilla.org is what makes
  it stick.

Then open <https://music.apple.com> and sign in; the tab title becomes **DJ**.

Allow autoplay for the site, or playback stops between tracks: click the icon
left of the address bar → **Permissions for this site** → **Media autoplay** →
**Allow**.

**2. The launcher**, once: run `python app/host/register.py`. It registers the
native messaging host that lets the browser start the DJ, for every browser it
finds — the Windows registry under HKCU, or the per-browser manifest directory
on macOS and Linux. No admin anywhere. Re-run it if the folder ever moves, and
run it with `--list` to see where it would write without writing anything.

**From then on, the extension icon is the switch.** Click it and the
launcher starts the daemon and the overlay -- no consoles, no desktop
shortcut -- and the extension opens its own pinned Apple Music tab if none is
there, bringing it to the front so the first click that unlocks audio has
something to land on. The badge reads **ON** while the daemon is connected.
Click again to stop: the music pauses, the overlay closes, the daemon exits,
the badge clears. Clicking twice fast cannot start two daemons; the launcher
checks the port first. An **ERR** badge means the host is not registered --
run step 2 and reload the extension.

`app\start.cmd` (or `app/start.sh`) still does the same launch from the
desktop, when you want it without the browser round-trip. When something is
wrong, run the pieces by hand to see the logs:

```
cd app
python -m daemon.main --verbose
python -m overlay.app         # second terminal
```

Useful flags: `--no-claude` picks from the profile only; `--solid` and
`--idle-alpha N` control the overlay's translucency.

## What it does

The daemon watches `state.json` for mood changes, asks Claude for a batch of
twelve tracks, resolves each to a catalog id through the extension, and
advances the queue when a track ends. Apple's own autoplay is never used --
curating is the point.

**Picks come from Claude**, prompted with your taste profile, the current mood,
recent plays, and what this lane has learned (below). Each pick carries the
reason Claude gave (kept in the queue data, not shown). The taste profile is
the floor underneath: a missing CLI, a timeout or an unparseable answer all
fall through to it, so the music never stops because the model was unhelpful.

**The overlay** is a 60px album cover, translucent, always on top and absent
from the taskbar. Hover it and it opens into a mini player: title, artist, a
scrubber, transport, five stars and the mood chip.

On Windows it is properly frosted, keeps out of Alt+Tab and the taskbar, and
fades to nothing when the music pauses. That is all Win32: DWM acrylic,
layered alpha, `WS_EX_TOOLWINDOW`, click-through. None of it has an
equivalent that macOS and Linux share, so there the same window runs on
pywebview alone — frameless, on top, its own background instead of the
system's blur, hidden and shown rather than faded. `overlay/app.py` returns
early from every Win32 call off Windows; the callers all had a fallback
already, because those calls can fail on the wrong Windows build too.

**Previous** restarts the song. Press it again within the first few seconds
and it goes back one — the playhead is at zero by then, so the second press
takes the other branch without anything counting clicks.

**Ratings are per mood.** One star while debugging says nothing about a Friday
night. Five stars adds the track to a playlist you choose during setup, and
checks membership first so re-rating cannot duplicate.

## How it learns

Every star and every skip lands in the store under the mood you were in.
`library.taste()` folds those into one view per lane. Three rules, taken from
how [troi](https://github.com/metabrainz/troi-recommendation-playground), the
ListenBrainz playlist engine, uses its own feedback.

**Pooled by lane.** `coding` and `research` both draw from `focus`, so a star
you give in one counts in the other. Split five ways, the evidence was thin
enough that most batches saw none of it.

**Carried up to the artist.** There are tens of millions of songs and you meet
the same one twice a year, so a verdict on a track is spent on a track that
never comes back. The artist is the one link already sitting on every row we
store, so a star for one song shapes the picks for songs you have never heard.
It counts half, capped, and leaves out that song's own record: counting it
twice made one track's bad run look like a pattern across everything the
artist ever did. Names match on their core, so "Daft Punk" and "Daft Punk
feat. Julian Casablancas" are one artist.

**Scored and faded.** Nothing gets banned outright. Candidates come back
best-first and only the strongly negative drop out. A verdict is worth half as
much 45 days on, so a bad afternoon in March is not still deciding your July.
Unrated stays neutral, as it always has.

The floor learns as well: the profile fallback drops seed artists this lane has
ruled out and leads with the ones it likes, keeping the shuffle inside each
band so you do not get the same favourite every time. Rewrite
`taste-profile.md` and the next refill reads it, rather than the next restart.

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
python -m pip install -r requirements-dev.txt   # test deps, once
python -m pytest tests/ -q                      # the full suite; all should pass
python tools/transport_smoke.py                 # transport, with a mock extension
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
