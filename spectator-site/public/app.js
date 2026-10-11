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

function standing(rank) {
  if (!rank) return el('div', { class: 'rank muted' }, 'Rank unavailable');
  if (!rank.tier) {
    const p = rank.placement || { played: 0, required: 10 };
    return el('div', { class: 'rank muted' }, `Placement games ${p.played}/${p.required}`);
  }
  const tier = rank.tier.split(' ')[0].toLowerCase();
  return el('div', { class: 'rank' },
    el('span', { class: `tier ${tier}` }, rank.tier),
    el('span', { class: 'rating' }, rank.rating != null ? `${rank.rating}` : '—', el('small', {}, ' Elo')),
    rank.globalRank != null ? el('span', { class: 'global' }, `#${rank.globalRank.toLocaleString('en-CA')} global`) : '');
}

function winRate(bot) {
  const r = bot.rank;
  if (r && r.winRate != null && r.games) return `${r.winRate}% of ${r.games.toLocaleString('en-CA')}`;
  return score(bot.ranked);
}

function lastTen(recent = '') {
  const dots = [...recent].map((c) => el('span', { class: `result ${c === 'W' ? 'win' : 'loss'}`, title: c === 'W' ? 'Win' : 'Loss' }));
  return el('div', { class: 'recent', 'aria-label': `Last ${recent.length} games: ${recent.split('').map((c) => (c === 'W' ? 'win' : 'loss')).join(', ')}` },
    el('span', { class: 'label' }, 'Last 10'),
    el('span', { class: 'dots' }, ...(dots.length ? dots : ['—'])));
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
    standing(bot.rank),
    el('dl', {},
      el('dt', {}, 'Ranked win rate'), el('dd', {}, winRate(bot)),
      el('dt', {}, 'Today'), el('dd', {}, score(bot.today)),
      el('dt', {}, 'Net'), el('dd', {}, bot.net || '—')),
    lastTen(bot.recent),
    watch);
}

const STEPS = { selfplay: 'playing self-play games', train: 'training', match: 'promotion match' };
const pct = (x) => (x == null ? '—' : `${(100 * x).toFixed(1)}%`);

function renderTraining(training, records) {
  const box = document.getElementById('training');
  if (!training) { box.hidden = true; return; }
  box.hidden = false;
  const since = new Date(training.since).toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: 'numeric', minute: '2-digit' });
  document.getElementById('trainingNow').replaceChildren(
    'Round ', el('strong', {}, String(training.round)), `: ${STEPS[training.step] || training.step} (since ${since}). `,
    'The bots play ', el('strong', {}, `round ${training.bestRound}`), ', the best net so far.');
  const ranked = Object.fromEntries((records || []).map((r) => [r.round, r]));
  const rows = [...training.rounds].reverse().map((r) => {
    const result = r.rolledBack ? ['Rolled back', 'back'] : r.promoted ? ['Promoted', 'up'] : ['Kept previous', 'kept'];
    const live = ranked[r.round];
    return el('tr', { class: r.round === training.bestRound ? 'best' : '' },
      el('td', {}, String(r.round), r.round === training.bestRound ? el('span', { class: 'chip' }, 'playing') : ''),
      el('td', {}, pct(r.score), el('small', {}, r.games ? ` of ${r.games.toLocaleString('en-CA')}` : '')),
      el('td', {}, el('span', { class: `result-tag ${result[1]}` }, result[0])),
      el('td', {}, pct(r.original)),
      el('td', {}, pct(r.benchmark)),
      el('td', {}, live && live.wins + live.losses ? score(live) : '—'));
  });
  document.getElementById('rounds').replaceChildren(...rows);
}

async function refresh() {
  const box = document.getElementById('bots');
  try {
    const response = await fetch('/api/live', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.bots)) throw new Error('bad response');
    renderTraining(data.training, data.netRecords);
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
