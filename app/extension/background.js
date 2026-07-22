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

// No DJ tab anywhere? Open one ourselves -- pinned and in the background, so
// starting the daemon is the only launch step there is. Resolves when the
// page has loaded (or after a generous timeout, for slow networks).
async function openTab() {
  const tab = await chrome.tabs.create({
    url: "https://music.apple.com/", pinned: true, active: false });
  tabId = tab.id;
  await new Promise((resolve) => {
    const done = (id, info) => {
      if (id === tab.id && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(done);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(done);
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(done);
      resolve();
    }, 20000);
  });
  log("opened a DJ tab:", tabId);
  return tabId;
}

// Content scripts declared in the manifest only reach pages that load after the
// extension is installed. A tab that was already open when you loaded it has no
// bridge in it, and every command fails with "Receiving end does not exist".
// Injecting on demand fixes that without asking the user to reload anything.
async function inject(id) {
  await chrome.scripting.executeScript({
    target: { tabId: id }, files: ["bridge-main.js"], world: "MAIN" });
  await chrome.scripting.executeScript({
    target: { tabId: id }, files: ["bridge-iso.js"], world: "ISOLATED" });
}

async function toPage(payload) {
  if (tabId == null) await findTab();
  if (tabId == null) {
    await openTab();
    await inject(tabId);
    // The fresh page announces itself and MusicKit takes a moment to exist.
    await new Promise((r) => setTimeout(r, 500));
  }
  try {
    await chrome.tabs.sendMessage(tabId, { kind: "toPage", payload });
    return;
  } catch (e) {
    log("no listener in tab", tabId, "- re-injecting");
  }

  // Either the tab id went stale (closed, navigated away) or the bridge was
  // never injected. Find the tab again -- opening one if it is gone --
  // inject, and retry once.
  tabId = null;
  if (!(await findTab())) await openTab();
  try {
    await inject(tabId);
  } catch (e) {
    throw new Error("could not inject into the tab: " + (e.message || e));
  }
  // The page-world script polls for MusicKit, so give it a moment to wire up.
  await new Promise((r) => setTimeout(r, 500));
  try {
    await chrome.tabs.sendMessage(tabId, { kind: "toPage", payload });
  } catch (e) {
    throw new Error("the DJ tab is not responding — try reloading it");
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

  // Captured so that a socket we have already replaced cannot tear down the
  // live connection's keepalive when its close event finally arrives.
  const sock = ws;

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

  ws.onclose = () => {
    if (ws !== sock) return;          // already superseded; leave the live one alone
    clearInterval(keepaliveTimer);
    keepaliveTimer = null;
    schedule();
  };
  ws.onerror = () => { try { sock.close(); } catch (_) {} };
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
  // Whichever tab we adopted first stays the one we drive. Reassigning on
  // every message meant a second signed-in tab could take over mid-command,
  // and both tabs' playback and trackEnded events landed on one socket with
  // nothing to tell them apart. onRemoved clears tabId, so a closed tab
  // still hands over cleanly.
  if (msg.kind === "hello") {
    if (sender.tab && tabId == null) tabId = sender.tab.id;
    connect();
    send({ evt: "tabReady" });
    return false;
  }
  if (msg.kind === "fromPage") {
    if (sender.tab) {
      if (tabId == null) tabId = sender.tab.id;
      if (sender.tab.id !== tabId) return false;
    }
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
