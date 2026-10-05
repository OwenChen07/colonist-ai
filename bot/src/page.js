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
 * It never sends anything to colonist.
 */
(function () {
  'use strict';
  if (window.__colonistAiBot) return;
  const TAG = 'colonist-ai-bot';
  const backlog = [];

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
  function forward(dir, data) {
    const t = Date.now();
    let bytes;
    if (data instanceof ArrayBuffer) bytes = data;
    else if (ArrayBuffer.isView(data)) bytes = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
    else if (typeof Blob !== 'undefined' && data instanceof Blob) bytes = data.arrayBuffer();
    else return;  // text frames are lobby chatter: the game itself is binary MessagePack
    queue = queue.then(() => bytes).then((b) => {
      const msg = { kind: 'frame', dir, b64: toBase64(b), t };
      backlog.push(msg);
      window.postMessage({ [TAG]: 'to-relay', msg }, '*');
    }).catch((e) => console.warn('[colonist-ai bot] could not forward a frame', e));
  }

  const NativeWebSocket = window.WebSocket;
  function BotWebSocket(url, protocols) {
    const ws = protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols);
    ws.addEventListener('message', (e) => forward('in', e.data));
    const nativeSend = ws.send;
    ws.send = function (data) {
      forward('out', data);
      return nativeSend.call(this, data);
    };
    return ws;
  }
  BotWebSocket.prototype = NativeWebSocket.prototype;
  for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) BotWebSocket[k] = NativeWebSocket[k];
  window.WebSocket = BotWebSocket;

  // The relay asks for the backlog when the bridge (re)connects.
  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data || e.data[TAG] !== 'replay-backlog') return;
    window.postMessage({ [TAG]: 'to-relay', msg: { kind: 'reset' } }, '*');
    for (const msg of backlog) window.postMessage({ [TAG]: 'to-relay', msg }, '*');
  });

  window.__colonistAiBot = { backlog };
})();
