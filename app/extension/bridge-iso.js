// Isolated world. A pure relay: page world has MusicKit but no chrome.*, the
// service worker has chrome.* but no MusicKit. This carries messages between.

(() => {
  "use strict";

  const FROM_PAGE = "music-dj:from-page";
  const TO_PAGE = "music-dj:to-page";

  // Page -> service worker.
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const data = ev.data;
    if (!data || data.__musicdj !== FROM_PAGE || !data.payload) return;
    try {
      chrome.runtime.sendMessage({ kind: "fromPage", payload: data.payload });
    } catch (_) {
      // Worker asleep or extension reloading — the daemon retries, so dropping
      // one message here is survivable.
    }
  });

  // Service worker -> page.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.kind === "toPage" && msg.payload) {
      window.postMessage({ __musicdj: TO_PAGE, payload: msg.payload }, "*");
      sendResponse({ ok: true });
    }
    return false;
  });

  // Announce the tab on (re)injection so the worker knows where to send
  // commands after a reload.
  try { chrome.runtime.sendMessage({ kind: "hello" }); } catch (_) {}
})();
