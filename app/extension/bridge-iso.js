// Isolated world. A pure relay: page world has MusicKit but no chrome.*, the
// service worker has chrome.* but no MusicKit. This carries messages between.

(() => {
  "use strict";

  // Guard against being injected twice (manifest + on demand); a second relay
  // would forward every message to the daemon in duplicate.
  if (window.__musicDjBridgeIso) return;
  window.__musicDjBridgeIso = true;

  // Firefox's promise-returning API lives on `browser`; Chrome has only
  // `chrome`, which returns promises in MV3. Either way the sends below get
  // something with a .catch on it.
  const api = typeof browser !== "undefined" ? browser : chrome;

  const FROM_PAGE = "music-dj:from-page";
  const TO_PAGE = "music-dj:to-page";

  // Page -> service worker.
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const data = ev.data;
    if (!data || data.__musicdj !== FROM_PAGE || !data.payload) return;
    try {
      // sendMessage returns a promise in MV3; "receiving end does not exist"
      // arrives as a rejection, not a throw, so it needs its own catch or it
      // spams the console.
      api.runtime.sendMessage({ kind: "fromPage", payload: data.payload })
        .catch(() => {});
    } catch (_) {
      // Worker asleep or extension reloading — the daemon retries, so dropping
      // one message here is survivable.
    }
  });

  // Service worker -> page.
  api.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.kind === "toPage" && msg.payload) {
      window.postMessage({ __musicdj: TO_PAGE, payload: msg.payload }, "*");
      sendResponse({ ok: true });
    }
    return false;
  });

  // Announce the tab on (re)injection so the worker knows where to send
  // commands after a reload.
  try { api.runtime.sendMessage({ kind: "hello" }).catch(() => {}); } catch (_) {}
})();
