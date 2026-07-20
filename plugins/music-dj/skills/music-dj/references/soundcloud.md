# SoundCloud control

Browser mode only: soundcloud.com in the DJ tab via Claude in Chrome.
SoundCloud's public API is closed to new apps, so UI automation is the way.
A free account works (ad-supported); SoundCloud Go+ removes limits.

- Navigate to `https://soundcloud.com`; user signs in themselves.
- **Search & play a seed**: navigate to
  `https://soundcloud.com/search?q=<urlencoded terms>`, `find` the first
  result's play button, click it. SoundCloud's "Stations" (three-dot menu →
  "Start station") continue with similar tracks — prefer starting a station
  from the seed for the autoplay effect.
- **Transport**: the player bar sits at the bottom; `find` its
  play/pause/next controls. Keyboard: `space` toggles play once the tab has
  had a real click (autoplay policy — same as other services).
- **Library scan**: page-scrape `soundcloud.com/you/likes` (scroll +
  `get_page_text` in passes) and `soundcloud.com/you/following` for artists;
  infer genres from track tags shown on track pages.
- Tab title: same "DJ" observer snippet as `apple-music.md`.
- Quirk: SoundCloud is SPA-heavy; after navigation give it 2–3s before
  `find`, and re-apply the title observer.
