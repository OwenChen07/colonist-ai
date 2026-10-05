// Runs src/page.js against a fake WebSocket and window, and checks what it
// hands to the relay.
//
//   npm test        # in bot/
const assert = require('assert');
const fs = require('fs');
const path = require('path');

class FakeWebSocket {
  constructor(url) { this.url = url; this.listeners = {}; this.sent = []; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  send(data) { this.sent.push(data); }
  emit(type, event) { for (const fn of this.listeners[type] || []) fn(event); }
}
FakeWebSocket.OPEN = 1;

const posted = [];
const windowListeners = [];
globalThis.window = globalThis;
globalThis.WebSocket = FakeWebSocket;
globalThis.postMessage = (data) => posted.push(data);
globalThis.addEventListener = (type, fn) => { if (type === 'message') windowListeners.push(fn); };
const fromPage = (data) => windowListeners.forEach((fn) => fn({ source: window, data }));

eval(fs.readFileSync(path.join(__dirname, '..', 'src', 'page.js'), 'utf8'));

const TAG = 'colonist-ai-bot';
const frame = Uint8Array.from([0x82, 0xa2, 0x69, 0x64, 0xa3, 0x31, 0x33, 0x30]);

(async () => {
  const ws = new WebSocket('wss://socket.svr.colonist.io/?version=2');
  assert(ws instanceof FakeWebSocket, 'the page still gets a real socket');
  let pageSaw = 0;
  ws.addEventListener('message', () => pageSaw++);
  ws.emit('message', { data: frame.buffer.slice(0) });
  ws.emit('message', { data: 'lobby text' });
  ws.emit('message', { data: new Blob([frame]) });
  ws.send(frame);
  await new Promise((r) => setTimeout(r, 20));  // the Blob is read asynchronously

  assert.strictEqual(pageSaw, 3, 'colonist still receives every message');
  assert.deepStrictEqual(ws.sent, [frame], 'outgoing messages still go out unchanged');
  const frames = posted.filter((p) => p[TAG] === 'to-relay').map((p) => p.msg);
  assert.deepStrictEqual(frames.map((f) => f.dir), ['in', 'in', 'out'], 'binary frames only');
  const b64 = Buffer.from(frame).toString('base64');
  assert(frames.every((f) => f.kind === 'frame' && f.b64 === b64));

  // When the bridge (re)connects, the page resends everything after a reset.
  posted.length = 0;
  fromPage({ [TAG]: 'replay-backlog' });
  const replayed = posted.filter((p) => p[TAG] === 'to-relay').map((p) => p.msg.kind);
  assert.deepStrictEqual(replayed, ['reset', 'frame', 'frame', 'frame']);
  console.log('page.js: all checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
