// Service worker: holds the WebSocket to the daemon and routes commands to the
// Apple Music tab.
//
// MV3 workers get killed when idle. WebSocket traffic counts as activity, and
// the alarm below guarantees traffic even when the daemon is quiet. If the
// worker dies anyway, the alarm wakes it and the socket is rebuilt.

const DAEMON_URL = "ws://127.0.0.1:8787/bridge";
const KEEPALIVE_MS = 15000;

let ws = null;
let backoff = 1000;
let tabId = null;
let keepaliveTimer = null;

const log = (...a) => console.log("[music-dj]", ...a);

// ------------------------------------------------------------------- the tab

async function findTab() {
  const tabs = await chrome.tabs.query({ url: "https://music.apple.com/*" });
  tabId = tabs.length ? tabs[0].id : null;
  return tabId;
}

async function toPage(payload) {
  if (tabId == null) await findTab();
  if (tabId == null) throw new Error("no music.apple.com tab open");
  try {
    await chrome.tabs.sendMessage(tabId, { kind: "toPage", payload });
  } catch (e) {
    // Stale tab id (closed, or navigated away) — look again once before failing
    // so a reload doesn't need a daemon restart to recover.
    tabId = null;
    if (await findTab()) {
      await chrome.tabs.sendMessage(tabId, { kind: "toPage", payload });
    } else {
      throw new Error("no music.apple.com tab open");
    }
  }
}

// ---------------------------------------------------------------- the socket

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try {
    ws = new WebSocket(DAEMON_URL);
  } catch (e) {
    schedule();
    return;
  }

  ws.onopen = () => {
    backoff = 1000;
    log("connected to daemon");
    findTab().then((id) => {
      send({ evt: "bridgeUp", hasTab: id != null });
    });
    clearInterval(keepaliveTimer);
    keepaliveTimer = setInterval(() => send({ evt: "keepalive" }), KEEPALIVE_MS);
  };

  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (!msg || !msg.cmd) return;
    try {
      await toPage(msg);
    } catch (e) {
      // Reply rather than going silent: the daemon correlates on id and would
      // otherwise wait out its timeout for a tab that simply isn't there.
      send({ id: msg.id, error: String(e.message || e) });
    }
  };

  ws.onclose = () => { clearInterval(keepaliveTimer); schedule(); };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

function schedule() {
  const delay = backoff;
  backoff = Math.min(backoff * 2, 30000);
  setTimeout(connect, delay);
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  return false;
}

// ------------------------------------------------------------------- routing

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg) return false;
  if (msg.kind === "hello") {
    if (sender.tab) tabId = sender.tab.id;
    connect();
    send({ evt: "tabReady" });
    return false;
  }
  if (msg.kind === "fromPage") {
    if (sender.tab) tabId = sender.tab.id;
    send(msg.payload);
  }
  return false;
});

chrome.tabs.onRemoved.addListener((id) => {
  if (id === tabId) {
    tabId = null;
    send({ evt: "tabGone" });
  }
});

chrome.alarms.create("keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());

connect();
