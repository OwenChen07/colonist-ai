/*
 * Colonist AI Capture — records every WebSocket message on colonist.io.
 *
 * Runs in the page's own world at document_start, before colonist's code, so
 * it is attached to the game socket from the moment it opens: the first
 * messages carry the full game state (board, ports, setup), and everything
 * after is updates. Messages are stored raw (base64 for binary frames) in
 * IndexedDB as they arrive, so a game survives reloads and closed tabs.
 * Decoding happens offline (catanatron.colonist.capture).
 *
 * Read-only: it wraps the WebSocket constructor and send() only to copy
 * frames, and never sends or changes anything.
 *
 * "Save capture" downloads the current page's session, or the previous one
 * if this page has not seen a game yet. The last KEEP_SESSIONS are kept.
 */
(function () {
  'use strict';
  if (window.__colonistAiCapture) return;

  const DB_NAME = 'colonist-ai-capture';
  const KEEP_SESSIONS = 10;
  const FLUSH_MS = 1000;
  const FORMAT = 'colonist-ai.capture';

  const session = { id: Date.now(), url: location.href, sockets: 0 };
  let seq = 0;
  let pending = [];
  let count = 0;
  let onCount = null;

  // ----- storage -----
  let dbPromise = null;
  function db() {
    if (!dbPromise) {
      dbPromise = new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => {
          const d = req.result;
          d.createObjectStore('sessions', { keyPath: 'id' });
          const m = d.createObjectStore('messages', { autoIncrement: true });
          m.createIndex('session', 'session');
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
    return dbPromise;
  }

  function tx(stores, mode, fn) {
    return db().then((d) => new Promise((resolve, reject) => {
      const t = d.transaction(stores, mode);
      const out = fn(t);
      t.oncomplete = () => resolve(out);
      t.onerror = () => reject(t.error);
    }));
  }

  function flush() {
    if (!pending.length) return;
    const batch = pending;
    pending = [];
    tx(['sessions', 'messages'], 'readwrite', (t) => {
      t.objectStore('sessions').put({ id: session.id, url: session.url, count });
      const m = t.objectStore('messages');
      for (const msg of batch) m.add(msg);
    }).catch((e) => console.warn('[colonist-ai capture] could not store messages', e));
  }
  setInterval(flush, FLUSH_MS);
  addEventListener('pagehide', flush);

  function pruneOldSessions() {
    return tx(['sessions'], 'readonly', (t) => {
      const r = t.objectStore('sessions').getAllKeys();
      return r;
    }).then((r) => {
      const ids = (r.result || []).sort((a, b) => a - b);
      const drop = ids.slice(0, Math.max(0, ids.length - KEEP_SESSIONS));
      if (!drop.length) return;
      return tx(['sessions', 'messages'], 'readwrite', (t) => {
        const index = t.objectStore('messages').index('session');
        for (const id of drop) {
          t.objectStore('sessions').delete(id);
          index.openKeyCursor(IDBKeyRange.only(id)).onsuccess = (e) => {
            const c = e.target.result;
            if (c) { t.objectStore('messages').delete(c.primaryKey); c.continue(); }
          };
        }
      });
    }).catch((e) => console.warn('[colonist-ai capture] could not prune', e));
  }
  pruneOldSessions();

  // ----- recording -----
  function toBase64(buf) {
    const bytes = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  function record(dir, socket, data) {
    const entry = { session: session.id, seq: seq++, t: Date.now(), dir, socket };
    const store = (fields) => {
      pending.push(Object.assign(entry, fields));
      count++;
      if (onCount) onCount(count);
    };
    if (typeof data === 'string') store({ text: data });
    else if (data instanceof ArrayBuffer) store({ b64: toBase64(data) });
    else if (ArrayBuffer.isView(data)) store({ b64: toBase64(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)) });
    else if (data instanceof Blob) data.arrayBuffer().then((b) => store({ b64: toBase64(b) }));
    else store({ text: String(data) });
  }

  const NativeWebSocket = window.WebSocket;
  function CapturingWebSocket(url, protocols) {
    const ws = protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols);
    const id = ++session.sockets;
    record('open', id, String(url));
    ws.addEventListener('message', (e) => record('in', id, e.data));
    ws.addEventListener('close', (e) => record('close', id, String(e.code)));
    const nativeSend = ws.send;
    ws.send = function (data) {
      record('out', id, data);
      return nativeSend.call(this, data);
    };
    return ws;
  }
  CapturingWebSocket.prototype = NativeWebSocket.prototype;
  for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) CapturingWebSocket[k] = NativeWebSocket[k];
  window.WebSocket = CapturingWebSocket;

  // ----- export -----
  function sessionsWithMessages() {
    return tx(['sessions'], 'readonly', (t) => t.objectStore('sessions').getAll())
      .then((r) => (r.result || []).filter((s) => s.count > 0).sort((a, b) => b.id - a.id));
  }

  function messagesOf(id) {
    return tx(['messages'], 'readonly', (t) => t.objectStore('messages').index('session').getAll(IDBKeyRange.only(id)))
      .then((r) => (r.result || []).sort((a, b) => a.seq - b.seq).map((m) => {
        const out = { seq: m.seq, t: m.t, dir: m.dir, socket: m.socket };
        if (m.b64 !== undefined) out.b64 = m.b64; else out.text = m.text;
        return out;
      }));
  }

  function captureDoc(meta, messages) {
    return { format: FORMAT, version: 1, started: meta.id, url: meta.url, messages };
  }

  function download(meta, messages) {
    const doc = captureDoc(meta, messages);
    const blob = new Blob([JSON.stringify(doc)], { type: 'application/json' });
    const a = document.createElement('a');
    const d = new Date(meta.id);
    const pad = (n) => String(n).padStart(2, '0');
    a.download = `colonist-capture-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.json`;
    a.href = URL.createObjectURL(blob);
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  // A session counts as a game once it has more than lobby chatter.
  const GAME_MIN_MESSAGES = 50;

  /** The session to save: this page's if it saw a game, else the latest one that did. */
  function pickSession() {
    flush();
    return new Promise((r) => setTimeout(r, 200))
      .then(sessionsWithMessages)
      .then((all) => {
        const current = all.find((s) => s.id === session.id);
        return current && current.count >= GAME_MIN_MESSAGES ? current
          : all.find((s) => s.id !== session.id && s.count >= GAME_MIN_MESSAGES) || current;
      });
  }

  /** The capture document that "Save capture" would download, or null. */
  function exportCapture() {
    return pickSession().then((pick) => pick ? messagesOf(pick.id).then((m) => captureDoc(pick, m)) : null);
  }

  function save() {
    return exportCapture().then((doc) => {
      if (!doc) { alertBox('Nothing captured yet. Reload the game page with the extension on.'); return; }
      download({ id: doc.started, url: doc.url }, doc.messages);
    });
  }

  function alertBox(text) {
    if (button) { button.title = text; button.textContent = text; setTimeout(render, 4000); }
  }

  // ----- button -----
  let button = null;
  function render() {
    if (button) button.textContent = `● Save capture (${count})`;
  }
  function mount() {
    if (window.top !== window || button || !document.body) return;
    button = document.createElement('button');
    button.type = 'button';
    button.title = 'Colonist AI Capture: download this game\'s messages';
    Object.assign(button.style, {
      position: 'fixed', left: '8px', bottom: '8px', zIndex: 2147483647,
      font: '12px system-ui, sans-serif', padding: '4px 8px', borderRadius: '6px',
      border: '1px solid #b71c1c', background: '#fff', color: '#b71c1c', cursor: 'pointer', opacity: '0.85',
    });
    button.addEventListener('click', (e) => { e.stopPropagation(); save(); });
    document.body.appendChild(button);
    render();
  }
  let renderQueued = false;
  onCount = () => {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => { renderQueued = false; render(); });
  };
  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);

  window.__colonistAiCapture = { session, save, exportCapture, count: () => count };
})();
