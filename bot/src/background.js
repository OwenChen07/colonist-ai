/*
 * Colonist AI Bot, background worker: one connection to the bridge per
 * colonist tab. Frames from the tab go to the bridge; the bridge's answers go
 * back to the tab. If the bridge isn't running, it retries every few seconds,
 * and when it connects the tab resends its game from the start.
 *
 * An open WebSocket keeps this worker alive while a game is on.
 */
const BRIDGE_URL = 'ws://127.0.0.1:8765';
const RETRY_MS = 3000;

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'colonist-ai-bot') return;
  let socket = null;
  let closed = false;
  let retry = null;

  function open() {
    if (closed) return;
    socket = new WebSocket(BRIDGE_URL);
    socket.onopen = () => port.postMessage({ kind: 'bridge-connected' });
    socket.onmessage = (e) => {
      try { port.postMessage(JSON.parse(e.data)); } catch (err) { /* ignore */ }
    };
    socket.onclose = () => {
      socket = null;
      if (closed) return;
      port.postMessage({ kind: 'bridge-disconnected' });
      retry = setTimeout(open, RETRY_MS);
    };
    socket.onerror = () => {};  // onclose follows
  }

  port.onMessage.addListener((msg) => {
    // Frames before the bridge is up are not lost: the tab resends its backlog
    // when it hears 'bridge-connected'.
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
  });

  port.onDisconnect.addListener(() => {
    closed = true;
    clearTimeout(retry);
    if (socket) socket.close();
  });

  open();
});
