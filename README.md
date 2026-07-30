# 🎧 music-dj

**Music that follows your work.** It watches what you are doing, works out
what that feels like, and plays something that fits. Your tests start
failing and it puts on something calm. You ship a feature and it finds
momentum. You never build a playlist, because it chooses one song at a time
and learns from what you do with each one.

Works with Apple Music, Spotify, SoundCloud, YouTube Music, Deezer, Tidal,
Amazon Music, Qobuz, Bandcamp and Pandora.

---

## The problem, for anyone

Music apps will not let a program control them. Apple Music and Spotify have
desktop apps you cannot script on Windows at all. So the usual approach,
"tell the music app what to play", is closed off before you start.

And even with control, you would still have to answer the harder question:
what should play right now? A playlist cannot know you are twenty minutes
into a bug. Recommendation engines like Spotify's work by comparing you to
millions of other listeners, which is not available to one person on one
laptop.

This solves both. Here is how.

---

## Five problems worth reading about

### 1. Playing music with no way in

There is no back door, so the DJ goes through the front: it drives the same
web player you would use yourself, from inside the page.

A browser extension injects a script into the music site, in the same
JavaScript world as the site's own code. From there it can call the player.
The site's own access tokens are used to search the catalogue and never
leave the page: not logged, not saved, not passed to the program driving it.

The catch is that a browser deliberately makes this hard: extension code and
page code are kept apart, and neither can see the other's variables. The
bridge crosses that gap in two hops, page to extension to a local program,
each with its own rules about what may pass.

### 2. Knowing what you are doing

Every action in [Claude Code](https://claude.com/claude-code) fires a hook.
Editing files, running tests, reading documentation and watching a build
fail all look different, so each one votes for a mood.

Votes are weighted and decay. A single failing test does not count as a
crisis, and one passing test does not mean the crisis is over. Without that,
the music changed every thirty seconds, which is worse than the wrong music.

### 3. Learning taste from almost nothing

Spotify knows what you like by finding listeners similar to you. With one
user there is nobody to compare against, and giving a song five stars
teaches you about that one song out of tens of millions. You will not meet
it again for a year.

Three ideas make a handful of ratings go further, borrowed from
[troi](https://github.com/metabrainz/troi-recommendation-playground), the
open source engine behind ListenBrainz:

**Group the moods that want the same music.** Coding and researching both
want low-vocal instrumental, so a star given while coding counts while
researching. Kept apart, five labels split the evidence so thin that most
batches saw none of it.

**Judge the artist, not the track.** A verdict on one song is spent the
moment that song ends. Carried up to whoever made it, one star starts
shaping the picks for songs you have never heard. It counts half, because it
is a weaker claim, and it leaves out that song's own record. Counting that
twice made one bad afternoon look like a pattern.

**Let old opinions fade.** A verdict is worth half as much after 45 days.
What you skipped in March should not still be deciding your July.

Then Claude picks the batch, given your profile, the mood and everything
above. When Claude is slow or unavailable the profile picks on its own, so
the music never stops waiting for a model.

### 4. A window that behaves like furniture

The player is a small album cover that sits above everything, has no title
bar, stays out of Alt+Tab and the taskbar, and dissolves when you pause.

Windows gives you that through the compositor: acrylic blur, per-window
transparency, and a style that makes the window ignore the mouse while it is
invisible. Get it wrong and the window is gone for good, because hiding a
window of this kind can lose it permanently. macOS and Linux share none of
those APIs, so the same window runs there on the cross-platform layer alone
and says so rather than pretending.

### 5. Making one codebase run everywhere

Three operating systems and seven browsers, each disagreeing about
something:

- The overlay **crashed on import** on macOS and Linux. The module that
  describes Windows data types raises an error on other systems instead of
  simply being empty.
- The play and pause icons came from a font that ships only with Windows.
  Everywhere else they were empty boxes. They are drawings now.
- Starting a background program takes opposite arguments on Windows and
  everywhere else. Passing the Windows ones elsewhere is an error, not a
  no-op.
- Teaching a browser to launch a local program means a registry key on
  Windows and a file in a different directory for every browser on macOS and
  Linux. One script now writes all of them.
- Firefox spells half the extension API differently and returns a different
  kind of value from the same call.

---

## How the pieces fit

```
   Claude Code            this app                        your browser
   ───────────            ────────                        ────────────

   what you're    ──▶  ~/.music-dj/    ──▶   daemon   ══▶   extension
   doing now           state.json             │      ws        │
   (hooks)                                    │               │ injects
                                              │               ▼
                       taste profile   ──▶    │            the page's
                       ratings, skips         │           own player
                                              │               │
                                    ws        ▼               ▼
                             overlay ◀────────┘            speakers
                          (what's playing,
                           stars, controls)
```

The daemon is the only piece that decides anything. The extension is hands,
the overlay is a face, and both talk to it over local sockets that refuse
connections from anywhere but this machine.

---

## What runs where

| | Works | Notes |
|---|---|---|
| **Windows 10/11** | ✅ | Frosted overlay, no taskbar button. Developed and used here |
| **macOS** | ✅ | Plain overlay; the frosted glass is a Windows API |
| **Linux** | ✅ | Same, with a GTK or Qt backend |
| **Chrome, Edge, Brave, Vivaldi, Opera** | ✅ | One build covers all five |
| **Firefox** | ✅ | 128 or newer |
| **Safari** | ❌ | Needs repackaging through Xcode |
| **Phones, tablets** | ❌ | The DJ runs beside your speakers, not in the cloud |

macOS, Linux and Firefox are covered by the test suite on every push. They
have not been driven by hand, and the README would rather say so.

---

## Install

**Windows**

```powershell
git clone https://github.com/baptistecristo/music-dj.git
cd music-dj
.\install.ps1
```

**macOS / Linux**

```bash
git clone https://github.com/baptistecristo/music-dj.git
cd music-dj
./install.sh
```

The installer asks which music service you use, installs the plugin into
Claude Code, and prints a guide for that service. Then run `claude` and say:

```
set up my music DJ
```

It opens your service in a tab, asks you to sign in, reads your library,
writes your taste profile, and starts playing.

You need Claude Code, Python 3, one of the browsers above with the Claude in
Chrome extension, and an account on your music service.

There is also a **standalone app** for Apple Music that runs without Claude
Code once started, with its own overlay and one-click launch from the
browser toolbar. See [app/README.md](app/README.md).

---

## Engineering notes

- **275 automated tests**, run on Windows, macOS and Linux on every push. The
  browser is mocked, so playback, queueing, mood changes, ratings and the
  whole learning model are tested without a browser open.
- **Failure is designed for.** A missing model, a timed-out search, a
  reloaded tab, a dead track, two racing commands: each has a path that
  keeps music playing. The rule throughout is that the music never stops
  because something upstream was unhelpful.
- **The comments explain why, not what.** Most of the hard parts here look
  like ordinary code until you know which bug put them there, so the code
  says.

## Privacy

- Your taste profile, ratings and history live in `~/.music-dj/` on your
  machine.
- The DJ never sees your password. You sign in to your music service
  yourself, in your own browser.
- Your library is never uploaded. When Claude picks the next batch, the
  prompt carries your taste profile, recent plays and ratings, the same as
  anything else you send Claude. Run the daemon with `--no-claude` and
  picking happens entirely on your machine.

## License

MIT
