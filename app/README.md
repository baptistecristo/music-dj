# music-dj overlay

An always-on-top DJ for Windows that plays Apple Music matched to your working
mood, independent of any Claude session.

Built alongside the `music-dj` Claude Code plugin, not replacing it. The plugin's
hook keeps classifying activity into `state.json`; this app reads that and plays
the music.

```
extension/   Chrome extension -- the hands, inside music.apple.com
daemon/      Python -- the brain: mood, queue, history, ratings
overlay/     pywebview strip                              (milestone 3, not built)
tools/       throwaway CLI driver + transport smoke test
tests/       98 tests, browser mocked
```

## Status

| Milestone | State |
|---|---|
| 1. Extension + CLI driver | **written, needs a human to load it** (see below) |
| 2. Daemon, profile picking | written, passes 98 tests against a mock extension; **not yet run against real audio** |
| 3. Overlay | not started |
| 4. Setup, Claude picking, ratings | ratings + starred-playlist logic written and tested; setup UI and Claude picking not started |

Nothing here has played a note of real audio yet. Milestone 1 needs one manual
step that browser automation cannot do.

## Verifying milestone 1

Loading an unpacked extension goes through a native file-picker dialog, which no
automation can drive. So this part is yours:

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode**.
3. **Load unpacked** → pick the `app/extension` folder in this repo.
4. Open <https://music.apple.com> and sign in. The tab title should become **DJ**.
5. In a terminal:

   ```
   cd app
   python tools/cli_driver.py
   ```

   It should print `== extension connected` within a few seconds.

Then work through these, in order:

```
playlists                    # expect ~48 total, editable ones listed
search Folamour The Journey  # expect a catalog id back
play <catalogId>             # expect audio. If not, see below.
pause                        # audio stops
resume                       # audio resumes
skip
create dj-scratch            # a throwaway playlist -- never test against 🦅
add <playlistId> <catalogId> # verify in the web player it landed
```

Your starred target is likely an existing, well-used playlist with hundreds of
tracks in it. Nothing here writes to it until you pick it during first-run
setup, and the star path checks membership before adding, so re-starring a
track cannot duplicate it. Test adds against a throwaway playlist.

**If `play` produces no audio:** click play once manually in the DJ tab. Chrome
refuses audio in a tab that has never had a real user click. The driver prints an
`autoplayBlocked` event when it detects this.

**If everything comes back preview-only:** the web player sees no active
subscription -- check which Apple ID is signed in.

## Running the daemon (milestone 2)

```
python -m daemon.main --verbose
```

Serves `ws://127.0.0.1:8787` on two paths: `/bridge` (extension) and `/ui`
(overlay). It watches `~/.music-dj/state.json` for mood changes, builds a queue,
and advances it on `trackEnded`.

Picks currently come from `taste-profile.md` only. Claude picking is milestone 4;
this profile path stays as the mandatory fallback underneath it.

## Tests

```
python -m pytest tests/ -q      # 98 passing
python tools/transport_smoke.py # transport, with a mock extension
```

The tests mock the extension transport, so they prove the daemon's logic --
mood mapping, queue advance, history dedupe, per-mood ratings, the
no-duplicate-star rule, and every fallback path. They prove nothing about
MusicKit actually playing; only step 5 above does that.

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

Keeping both means the hook stays untouched and the profile stays the single
source of musical truth. `research → focus` is a judgement call: reading docs
wants the same low-vocal register as coding.
