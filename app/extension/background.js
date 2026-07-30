// Service worker: holds the WebSocket to the daemon and routes commands to the
// Apple Music tab.
//
// MV3 workers get killed when idle. WebSocket traffic counts as activity, and
// the alarm below guarantees traffic even when the daemon is quiet. If the
// worker dies anyway, the alarm wakes it and the socket is rebuilt.

// Firefox puts the promise-returning API on `browser` and keeps `chrome` as a
// callback-style alias; Chrome has only `chrome`, and in MV3 it returns
// promises. Picking `browser` where it exists gives one promise-shaped API on
// both, which is what every await below assumes.
const api = typeof browser !== "undefined" ? browser : chrome;

const DAEMON_URL = "ws://127.0.0.1:8787/bridge";
const KEEPALIVE_MS = 15000;
const LAUNCHER = "com.music_dj.launcher";

let ws = null;
let backoff = 1000;
let tabId = null;
let keepaliveTimer = null;

const log = (...a) => console.log("[music-dj]", ...a);

// ------------------------------------------------------------------- the tab

// Adoption has to outlive the worker: MV3 suspends it constantly, and coming
// back with tabId null meant re-querying — which, with two Apple Music tabs
// open, could flip adoption to the idle one and drop the playing tab's events.
// Session storage lives exactly as long as the browser session, same as a tab.
function adopt(id) {
  tabId = id;
  try {
    api.storage.session.set({ tabId: id }).catch(() => {});
  } catch (_) {}
}

const restored = (async () => {
  try {
    const saved = (await api.storage.session.get("tabId")).tabId;
    if (saved == null || tabId != null) return;
    await api.tabs.get(saved);    // throws if it closed while we slept
    tabId = saved;
  } catch (_) {}
})();

// Never clobber an adopted tab that is still alive; only re-query once it is
// genuinely gone. Reconnects happen on every worker wake, so an unconditional
// query here is exactly the adoption flip described above.
async function findTab() {
  await restored;
  if (tabId != null) {
    try {
      await api.tabs.get(tabId);
      return tabId;
    } catch (_) {
      adopt(null);
    }
  }
  const tabs = await api.tabs.query({ url: "https://music.apple.com/*" });
  adopt(tabs.length ? tabs[0].id : null);
  return tabId;
}

// Bring the DJ tab to the front. Worth doing on an explicit click: the
// browser will not let audio start until someone has interacted with that
// tab at least once, and a pinned background tab is a favicon at the far
// left of the strip that nobody notices, let alone clicks.
async function showTab(id) {
  try {
    const tab = await api.tabs.update(id, { active: true });
    if (tab && tab.windowId != null) {
      await api.windows.update(tab.windowId, { focused: true });
    }
  } catch (e) {
    log("could not bring the DJ tab forward:", e.message || e);
  }
}

// No DJ tab anywhere? Open one ourselves -- pinned, so starting the daemon is
// the only launch step there is. Resolves when the page has loaded (or after
// a generous timeout, for slow networks). `show` is set when a click asked
// for the tab, and left off when the daemon merely needs somewhere to play.
async function openTab(show) {
  const tab = await api.tabs.create({
    url: "https://music.apple.com/", pinned: true, active: !!show });
  adopt(tab.id);
  await new Promise((resolve) => {
    const done = (id, info) => {
      if (id === tab.id && info.status === "complete") {
        api.tabs.onUpdated.removeListener(done);
        resolve();
      }
    };
    api.tabs.onUpdated.addListener(done);
    setTimeout(() => {
      api.tabs.onUpdated.removeListener(done);
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
  await api.scripting.executeScript({
    target: { tabId: id }, files: ["bridge-main.js"], world: "MAIN" });
  await api.scripting.executeScript({
    target: { tabId: id }, files: ["bridge-iso.js"], world: "ISOLATED" });
}

// One find-or-open at a time. The daemon fires several commands concurrently
// on first start; each seeing tabId null and opening its own pinned tab left
// up to four music.apple.com tabs. All callers share the acquisition in
// flight instead, so only one openTab can ever run.
let acquiring = null;

function acquireTab(show) {
  if (acquiring) return acquiring;
  acquiring = (async () => {
    let id = await findTab();
    if (id == null) {
      id = await openTab(show);
      await inject(id);
      // The fresh page announces itself and MusicKit takes a moment to exist.
      await new Promise((r) => setTimeout(r, 500));
    } else if (show) {
      await showTab(id);       // already open, somewhere behind everything
    }
    return id;
  })().finally(() => { acquiring = null; });
  return acquiring;
}

async function toPage(payload) {
  await acquireTab();
  try {
    await api.tabs.sendMessage(tabId, { kind: "toPage", payload });
    return;
  } catch (e) {
    log("no listener in tab", tabId, "- re-injecting");
  }

  // Either the tab id went stale (closed, navigated away) or the bridge was
  // never injected. Find the tab again -- opening one if it is gone --
  // inject, and retry once.
  adopt(null);
  await acquireTab();
  try {
    await inject(tabId);
  } catch (e) {
    throw new Error("could not inject into the tab: " + (e.message || e));
  }
  // The page-world script polls for MusicKit, so give it a moment to wire up.
  await new Promise((r) => setTimeout(r, 500));
  try {
    await api.tabs.sendMessage(tabId, { kind: "toPage", payload });
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
    api.action.setBadgeBackgroundColor({ color: "#2e7d32" });
    api.action.setBadgeText({ text: "ON" });
    backoff = 1000;
    log("connected to daemon");
    findTab();   // re-validate (or adopt) the DJ tab ahead of the first command
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
    api.action.setBadgeText({ text: "" });
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

api.runtime.onMessage.addListener((msg, sender) => {
  if (!msg) return false;
  // Whichever tab we adopted first stays the one we drive. Reassigning on
  // every message meant a second signed-in tab could take over mid-command,
  // and both tabs' playback and trackEnded events landed on one socket with
  // nothing to tell them apart. onRemoved clears tabId, so a closed tab
  // still hands over cleanly.
  if (msg.kind === "hello") {
    if (sender.tab && tabId == null) adopt(sender.tab.id);
    connect();
    send({ evt: "tabReady" });
    return false;
  }
  if (msg.kind === "fromPage") {
    if (sender.tab) {
      if (tabId == null) adopt(sender.tab.id);
      if (sender.tab.id !== tabId) return false;
    }
    send(msg.payload);
  }
  return false;
});

api.tabs.onRemoved.addListener((id) => {
  if (id === tabId) {
    adopt(null);
    send({ evt: "tabGone" });
  }
});

// ------------------------------------------------------------------ toggle

// The toolbar icon is the DJ's switch: connected means clicking stops it,
// disconnected means clicking asks the launcher (a native messaging host,
// registered by host/register.py) to start the daemon and overlay. The
// DJ tab is opened here too, up front, so activation is one click total.
async function startTheDaemon() {
  // Promise form rather than a callback with runtime.lastError: Firefox's
  // `browser` API has no lastError at all, and a failure there arrives as a
  // rejection. Chrome MV3 answers the same shape.
  try {
    const reply = await api.runtime.sendNativeMessage(LAUNCHER, { cmd: "start" });
    log("launcher:", JSON.stringify(reply));
    // The reconnect loop may be sitting out a 30s backoff; the daemon will
    // be up in a moment, so try again now.
    backoff = 1000;
    connect();
  } catch (e) {
    log("launcher failed:", (e && e.message) || e,
        "-- run app/host/register.py, then reload the extension");
    api.action.setBadgeBackgroundColor({ color: "#c62828" });
    api.action.setBadgeText({ text: "ERR" });
    setTimeout(() => api.action.setBadgeText({ text: "" }), 4000);
  }
}

api.action.onClicked.addListener(async () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    send({ evt: "shutdown" });
    return;
  }
  startTheDaemon();            // not awaited: the tab can open alongside it
  try {
    await acquireTab(true);    // open it if missing, and put it in front
  } catch (e) {
    log("could not open the DJ tab:", e);
  }
});

api.alarms.create("keepalive", { periodInMinutes: 0.5 });
api.alarms.onAlarm.addListener(() => connect());
api.runtime.onStartup.addListener(() => connect());
api.runtime.onInstalled.addListener(() => connect());

connect();
