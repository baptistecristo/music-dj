# 🎧 music-dj

**An AI DJ for Claude.** It scans your music library to learn your taste,
reads your mood from *how you're writing* — excited, frustrated, locked in,
winding down — and plays music that fits. Tests start failing? The music
calms down. You ship? Momentum. No playlists to maintain: it picks songs.

Works with **Apple Music, Spotify, SoundCloud, YouTube Music, Deezer,
Tidal, Amazon Music, Qobuz, Bandcamp, and Pandora**.

## How it works

- **Learns your taste.** On first run the agent scans your library through
  your service's web player — artists, genre distribution, recent plays —
  and writes a taste profile to `~/.music-dj/taste-profile.md`. Your data
  stays on your machine; nothing is uploaded anywhere.
- **Reads the room.** The DJ infers mood from your writing and (in Claude
  Code) from what's happening: hooks classify every tool call — failing
  tests, passing builds, doc writing, research — with debouncing so the
  music doesn't flap.
- **Plays through your browser.** The DJ drives your service's web player
  in a tab named **DJ**, in the browser you pick at install (Chrome, Edge,
  Brave, Arc, Opera or Vivaldi), via the Claude in Chrome extension — so it
  works from any Claude interface, on Windows too (where the Apple Music /
  Spotify desktop apps can't be scripted at all). On macOS with Apple
  Music, there's also a native AppleScript mode.
- **Takes requests.** "Play something", "calmer please", "skip", "put on
  some French rap", "what's playing?", "stop DJ-ing today".

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

The installer asks which music service you use, saves it, installs the
plugin into [Claude Code](https://claude.com/claude-code), and prints a
short service-specific guide. You can also install manually inside Claude
Code:

```
/plugin marketplace add baptistecristo/music-dj
/plugin install music-dj@music-dj
```

### Prerequisites

- [Claude Code](https://claude.com/claude-code) (`npm install -g
  @anthropic-ai/claude-code`)
- A Chromium browser — Chrome, Edge, Brave, Arc, Opera or Vivaldi — with the
  **Claude in Chrome** extension installed *in that browser* (for playback
  control). The installer asks which one you want the DJ to use.
- An account on your music service (subscription tiers: see the guide the
  installer prints)

## First run

Open a terminal, run `claude`, and say:

```
set up my music DJ
```

The agent opens your service in a tab named "DJ", asks you to sign in
(it never touches your credentials), scans your whole library, learns your
taste, saves the profile locally, and plays a first song matched to your
mood. From then on it DJs from any Claude session — terminal, desktop, even
your phone via a cloud session, as long as your chosen browser is open on the
machine with the speakers.

## Repo layout

```
install.ps1 / install.sh      onboarding scripts
.claude-plugin/marketplace.json   makes this repo a Claude Code marketplace
plugins/music-dj/             the plugin itself
  skills/music-dj/            the DJ brain + per-service control references
  hooks/                      activity → mood classification (debounced)
  server/                     MCP server (config anywhere; AppleScript on macOS)
  lib/                        shared config/classifier/control library
```

## Privacy

- The taste profile and all config live in `~/.music-dj/` on your machine.
- The DJ never handles your passwords — you sign in to your service
  yourself.
- Nothing from your library is sent anywhere by this plugin.

## License

MIT
