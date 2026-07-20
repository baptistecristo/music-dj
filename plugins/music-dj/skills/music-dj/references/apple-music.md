# Browser control of the Apple Music web player

Tested snippets for driving music.apple.com through Claude in Chrome's
`javascript_tool`. All run in the page context of the DJ tab. `tabId` is the
DJ tab's id from `tabs_context_mcp`.

## Ground rules (learned the hard way)

- **Never `await mk.play()`** — it can hang the evaluation for 45s+. Call
  `mk.play()` fire-and-forget, wait ~3s, then poll state.
- **Chrome autoplay policy:** a tab that has never had a real user click
  cannot emit audio. If `playbackState` sticks at 1 (loading) with
  `currentPlaybackTime` 0, ask the user to click play once in the tab; after
  that, script-driven playback works for the rest of the browser session.
- **`previewOnly: true`** means the web player doesn't see an active Apple
  Music subscription (wrong Apple ID, or subscription check pending). Songs
  will only play 30-second previews or nothing. Ask the user to check which
  Apple ID is signed in. After a reload with a valid subscription it becomes
  false.
- **Page reloads wipe everything**: the title observer, the MusicKit queue.
  Re-apply the title snippet after any navigation; re-seed the queue if the
  DJ was mid-set.
- Screenshots can fail on some machines ("Failed to deserialize
  params.clip.scale"). Fall back to `find`, `read_page`, `get_page_text`,
  and `javascript_tool` — they keep working.
- Playback state codes: 0 none, 1 loading, 2 playing, 3 paused.

## Keep the tab named "DJ"

```js
document.title = "DJ";
if (!window.__djTitleObserver) {
  const t = document.querySelector("title");
  window.__djTitleObserver = new MutationObserver(() => { if (document.title !== "DJ") document.title = "DJ"; });
  window.__djTitleObserver.observe(t, {childList: true, characterData: true, subtree: true});
}
```

## Status check

```js
const mk = MusicKit.getInstance();
JSON.stringify({state: mk.playbackState, previewOnly: !!mk.previewOnly,
  authorized: mk.isAuthorized,
  now: mk.nowPlayingItem ? (mk.nowPlayingItem.title + " — " + mk.nowPlayingItem.artistName) : null,
  time: Math.round(mk.currentPlaybackTime), queue: mk.queue.length})
```

## Play a seed song with autoplay (the core DJ move)

```js
const mk = MusicKit.getInstance();
const H = {Authorization: "Bearer " + mk.developerToken, "Music-User-Token": mk.musicUserToken};
const sf = mk.storefrontId || "fr";
const r = await fetch(`https://amp-api.music.apple.com/v1/catalog/${sf}/search?term=${encodeURIComponent("SEED SONG + ARTIST HERE")}&types=songs&limit=3`, {headers: H}).then(x => x.json());
const song = r.results?.songs?.data?.[0];
mk.autoplayEnabled = true;               // Apple continues with similar songs
await mk.setQueue({song: song.id});
mk.play();                                // do NOT await
JSON.stringify({queued: song?.attributes?.name + " — " + song?.attributes?.artistName});
```

Then poll the status snippet after ~3s to confirm `state: 2`.

## Transport

```js
const mk = MusicKit.getInstance();
mk.pause();                // pause
mk.play();                 // resume (no await)
mk.skipToNextItem();       // skip
mk.skipToPreviousItem();   // back
```

## Library scan (taste profiling)

Artists (paginated, ~100/page):

```js
const mk = MusicKit.getInstance();
const H = {Authorization: "Bearer " + mk.developerToken, "Music-User-Token": mk.musicUserToken};
const artists = [];
let next = "/v1/me/library/artists?limit=100";
for (let i = 0; i < 5 && next; i++) {
  const d = await fetch("https://amp-api.music.apple.com" + next, {headers: H}).then(r => r.json());
  (d.data || []).forEach(a => artists.push(a.attributes?.name));
  next = d.next;
}
JSON.stringify(artists.slice(0, 60))   // slice — tool output truncates near ~1500 chars
```

Genre distribution and recent plays:

```js
// genres: aggregate attributes.genreNames over /v1/me/library/songs?limit=100 pages
// recent: /v1/me/recent/played/tracks?limit=25 → attributes.artistName + name
// heavy rotation: /v1/me/history/heavy-rotation
```

Return compact JSON and slice results — large outputs get truncated, so pull
the library in chunks across multiple javascript_tool calls.
