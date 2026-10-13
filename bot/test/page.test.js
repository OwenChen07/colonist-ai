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
  assert.deepStrictEqual(replayed, ['reset', 'frame', 'frame', 'frame', 'replay-done']);
  assert(posted.filter((p) => p[TAG] === 'to-relay' && p.msg.kind === 'frame').every((p) => p.msg.replay));

  // Sending a move: the bytes go out on the named socket, and are copied
  // back like any outgoing message.
  posted.length = 0;
  ws.readyState = FakeWebSocket.OPEN;
  fromPage({ [TAG]: 'send', socket: 1, b64: Buffer.from([1, 2, 3]).toString('base64'), sendId: 'move-a' });
  await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(Array.from(ws.sent[1]), [1, 2, 3]);
  const echoed = posted.filter((p) => p[TAG] === 'to-relay').map((p) => p.msg);
  assert(echoed.some((m) => m.kind === 'frame' && m.dir === 'out' && m.socket === 1 && m.b64 === 'AQID'));
  assert(echoed.some((m) => m.kind === 'send-confirmed' && m.sendId === 'move-a'));
  fromPage({ [TAG]: 'send', socket: 1, b64: 'AQID', sendId: 'move-a' });
  assert.strictEqual(ws.sent.length, 2, 'retrying one command must not duplicate its move');
  fromPage({ [TAG]: 'send', socket: 7, b64: 'AQID' });  // unknown socket: nothing sent
  assert.strictEqual(ws.sent.length, 2);
  assert(posted.some((p) => p.msg && p.msg.kind === 'send-failed' && p.msg.socket === 7));
  // The named socket closed after a reconnect: the move goes out on the
  // newer open socket instead of failing.
  const replaced = new WebSocket('wss://socket.svr.colonist.io/?version=2');
  replaced.readyState = FakeWebSocket.OPEN;
  ws.readyState = 3;  // CLOSED
  posted.length = 0;
  fromPage({ [TAG]: 'send', socket: 1, b64: 'BAUG', sendId: 'move-b' });
  await new Promise((r) => setTimeout(r, 20));  // the copy back is asynchronous
  assert.deepStrictEqual(Array.from(replaced.sent[0]), [4, 5, 6]);
  assert.strictEqual(ws.sent.length, 2, 'nothing more on the closed socket');
  assert(posted.some((p) => p.msg && p.msg.kind === 'frame' && p.msg.dir === 'out' && p.msg.socket === 2));
  assert(!posted.some((p) => p.msg && p.msg.kind === 'send-failed'));
  ws.readyState = FakeWebSocket.OPEN;
  replaced.readyState = 3;
  // ----- sequence numbers -----
  // A game message: header 3,1, channel "G1", then {action, payload, sequence}.
  const gameMsg = (action, seq) => {
    const body = [0x83, 0xa6, ...Buffer.from('action'), action, 0xa7, ...Buffer.from('payload'), 0xc3,
      0xa8, ...Buffer.from('sequence'), ...(seq <= 0x7f ? [seq] : [0xcc, seq])];
    return Uint8Array.from([3, 1, 2, ...Buffer.from('G1'), ...body]);
  };
  const seqOf = (bytes) => {
    const b = Buffer.from(bytes);
    const at = b.indexOf(Buffer.from([0xa8, ...Buffer.from('sequence')])) + 9;
    return b[at] === 0xcc ? b[at + 1] : b[at];
  };
  const { renumber } = window.__colonistAiBot;
  const game = new WebSocket('wss://socket.svr.colonist.io/game');
  game.readyState = FakeWebSocket.OPEN;
  fromPage({ [TAG]: 'new-game' });
  game.send(gameMsg(2, 2));
  game.send(gameMsg(6, 3));
  assert.deepStrictEqual(game.sent.map(seqOf), [2, 3], "the client's own numbers pass unchanged");
  fromPage({ [TAG]: 'send', socket: 3, b64: Buffer.from(gameMsg(9, 0)).toString('base64') });
  game.send(gameMsg(6, 4));  // the client doesn't know the bot sent number 4
  assert.deepStrictEqual(game.sent.map(seqOf), [2, 3, 4, 5], 'bot and client never share a number');
  assert.strictEqual(game.sent[2][13], 9, 'the bot message itself is unchanged apart from its number');

  // The client sends between the bot's moves (bot6's client, every turn): its
  // number trails the count by every bot message, which is not a new game.
  fromPage({ [TAG]: 'new-game' });
  game.sent.length = 0;
  const botMove = () => fromPage({ [TAG]: 'send', socket: 3, b64: Buffer.from(gameMsg(11, 0)).toString('base64') });
  game.send(gameMsg(62, 2));
  botMove();
  game.send(gameMsg(62, 3));
  botMove();
  botMove();
  game.send(gameMsg(62, 4));
  botMove();
  game.send(gameMsg(62, 5));
  assert.deepStrictEqual(game.sent.map(seqOf), [2, 3, 4, 5, 6, 7, 8, 9], 'a trailing client never reuses a number');

  // Crossing 127 -> 128 changes the number's width; the message stays valid.
  fromPage({ [TAG]: 'new-game' });
  game.sent.length = 0;
  game.send(gameMsg(2, 127));
  fromPage({ [TAG]: 'send', socket: 3, b64: Buffer.from(gameMsg(6, 0)).toString('base64') });
  assert.deepStrictEqual(game.sent.map(seqOf), [127, 128]);
  assert.strictEqual(game.sent[1].length, game.sent[0].length + 1);

  // A client message numbered far below the count: the client started again.
  game.sent.length = 0;
  game.send(gameMsg(2, 2));
  assert.deepStrictEqual(game.sent.map(seqOf), [2]);
  // Before the client sends anything in a game, the bot starts at 2.
  fromPage({ [TAG]: 'new-game' });
  game.sent.length = 0;
  fromPage({ [TAG]: 'send', socket: 3, b64: Buffer.from(gameMsg(15, 0)).toString('base64') });
  assert.deepStrictEqual(game.sent.map(seqOf), [2]);
  // Lobby messages are left alone.
  const lobby = Uint8Array.from([2, 7, 5, ...Buffer.from('lobby'), 0x80]);
  assert.strictEqual(renumber(lobby.buffer, true), lobby.buffer);

  console.log('page.js: all checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
