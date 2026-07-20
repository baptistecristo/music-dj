# YouTube Music control

Browser mode only: music.youtube.com in the DJ tab via Claude in Chrome.
Free tier works (ads); Premium removes them. No supported public API for
playback.

- Navigate to `https://music.youtube.com`; user signs in themselves
  (Google account).
- **Search & play a seed**: navigate to
  `https://music.youtube.com/search?q=<urlencoded terms>`, `find` the top
  "Songs" result, click it. YouTube Music auto-continues with a radio of
  similar songs by default — ideal for the DJ pattern.
- Direct radio: on any song, the "Start radio" option seeds a station.
- **Transport**: player bar at the bottom; `find` play/pause/next. Keyboard
  shortcuts (`space`, `j`/`k`) work after the tab has one real click.
- **Library scan**: `music.youtube.com/library/songs` and
  `/library/artists` — scroll + `get_page_text` in passes; also "Listen
  again" rows on the home page reveal recent habits.
- Tab title: same "DJ" observer snippet as `apple-music.md`. YouTube Music
  rewrites the title on every track change — the observer handles it, but
  re-install it after any full page reload.
