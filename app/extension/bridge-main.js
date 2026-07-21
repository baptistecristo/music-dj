// Runs in the PAGE world on music.apple.com, so it can reach MusicKit.
//
// This is the only place the Apple developer token is ever touched. It stays in
// the page: never logged, never persisted, never forwarded to the daemon. The
// daemon asks for "search"; the answer is computed here.
//
// Talks to bridge-iso.js (isolated world) over window.postMessage, which in turn
// relays to the service worker. The page world cannot use chrome.* APIs.

(() => {
  "use strict";

  const OUT = "music-dj:from-page";
  const IN = "music-dj:to-page";
  const API = "https://amp-api.music.apple.com";

  // MusicKit playback states. Only 0-3 matter for our purposes, but 5/10 are
  // how a track signals it finished, which is what drives the queue.
  const S_NONE = 0, S_LOADING = 1, S_PLAYING = 2, S_PAUSED = 3;
  const S_ENDED = 5, S_COMPLETED = 10;

  let mk = null;
  let announcedReady = false;
  let lastState = null;
  let lastItemId = null;
  let lastPositionSent = 0;

  const send = (msg) => window.postMessage({ __musicdj: OUT, payload: msg }, "*");
  const emit = (evt, extra) => send(Object.assign({ evt }, extra || {}));

  // ---------------------------------------------------------------- MusicKit

  function instance() {
    try {
      if (window.MusicKit && typeof window.MusicKit.getInstance === "function") {
        return window.MusicKit.getInstance() || null;
      }
    } catch (_) {}
    return null;
  }

  function artworkUrl(artwork, size) {
    if (!artwork || !artwork.url) return null;
    const px = size || 120;
    return artwork.url.replace("{w}", px).replace("{h}", px)
                      .replace("{f}", "jpg").replace("{c}", "");
  }

  // amp-api call from inside the page. Origin is set by the browser; the tokens
  // are read fresh off the instance each time so a re-auth doesn't strand us.
  async function api(path, opts) {
    opts = opts || {};
    if (!mk) throw new Error("MusicKit not ready");
    const headers = {
      Authorization: "Bearer " + mk.developerToken,
      "Music-User-Token": mk.musicUserToken,
    };
    if (opts.body) headers["Content-Type"] = "application/json";
    const res = await fetch(API + path, {
      method: opts.method || "GET",
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error("HTTP " + res.status + " on " + path +
                      (detail ? " — " + detail.slice(0, 200) : ""));
    }
    if (res.status === 204) return {};
    const text = await res.text();
    return text ? JSON.parse(text) : {};
  }

  const storefront = () => mk && (mk.storefrontId || mk.storefrontCountryCode) || "us";

  function nowPlaying() {
    if (!mk) return {};
    const item = mk.nowPlayingItem;
    if (!item) return { state: mk.playbackState, catalogId: null };
    const attrs = item.attributes || {};
    return {
      state: mk.playbackState,
      // Library items carry a different id than the catalog; prefer the catalog
      // one so ratings and playlist adds line up with what the daemon queued.
      catalogId: (item.playParams && item.playParams.catalogId) || item.id || null,
      title: attrs.name || item.title || null,
      artist: attrs.artistName || item.artistName || null,
      artworkUrl: artworkUrl(attrs.artwork, 120),
      position: Math.round((mk.currentPlaybackTime || 0) * 1000),
      duration: attrs.durationInMillis ||
                Math.round((mk.currentPlaybackDuration || 0) * 1000) || null,
    };
  }

  // ---------------------------------------------------------------- commands

  async function search(term) {
    const q = encodeURIComponent(term || "");
    const data = await api("/v1/catalog/" + storefront() +
                           "/search?term=" + q + "&types=songs&limit=10");
    const songs = ((data.results || {}).songs || {}).data || [];
    return {
      songs: songs.map((s) => ({
        catalogId: s.id,
        title: (s.attributes || {}).name || null,
        artist: (s.attributes || {}).artistName || null,
        artworkUrl: artworkUrl((s.attributes || {}).artwork, 120),
        durationMs: (s.attributes || {}).durationInMillis || null,
      })),
    };
  }

  // Never `await mk.play()` — it can hang the evaluation for 45s+. Fire it,
  // give the player ~3s, then read the state back.
  function kick() {
    try { const p = mk.play(); if (p && p.catch) p.catch(() => {}); } catch (_) {}
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function play(catalogId) {
    if (!mk) throw new Error("MusicKit not ready");
    await mk.setQueue({ song: String(catalogId) });
    kick();
    await sleep(3000);
    // Stuck at "loading" with the playhead at zero is Chrome's autoplay policy
    // refusing us, not a slow network. It needs one real user click in the tab.
    if (mk.playbackState === S_LOADING && (mk.currentPlaybackTime || 0) === 0) {
      emit("autoplayBlocked");
    }
    return { ok: true, state: mk.playbackState };
  }

  async function listPlaylists() {
    // Library pagination via the `next` field stops early and silently. Step
    // offsets explicitly instead, and stop only on a short page.
    const out = [];
    const limit = 100;
    for (let offset = 0; offset < 2000; offset += limit) {
      const page = await api("/v1/me/library/playlists?limit=" + limit +
                             "&offset=" + offset + "&extend=trackCount");
      const items = page.data || [];
      for (const p of items) {
        const a = p.attributes || {};
        if (a.canEdit !== true) continue; // can't add tracks to the others
        out.push({
          id: p.id,
          name: a.name || "(untitled)",
          canEdit: true,
          trackCount: (p.meta && p.meta.trackCount) != null ? p.meta.trackCount
                      : (a.trackCount != null ? a.trackCount : null),
        });
      }
      if (items.length < limit) break;
    }
    return { playlists: out };
  }

  async function playlistTrackIds(playlistId) {
    // Used to avoid duplicating a track that is already in the target playlist.
    const ids = new Set();
    const limit = 100;
    for (let offset = 0; offset < 5000; offset += limit) {
      let page;
      try {
        page = await api("/v1/me/library/playlists/" +
                         encodeURIComponent(playlistId) + "/tracks?limit=" +
                         limit + "&offset=" + offset);
      } catch (e) {
        // A playlist with no tracks 404s rather than returning an empty list.
        if (String(e.message || "").includes("HTTP 404")) break;
        throw e;
      }
      const items = page.data || [];
      for (const t of items) {
        const pp = t.playParams || (t.attributes || {}).playParams || {};
        if (pp.catalogId) ids.add(String(pp.catalogId));
        if (t.id) ids.add(String(t.id));
      }
      if (items.length < limit) break;
    }
    return { trackIds: Array.from(ids) };
  }

  // Only used to make a scratch playlist for verification. The DJ never calls
  // this at play time: the starred target is chosen once during setup, from
  // playlists that already exist.
  async function createPlaylist(name) {
    const data = await api("/v1/me/library/playlists", {
      method: "POST",
      body: { attributes: { name: String(name || "music-dj scratch") } },
    });
    const made = (data.data || [])[0] || {};
    return { id: made.id || null, name: (made.attributes || {}).name || null };
  }

  async function addToPlaylist(catalogId, playlistId) {
    if (!playlistId) throw new Error("addToPlaylist needs a playlist id");
    await api("/v1/me/library/playlists/" + encodeURIComponent(playlistId) +
              "/tracks", {
      method: "POST",
      body: { data: [{ id: String(catalogId), type: "songs" }] },
    });
    return { ok: true };
  }

  async function handle(msg) {
    switch (msg.cmd) {
      case "search":        return await search(msg.term);
      case "play":          return await play(msg.catalogId);
      case "pause":         mk.pause(); return { ok: true };
      case "resume":        kick(); return { ok: true };
      case "skip":          await mk.skipToNextItem(); return { ok: true };
      case "previous":      await mk.skipToPreviousItem(); return { ok: true };
      case "listPlaylists": return await listPlaylists();
      case "playlistTracks":return await playlistTrackIds(msg.playlistId);
      case "createPlaylist":return await createPlaylist(msg.name);
      case "addToPlaylist": return await addToPlaylist(msg.catalogId, msg.playlistId);
      case "status":        return nowPlaying();
      case "ping":          return { ok: true };
      default: throw new Error("unknown command: " + msg.cmd);
    }
  }

  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const data = ev.data;
    if (!data || data.__musicdj !== IN || !data.payload) return;
    const msg = data.payload;
    Promise.resolve()
      .then(() => handle(msg))
      .then((result) => send(Object.assign({ id: msg.id }, result)))
      .catch((err) => send({ id: msg.id, error: String((err && err.message) || err) }));
  });

  // ------------------------------------------------------------------ events

  function pushPlayback() {
    const np = nowPlaying();
    emit("playback", np);
    lastPositionSent = Date.now();
  }

  function onStateChange(e) {
    const state = (e && e.state != null) ? e.state : (mk ? mk.playbackState : null);
    if (state === lastState) return;
    const prev = lastState;
    lastState = state;

    if (state === S_ENDED || state === S_COMPLETED) {
      emit("trackEnded", { catalogId: lastItemId });
      return;
    }
    // A transition into "playing" means autoplay is no longer blocked.
    if (state === S_PLAYING || state === S_PAUSED || prev !== null) pushPlayback();
  }

  function onItemChange() {
    const np = nowPlaying();
    lastItemId = np.catalogId;
    emit("playback", np);
  }

  function onTimeChange() {
    // Position ticks fire several times a second; one update per second is
    // plenty for a progress readout and keeps the socket quiet.
    if (Date.now() - lastPositionSent < 1000) return;
    pushPlayback();
  }

  function announceReady() {
    if (announcedReady || !mk) return;
    announcedReady = true;
    // previewOnly means the web player sees no active subscription: playback
    // would be capped at 30s clips. Worth surfacing rather than debugging blind.
    let previewOnly = true;
    try {
      previewOnly = !(mk.musicUserToken && mk.isAuthorized);
      if (mk.previewOnly != null) previewOnly = !!mk.previewOnly;
    } catch (_) {}
    emit("ready", { storefront: storefront(), previewOnly });
    pushPlayback();
  }

  function wire() {
    if (!mk) return false;
    const on = (name, fn) => { try { mk.addEventListener(name, fn); } catch (_) {} };
    on("playbackStateDidChange", onStateChange);
    on("nowPlayingItemDidChange", onItemChange);
    on("playbackTimeDidChange", onTimeChange);
    on("mediaItemStateDidChange", onItemChange);
    lastState = mk.playbackState;
    lastItemId = (nowPlaying() || {}).catalogId;
    announceReady();
    return true;
  }

  // ------------------------------------------------------------- tab title

  // The player rewrites document.title on every navigation, so re-apply it and
  // watch for replacement of the <title> node itself, not just its text.
  function ownTitle() {
    const want = "DJ";
    const apply = () => { if (document.title !== want) document.title = want; };
    apply();
    const observe = () => {
      const el = document.querySelector("title");
      if (!el) return;
      new MutationObserver(apply).observe(el, { childList: true, characterData: true, subtree: true });
    };
    observe();
    if (document.head) {
      new MutationObserver(() => { apply(); observe(); })
        .observe(document.head, { childList: true });
    }
    setInterval(apply, 2000);
  }

  // MusicKit is not there at document_start; poll until the page builds it.
  let tries = 0;
  const wait = setInterval(() => {
    if (!mk) mk = instance();
    if (mk && wire()) { clearInterval(wait); return; }
    if (++tries > 600) clearInterval(wait); // ~5 min, then give up quietly
  }, 500);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ownTitle, { once: true });
  } else {
    ownTitle();
  }

  // A reload wipes the MusicKit queue and every listener above. Telling the
  // daemon we came back is what lets it re-seed the current track.
  send({ evt: "injected" });
})();
