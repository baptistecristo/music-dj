# Spotify control

Two modes. Browser mode needs no developer account; API mode controls any
Spotify device (including the desktop app) but needs a one-time developer
setup. Both need **Spotify Premium** for on-demand playback.

## Browser mode (default) — open.spotify.com

Drive the web player tab with Claude in Chrome. There is no page-level SDK
handle like MusicKit, so use UI automation:

- Navigate to `https://open.spotify.com`; user signs in themselves.
- **Search & play a seed song**: navigate to
  `https://open.spotify.com/search/<urlencoded song + artist>`, use `find`
  to locate the top result's play control, click it. Enable autoplay
  ("Autoplay similar songs" in settings) so the vibe continues.
- **Transport**: `find` the player-bar Play/Pause/Next buttons and click, or
  send `space` (play/pause) once the tab has had a real user click.
- **Library scan**: page-scrape `open.spotify.com/collection/tracks` (Liked
  Songs) and `/collection/artists` with `get_page_text` while scrolling;
  collect artists and infer genres from artist pages.
- Tab title: same observer snippet as `apple-music.md` (set title "DJ").
- Autoplay policy quirk is identical: first audio needs one real click in
  the tab.

## API mode (optional, background control)

Spotify's Web API can start playback of any track on the user's active
device — no browser tab needed once configured.

One-time setup (the user does this, ~5 minutes):

1. Create an app at developer.spotify.com/dashboard (any name; redirect URI
   `http://127.0.0.1:8888/callback`).
2. Save client id + secret into `~/.music-dj/spotify.json`:
   `{"client_id": "...", "client_secret": "...", "refresh_token": ""}`
3. Authorize once with scopes
   `user-modify-playback-state user-read-playback-state user-library-read
   user-top-read user-read-recently-played` (any standard authorization-code
   flow walkthrough works); store the refresh token in the same file.

Then, from any environment with network access:

- Refresh access token: POST `https://accounts.spotify.com/api/token`
  (`grant_type=refresh_token`), basic-auth with client id/secret.
- Play a track: `PUT https://api.spotify.com/v1/me/player/play` with
  `{"uris": ["spotify:track:<id>"]}` — the track plays on the user's active
  device, desktop app included.
- Search seeds: `GET /v1/search?q=...&type=track&limit=3`.
- Taste scan: `/v1/me/top/artists`, `/v1/me/top/tracks`,
  `/v1/me/tracks` (liked songs), `/v1/me/player/recently-played`.
- Transport: `/v1/me/player/pause`, `/v1/me/player/next`,
  `/v1/me/player/queue`.

Never ask for or store the user's Spotify password; the API flow uses OAuth
in their own browser.
