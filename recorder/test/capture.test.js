// Runs src/capture.js against a fake WebSocket and an in-memory IndexedDB,
// then checks the exported capture.
//
//   npm test                      # in recorder/
//   npm test -- out.json          # also write the capture, for the Python loader test
const assert = require('assert');
const fs = require('fs');
const path = require('path');
require('fake-indexeddb/auto');

// A minimal page.
class FakeWebSocket {
  constructor(url) { this.url = url; this.listeners = {}; this.sent = []; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  send(data) { this.sent.push(data); }
  emit(type, event) { for (const fn of this.listeners[type] || []) fn(event); }
}
FakeWebSocket.OPEN = 1;
globalThis.window = globalThis;
globalThis.WebSocket = FakeWebSocket;
globalThis.location = { href: 'https://colonist.io/#test' };
globalThis.document = { body: null, addEventListener() {} };
globalThis.addEventListener = () => {};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);

eval(fs.readFileSync(path.join(__dirname, '..', 'src', 'capture.js'), 'utf8'));

// {"id": "130", "data": {"type": 91}} as MessagePack
const frame = Uint8Array.from([0x82, 0xa2, 0x69, 0x64, 0xa3, 0x31, 0x33, 0x30, 0xa4, 0x64, 0x61, 0x74, 0x61, 0x81, 0xa4, 0x74, 0x79, 0x70, 0x65, 0x5b]);

(async () => {
  const ws = new WebSocket('wss://socket.svr.colonist.io/?version=2');
  assert(ws instanceof FakeWebSocket, 'the page still gets a real socket');
  let pageSaw = 0;
  ws.addEventListener('message', () => pageSaw++);  // colonist's own handler
  for (let i = 0; i < 60; i++) ws.emit('message', { data: frame.buffer.slice(0) });
  ws.emit('message', { data: new Blob([frame]) });
  ws.emit('message', { data: 'hello' });
  ws.send(frame);
  assert.strictEqual(pageSaw, 62, 'page handlers still receive every message');
  assert.strictEqual(ws.sent.length, 1, 'sends still go out');

  await new Promise((r) => setTimeout(r, 50));  // let the Blob read finish
  const doc = await window.__colonistAiCapture.exportCapture();
  assert(doc, 'a capture is exported');
  assert.strictEqual(doc.format, 'colonist-ai.capture');
  const dirs = doc.messages.map((m) => m.dir);
  assert.strictEqual(dirs[0], 'open');
  assert.strictEqual(doc.messages[0].text, 'wss://socket.svr.colonist.io/?version=2');
  assert.strictEqual(dirs.filter((d) => d === 'in').length, 62);
  assert.strictEqual(dirs.filter((d) => d === 'out').length, 1);
  const b64 = Buffer.from(frame).toString('base64');
  assert.strictEqual(doc.messages[1].b64, b64);
  assert.strictEqual(doc.messages.find((m) => m.text === 'hello').dir, 'in');
  const seqs = doc.messages.map((m) => m.seq);
  assert.deepStrictEqual(seqs, [...seqs].sort((a, b) => a - b), 'messages in order');

  if (process.argv[2]) fs.writeFileSync(process.argv[2], JSON.stringify(doc));
  console.log(`ok: ${doc.messages.length} messages captured and exported`);
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
