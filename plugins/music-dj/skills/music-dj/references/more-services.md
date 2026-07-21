# Other services — web player control notes

All follow the same browser-mode pattern as the four majors: one browser tab
kept as "DJ" (title-observer snippet in `apple-music.md`), user signs in
themselves, one real click unlocks audio, seed a song/station matching mood +
taste profile, prefer the service's radio/autoplay feature so similar songs
continue. Use `find` + `get_page_text`; give SPAs 2–3s after navigation.

## Deezer — deezer.com

- Search: `https://www.deezer.com/search/<terms>`; play top track result.
- "Flow" is Deezer's taste-based endless mix — great fallback when unsure;
  mood Flows (Chill, Motivation, Focus...) map nicely to DJ moods.
- Scan: Favorites → Tracks / Artists pages; Flow history on home.
- Subscription needed for on-demand full tracks.

## Tidal — listen.tidal.com

- Search: `https://listen.tidal.com/search?q=<terms>`; play top track.
- "Track radio" (context menu) continues similar songs.
- Scan: My Collection → Tracks / Artists.
- Subscription required.

## Amazon Music — music.amazon.com

- Search bar at top; play top song result. "Play similar" / station rows
  continue the vibe.
- Scan: Library → Songs / Artists; "Recently played" rows on home.
- Catalog access depends on tier (Prime shuffle limits vs Unlimited
  on-demand) — if playback keeps redirecting to stations, the account is on
  the Prime tier; tell the user and lean on stations.

## Qobuz — play.qobuz.com

- Search top bar; play top track. Weekly/discover pages for freshness.
- Scan: Favorites → Tracks / Artists / Albums.
- Subscription required. No radio/autoplay: queue 5–10 taste-matched tracks
  yourself instead of relying on autoplay.

## Bandcamp — bandcamp.com

- No subscription: streams full tracks from artist pages; buying is the
  point. Search `https://bandcamp.com/search?q=<terms>`; play from track or
  album pages (inline player).
- Scan: the user's collection page (`bandcamp.com/<username>`) and
  wishlist; genres from tags.
- No autoplay across artists — queue album-by-album; great for focus
  sessions (whole albums), and mention purchases support artists directly.

## Pandora — pandora.com (US only)

- Station-based by design: search an artist/song, start its station —
  Pandora handles the "similar songs" part natively.
- Thumb-up/down on the user's behalf ONLY when they explicitly react to a
  song ("love this" / "not this one").
- Scan: My Collection / thumbed-up tracks.
