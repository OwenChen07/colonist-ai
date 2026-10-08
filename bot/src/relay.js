/*
 * Colonist AI Bot, relay: passes frames from page.js to the background worker
 * (which holds the connection to the bridge), and shows the bridge's answers
 * in a small panel on the page.
 *
 * Runs in the extension's isolated world: page.js can't reach the extension
 * APIs, and the page's own security policy can block a direct connection to
 * this computer, so messages go page.js -> relay.js -> background.js -> bridge.
 */
(function () {
  'use strict';
  if (window.top !== window && !location.hostname.endsWith('colonist.io')) return;
  const TAG = 'colonist-ai-bot';
  let port = null;

  function connect() {
    port = chrome.runtime.connect({ name: 'colonist-ai-bot' });
    port.onMessage.addListener(onBridge);
    port.onDisconnect.addListener(() => {
      port = null;
      show({ kind: 'status', state: 'offline', text: 'extension reloaded; reload the page' });
    });
  }

  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data || e.data[TAG] !== 'to-relay') return;
    if (!port) connect();
    port.postMessage(e.data.msg);
  });

  function onBridge(msg) {
    if (msg.kind === 'send') {
      window.postMessage({ [TAG]: 'send', socket: msg.socket, b64: msg.b64, sendId: msg.sendId }, '*');
      return;
    }
    if (msg.kind === 'new-game') {
      window.postMessage({ [TAG]: 'new-game' }, '*');
      return;
    }
    if (msg.kind === 'bridge-connected') {
      // A fresh bridge knows nothing: send this page's game from the start.
      window.postMessage({ [TAG]: 'replay-backlog' }, '*');
    }
    show(msg);
  }

  // ----- panel -----
  let panel = null;
  const view = { link: 'connecting to the bridge…', net: '', state: '', text: '', move: null, playing: false };

  function mount() {
    if (window.top !== window || panel || !document.body) return;
    panel = document.createElement('div');
    Object.assign(panel.style, {
      position: 'fixed', right: '8px', top: '8px', zIndex: 2147483647, width: '260px',
      font: '12px/1.4 system-ui, sans-serif', padding: '8px 10px', borderRadius: '8px',
      background: 'rgba(255,255,255,0.95)', color: '#222', border: '1px solid #1565c0',
      boxShadow: '0 2px 8px rgba(0,0,0,0.2)', pointerEvents: 'none',
    });
    document.body.appendChild(panel);
    render();
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function render() {
    if (!panel) return;
    let html = `<b>Colonist AI</b> <span style="color:#666">(${view.playing ? 'playing' : 'advisor'})</span><br>`;
    html += `<span style="color:#666">${esc(view.link)}</span>`;
    if (view.net) html += `<br><span style="color:#666">net: ${esc(view.net)}</span>`;
    if (view.text) html += `<br>${esc(view.state)}: ${esc(view.text)}`;
    const m = view.move;
    if (m) {
      html += `<hr style="border:none;border-top:1px solid #ddd;margin:6px 0">`;
      const steps = m.plan.length > 1 ? m.plan.map((p) => p.label).join(', ') : m.move.label;
      html += `<b>Play: ${esc(steps)}</b><br>`;
      html += `win chance ${Math.round(m.win_chance * 100)}% · ${m.ms} ms<br>`;
      for (const o of m.options) {
        html += `<span style="color:#555">${esc(o.move.label)}: ${Math.round(o.share * 100)}%</span><br>`;
      }
    }
    panel.innerHTML = html;
  }

  function show(msg) {
    if (msg.kind === 'bridge-connected') view.link = 'connected to the bridge';
    else if (msg.kind === 'info') {
      // Which net the bridge loaded, so it's clear what is playing.
      const epoch = msg.epoch != null ? `, epoch ${msg.epoch}` : '';
      view.net = `${msg.net}${epoch} · ${msg.simulations} sims`;
    } else if (msg.kind === 'bridge-disconnected') {
      view.link = 'bridge not running: start it with python -m catanatron.colonist.bridge <net.npz>';
      view.net = '';
      view.move = null;
    } else if (msg.kind === 'status') {
      view.state = msg.state;
      view.text = msg.text;
      if (msg.state !== 'following') view.move = null;
    } else if (msg.kind === 'move') {
      view.move = msg;
      view.playing = !!msg.played;
    }
    render();
  }

  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);
})();
