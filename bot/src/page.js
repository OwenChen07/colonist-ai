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
  const sentCommands = new Set();  // retries with one ID never send twice
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

  // ----- sequence numbers -----
  // Game messages (header 3, 1) carry a "sequence" that must count up across
  // everything sent: colonist's own client and the bot both send them, and
  // neither knows about the other's. So every outgoing game message gets the
  // next number here, whoever wrote it. The number is the value after the
  // MessagePack key "sequence" (a fixstr: a8 + the 8 letters); only that value
  // is replaced, so the rest of the message is byte-for-byte unchanged.
  const SEQ_KEY = [0xa8, 0x73, 0x65, 0x71, 0x75, 0x65, 0x6e, 0x63, 0x65];
  let lastSequence = null;

  function findSequence(b) {
    outer: for (let i = 3; i + SEQ_KEY.length < b.length; i++) {
      for (let j = 0; j < SEQ_KEY.length; j++) if (b[i + j] !== SEQ_KEY[j]) continue outer;
      const at = i + SEQ_KEY.length;
      const tag = b[at];
      if (tag <= 0x7f) return [at, 1, tag];
      if (tag === 0xcc) return [at, 2, b[at + 1]];
      if (tag === 0xcd) return [at, 3, (b[at + 1] << 8) | b[at + 2]];
      if (tag === 0xce) return [at, 5, ((b[at + 1] << 24) >>> 0) + (b[at + 2] << 16) + (b[at + 3] << 8) + b[at + 4]];
      return null;
    }
    return null;
  }

  function encodeUint(n) {
    if (n <= 0x7f) return [n];
    if (n <= 0xff) return [0xcc, n];
    if (n <= 0xffff) return [0xcd, n >> 8, n & 0xff];
    return [0xce, (n >>> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  }

  /** The game message with the next sequence number (or unchanged if it isn't
   * one). A client message numbered well below the count means the client
   * started counting again (a new game): follow it. */
  function renumber(bytes, fromClient) {
    const b = new Uint8Array(bytes);
    if (b.length < 3 || b[0] !== 3 || b[1] !== 1) return bytes;
    const found = findSequence(b);
    if (!found) return bytes;
    const [at, width, value] = found;
    if (fromClient && lastSequence !== null && value < lastSequence - 1) lastSequence = value - 1;
    const next = lastSequence === null ? value : lastSequence + 1;
    lastSequence = next;
    if (next === value) return bytes;
    const enc = encodeUint(next);
    const out = new Uint8Array(b.length - width + enc.length);
    out.set(b.subarray(0, at), 0);
    out.set(enc, at);
    out.set(b.subarray(at + width), at + enc.length);
    return out.buffer;
  }

  function toArrayBuffer(data) {
    if (data instanceof ArrayBuffer) return data;
    if (ArrayBuffer.isView(data)) return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
    return null;
  }

  const NativeWebSocket = window.WebSocket;
  function BotWebSocket(url, protocols) {
    const ws = protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols);
    const id = ++nextId;
    ws.addEventListener('message', (e) => forward('in', e.data, id));
    const nativeSend = ws.send;
    sockets[id] = [ws, nativeSend];
    ws.send = function (data) {
      // Only a message whose number changes is replaced; anything else goes
      // out exactly as colonist's client made it.
      const buf = toArrayBuffer(data);
      if (buf) {
        const out = renumber(buf, true);
        if (out !== buf) data = out;
      }
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
  function sendMove(id, b64, sendId) {
    if (sendId && sentCommands.has(sendId)) {
      window.postMessage({ [TAG]: 'to-relay', msg: { kind: 'send-confirmed', sendId } }, '*');
      return true;
    }
    let entry = sockets[id];
    if (entry && entry[0].readyState !== NativeWebSocket.OPEN) {
      // The named socket closed: colonist reconnected the game on a newer one
      // (seen live, often, on new accounts). Use the newest open socket.
      const newer = Object.keys(sockets).map(Number).filter((k) => k > id).sort((a, b) => b - a)
        .find((k) => sockets[k][0].readyState === NativeWebSocket.OPEN);
      if (newer !== undefined) {
        id = newer;
        entry = sockets[newer];
      }
    }
    if (!entry || entry[0].readyState !== NativeWebSocket.OPEN) {
      console.warn('[colonist-ai bot] game socket not open; move not sent');
      window.postMessage({ [TAG]: 'to-relay', msg: { kind: 'send-failed', socket: id, sendId, reason: 'game socket not open' } }, '*');
      return false;
    }
    const raw = atob(b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    // Before the client has sent any game message of its own, start where
    // colonist's client did in the recorded game (2).
    if (lastSequence === null) lastSequence = 1;
    const out = renumber(bytes.buffer, false);
    try {
      entry[1].call(entry[0], new Uint8Array(out));
    } catch (e) {
      window.postMessage({ [TAG]: 'to-relay', msg: { kind: 'send-failed', socket: id, sendId, reason: 'game socket send failed' } }, '*');
      return false;
    }
    forward('out', out, id);
    if (sendId) {
      sentCommands.add(sendId);
      window.postMessage({ [TAG]: 'to-relay', msg: { kind: 'send-confirmed', sendId } }, '*');
    }
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
      sendMove(e.data.socket, e.data.b64, e.data.sendId);
    } else if (e.data[TAG] === 'new-game') {
      lastSequence = null;  // the client counts game messages afresh per game
    }
  });

  window.__colonistAiBot = { backlog, sendMove, renumber };
})();
