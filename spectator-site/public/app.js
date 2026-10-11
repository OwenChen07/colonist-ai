const STATUS = {
  playing: 'In a game',
  searching: 'Looking for a game',
  paused: 'Paused',
  offline: 'Offline',
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

function score({ wins, losses }) {
  const games = wins + losses;
  return games ? `${wins}–${losses} (${Math.round((100 * wins) / games)}%)` : '—';
}

function card(bot) {
  const name = encodeURIComponent(bot.name);
  const watch = bot.status === 'playing' && bot.gameId
    ? el('a', { class: 'watch', href: `https://colonist.io/#${encodeURIComponent(bot.gameId)}`, target: '_blank', rel: 'noopener' }, 'Watch live ↗')
    : el('span', { class: 'watch disabled' }, bot.status === 'searching' ? 'Waiting for a game' : 'Not playing');
  return el('article', { class: `bot ${bot.status}` },
    el('div', { class: 'top' },
      el('a', { class: 'name', href: `https://colonist.io/profile/${name}`, target: '_blank', rel: 'noopener' }, bot.name),
      el('span', { class: 'status' }, el('span', { class: 'dot' }), STATUS[bot.status] || bot.status)),
    el('dl', {},
      el('dt', {}, 'Today'), el('dd', {}, score(bot.today)),
      el('dt', {}, 'Ranked'), el('dd', {}, score(bot.ranked)),
      el('dt', {}, 'Net'), el('dd', {}, bot.net || '—')),
    watch);
}

async function refresh() {
  const box = document.getElementById('bots');
  try {
    const response = await fetch('/api/live', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.bots)) throw new Error('bad response');
    box.replaceChildren(...(data.bots.length ? data.bots.map(card) : [el('p', { class: 'empty' }, 'No bots reporting yet.')]));
    document.getElementById('updated').textContent = data.updatedAt
      ? `Updated ${new Date(data.updatedAt).toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: 'numeric', minute: '2-digit' })} Toronto time`
      : '';
  } catch {
    document.getElementById('updated').textContent = 'Can’t reach the status feed; retrying.';
  }
}

refresh();
setInterval(refresh, 15000);
