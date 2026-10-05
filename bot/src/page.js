/*
 * Colonist AI Bot, page side: copies colonist's game messages to the relay.
 *
 * Runs in the page's own world at document_start, before colonist's code, so
 * it sees the game socket from the moment it opens (the first message carries
 * the whole game; the bridge needs it). It wraps the WebSocket constructor the
 * same way the recorder does and copies each frame, as base64, to relay.js via
 * window.postMessage. It keeps this page's frames, so a bridge that connects
 * (or reconnects) mid-game can be sent everything from the start.
 *
 * When the bridge plays (--play), it hands back the bot's moves as the
 * messages colonist's own client would send, and this sends them on the game
 * socket (colonist allows bots; see PLAN.md, Phase 4).
 */
(function () {
  'use strict';
  if (window.__colonistAiBot) return;
  const TAG = 'colonist-ai-bot';
  const backlog = [];
  const sockets = {};  // id -> [socket, its original send]
  let nextId = 0;

  function toBase64(buf) {
    const bytes = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  // Frames go out strictly in arrival order (the bridge applies colonist's
  // updates in sequence), even when one arrives as a Blob, which can only be
  // read asynchronously.
  let queue = Promise.resolve();
  function forward(dir, data, socket) {
    const t = Date.now();
    let bytes;
    if (data instanceof ArrayBuffer) bytes = data;
    else if (ArrayBuffer.isView(data)) bytes = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
    else if (typeof Blob !== 'undefined' && data instanceof Blob) bytes = data.arrayBuffer();
    else return;  // text frames are lobby chatter: the game itself is binary MessagePack
    queue = queue.then(() => bytes).then((b) => {
      const msg = { kind: 'frame', dir, b64: toBase64(b), t, socket };
      backlog.push(msg);
      window.postMessage({ [TAG]: 'to-relay', msg }, '*');
    }).catch((e) => console.warn('[colonist-ai bot] could not forward a frame', e));
  }

  const NativeWebSocket = window.WebSocket;
  function BotWebSocket(url, protocols) {
    const ws = protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols);
    const id = ++nextId;
    ws.addEventListener('message', (e) => forward('in', e.data, id));
    const nativeSend = ws.send;
    sockets[id] = [ws, nativeSend];
    ws.send = function (data) {
      forward('out', data, id);
      return nativeSend.call(this, data);
    };
    return ws;
  }
  BotWebSocket.prototype = NativeWebSocket.prototype;
  for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) BotWebSocket[k] = NativeWebSocket[k];
  window.WebSocket = BotWebSocket;

  // Sending the bot's moves: the bridge builds the message (the same bytes
  // colonist's client would send) and names the game's socket. It goes out
  // through that socket's original send, so it is copied back to the bridge
  // like any message the page sends.
  function sendMove(id, b64) {
    const entry = sockets[id];
    if (!entry || entry[0].readyState !== NativeWebSocket.OPEN) {
      console.warn('[colonist-ai bot] game socket not open; move not sent');
      return false;
    }
    const raw = atob(b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    forward('out', bytes.buffer, id);
    entry[1].call(entry[0], bytes);
    return true;
  }

  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data) return;
    if (e.data[TAG] === 'replay-backlog') {
      // The relay asks for the backlog when the bridge (re)connects.
      // Replayed frames are marked: the bridge must not answer old positions.
      window.postMessage({ [TAG]: 'to-relay', msg: { kind: 'reset' } }, '*');
      for (const msg of backlog) {
        window.postMessage({ [TAG]: 'to-relay', msg: Object.assign({}, msg, { replay: true }) }, '*');
      }
      window.postMessage({ [TAG]: 'to-relay', msg: { kind: 'replay-done' } }, '*');
    } else if (e.data[TAG] === 'send') {
      sendMove(e.data.socket, e.data.b64);
    }
  });

  window.__colonistAiBot = { backlog, sendMove };
})();
