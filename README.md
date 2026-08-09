# 🎧 music-dj

**Music that follows your work.** It watches what you are doing and plays
something that fits. Your tests start failing and it puts on something calm.
You ship a feature and it finds momentum. You never build a playlist,
because it chooses one song at a time and learns from what you do with each
one.

Works with Apple Music, Spotify, SoundCloud, YouTube Music, Deezer, Tidal,
Amazon Music, Qobuz, Bandcamp and Pandora.

---

## The problem

Music apps will not let a program control them. Apple Music and Spotify have
desktop apps you cannot script on Windows at all. So the usual approach dies
at the first step: you cannot tell the music app what to play.

And even with control, you would still have to answer the harder question:
what should play right now? A playlist cannot know you are twenty minutes
into a bug. Recommendation engines like Spotify's work by comparing you to
millions of other listeners, which is not available to one person on one
laptop.

---

## What made it hard

### 1. Playing music with no way in

There is no back door, so the DJ goes through the front: it drives the same
web player you would use yourself, from inside the page.

A browser extension slips a script into the music site, where it runs
alongside the site's own code and can call the player. The keys the site
uses to search its catalogue stay in the page. The program driving all this
never sees them.

A browser keeps extension code and page code apart on purpose, and neither
can read the other's variables. Crossing that gap takes two hops, page to
extension to a program on your machine, each with its own rules about what
may pass.

### 2. Knowing what you are doing

[Claude Code](https://claude.com/claude-code) announces each thing it does.
Editing a file, running tests, reading documentation and watching a build
fail all look different, so each one votes for a mood.

Each vote carries a weight and fades. One failing test is not a crisis, and
one passing test does not end one. Without that, the music changed every
thirty seconds, which is worse than the wrong music.

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

**Judge the artist as well as the track.** Rate one song and the verdict
dies with it. Carried up to whoever made it, one star starts
shaping the picks for songs you have never heard. It counts half, because it
is a weaker claim, and it leaves out that song's own record. Counting that
twice made one bad afternoon look like a pattern.

**Let old opinions fade.** A verdict is worth half as much after 45 days.
What you skipped in March should not still be choosing what plays in July.

Then Claude picks the batch, given your profile, the mood and everything
above. When Claude is slow or unavailable the profile picks on its own, so
the music never stops waiting for a model.

### 4. Understanding what you asked for out loud

The standalone app takes spoken instructions. Hold one key, say what you
want in French, let go, and the next songs answer you.

The tempting build is to teach the program French: a list of words meaning
calmer, another meaning louder. That breaks on the first sentence nobody
thought of, and every fix makes the list longer.

Nothing here reads French. A speech model on your laptop turns the recording
into a line of text, and that line goes to Claude next to your taste
profile, the mood, and what you played this afternoon. Claude was already
choosing songs from all of that. Now it reads your sentence too.

Two smaller things decide whether it feels like talking. The music drops to
a whisper while you hold the key rather than stopping, because a hole where
the music was distracts more than the music did. The key also has to reach
the DJ before the letter reaches whatever you were typing in.

The operating systems disagree about that second one. Windows reserves a
combination for you and swallows it whole, so the J never lands in your
document. It allows modifiers plus a single key and no more, which killed
the three-key chord this started as: pressing it would have sent a real
Alt+D to whatever was in front of you on the way to the J. macOS answers a
different question. It reports the character the key produced, and holding
Option turns J into `∆`, so a program watching for the letter waits forever
while you hold the key down. It also reports which physical key moved, and
that answer does not change, so the DJ reads both.

### 5. A window that behaves like furniture

The player is a small album cover that sits above everything, has no title
bar, stays out of Alt+Tab and the taskbar, and dissolves when you pause.

Windows will do all of that, through the same drawing layer that frosts the
taskbar: blur behind the window, transparency it controls itself, and a
setting that sends the mouse straight through while the window is invisible.
Hide a window of this kind the obvious way and you never get it back. None
of that exists on macOS or Linux, where the same window falls back to what
every system agrees on and looks plainer for it.

### 6. Making one codebase run everywhere

Three operating systems and seven browsers, each disagreeing about
something:

- The overlay **crashed on import** on macOS and Linux. The part of Python
  that describes Windows data types raises an error on other systems rather
  than coming up empty.
- The play and pause icons came from a font that ships only with Windows.
  Everywhere else they were empty boxes. They are drawings now.
- Starting a background program takes opposite arguments on Windows and
  everywhere else. Passing the Windows ones elsewhere is an error, not a
  no-op.
- Teaching a browser to launch a program on your machine means an entry in
  the Windows registry, and a file in a different folder for every browser
  on macOS and Linux. One script now writes all of them.
- Firefox spells half the extension commands its own way, and hands back a
  different kind of answer from the same call.

---

## How the pieces fit

There are four parts, and only one of them decides anything.

Claude Code writes down what you are working on. A program running in the
background reads that, together with your taste profile and your ratings,
and chooses what plays next. It tells the browser extension, and the
extension drives the page. A small album-cover window shows what is playing
and takes your stars and your skips.

The extension carries out what it is told and the window reports back.
Neither of them picks a song. Both reach the background program over
connections that refuse anything not coming from this machine.

---

## What runs where

| | Works | Notes |
|---|---|---|
| **Windows 10/11** | ✅ | Frosted overlay, no taskbar button. Developed and used here |
| **macOS** | ✅ | Plain overlay; the frosted glass is Windows-only |
| **Linux** | ✅ | Same, with a GTK or Qt window underneath |
| **Chrome, Edge, Brave, Vivaldi, Opera** | ✅ | One build covers all five |
| **Firefox** | ✅ | 128 or newer |
| **Safari** | ❌ | Needs repackaging through Apple's developer tools |
| **Phones, tablets** | ❌ | The DJ runs beside your speakers, not in the cloud |

The test suite covers macOS, Linux and Firefox on every push. Nobody has sat
down in front of them, which is worth knowing before you rely on it.

---

## Engineering notes

- **374 automated tests**, run on Windows, macOS and Linux on every push. The
  browser is mocked, so playback, queueing, mood changes, ratings and the
  whole learning model are tested without a browser open.
- **Every failure has a path back.** A missing model, a timed-out search, a
  reloaded tab, a dead track, two commands racing each other: each one ends
  with music still playing. Nothing upstream gets to stop the song.
- **The comments name the bug.** Most of the hard parts here read as ordinary
  code until you know what went wrong to put them there, so each one says.

## Privacy

- Your taste profile, ratings and history live in `~/.music-dj/` on your
  machine.
- The DJ never sees your password. You sign in to your music service
  yourself, in your own browser.
- Your library is never uploaded. When Claude picks the next batch, the
  prompt carries your taste profile, recent plays and ratings, the same as
  anything else you send Claude. Run the daemon with `--no-claude` and the
  picking never leaves your machine.
- The standalone app has a microphone. It opens while you hold the key and at
  no other time; nothing listens between presses, and there is no wake word.
- A model on your machine turns the clip into text. The recording never
  reaches a file, and it is gone as soon as the model has read it.
- That sentence goes to Claude in the batch prompt, next to your taste profile
  and recent plays, like everything else there.

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
Code once started, with its own overlay, one-click launch from the browser
toolbar, and a key you hold to tell it what you want out loud. See
[app/README.md](app/README.md).

## License

MIT
