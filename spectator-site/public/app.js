const STATUS = {
  playing: 'In a game',
  searching: 'Looking for a game',
  paused: 'Paused',
  offline: 'Offline',
  retired: 'Retired',
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
  if (bot.status === 'retired') return retiredCard(bot, name);
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

// A retired account: its final standing and record, nothing live.
function retiredCard(bot, name) {
  return el('article', { class: 'bot retired' },
    el('div', { class: 'top' },
      el('a', { class: 'name', href: `https://colonist.io/profile/${name}`, target: '_blank', rel: 'noopener' }, bot.name),
      el('span', { class: 'status' }, el('span', { class: 'dot' }), STATUS.retired)),
    standing(bot.rank),
    el('dl', {},
      el('dt', {}, 'Ranked win rate'), el('dd', {}, winRate(bot)),
      el('dt', {}, 'Last net'), el('dd', {}, bot.net || '—')),
    lastTen(bot.recent));
}

const pct = (x) => (x == null ? '—' : `${(100 * x).toFixed(1)}%`);

const clock = (ms) => new Date(ms).toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: 'numeric', minute: '2-digit' });
const minutes = (ms) => {
  const m = Math.max(1, Math.round(ms / 60000));
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
};

// The current round, step by step: what's done, what's running, when it should end.
function steps(training) {
  const p = training.progress;
  if (!p) return [];
  const now = Date.now();
  const out = [];
  const s = p.selfplay;
  const t = p.training;
  const m = p.match;
  if (s) {
    const start = Date.parse(s.started);
    const games = s.games ? `${s.games.toLocaleString('en-CA')} games` : 'games';
    if (s.done || t) {
      const end = t ? Date.parse(t.started) : now;
      out.push(['done', 'Self-play', `${games} in ${minutes(end - start)}`]);
    } else {
      const frac = s.localTotal ? s.localDone / s.localTotal : 0;
      const eta = frac > 0.05 ? ` · done about ${clock(start + (now - start) / frac)}` : '';
      out.push(['now', 'Self-play', `${games}: about ${Math.round(100 * frac)}%${eta}`]);
    }
  } else out.push(['next', 'Self-play', '']);
  if (t) {
    const done = t.epochs.length;
    const last = t.epochs[done - 1];
    const quality = last ? ` · value loss ${last.valueLoss.toFixed(3)}, accuracy ${(100 * last.accuracy).toFixed(1)}%` : '';
    if (m) {
      out.push(['done', 'Training', `${t.total} epochs${quality}`]);
    } else {
      // CPU and GPU epochs differ by more than tenfold. Wait for a measured
      // epoch before showing an estimate, especially after a device switch.
      let estimate = 'estimating after the first epoch';
      if (done) {
        const each = t.epochs.reduce((a, e) => a + e.seconds, 0) / done;
        const eta = Date.parse(t.started) + 1000 * t.total * each;
        estimate = eta > now ? `done about ${clock(eta)}` : 'finishing; awaiting update';
      }
      out.push(['now', 'Training', `epoch ${done} of ${t.total} done${quality} · ${estimate}`]);
    }
  } else out.push(['next', 'Training', `${training.epochs || 3} epochs; estimate after the first epoch`]);
  if (m) {
    const score = m.score != null ? `${(100 * m.score).toFixed(1)}% ± ${(100 * m.margin).toFixed(1)}% after ${m.games.toLocaleString('en-CA')} games` : 'starting';
    out.push(['now', 'Promotion match', `vs round ${training.bestRound}: ${score} (must be clearly better, about 52%, to be promoted; at most ${(m.maxGames || 2000).toLocaleString('en-CA')} games)`]);
  } else out.push(['next', 'Promotion match', `vs round ${training.bestRound}, the best net`]);
  return out;
}

function renderTraining(training, records) {
  const box = document.getElementById('training');
  if (!training) { box.hidden = true; return; }
  box.hidden = false;
  document.getElementById('trainingNow').replaceChildren(
    'Round ', el('strong', {}, String(training.round)), ` is under way. `,
    'The bots play ', el('strong', {}, `round ${training.bestRound}`), ', the best net so far.');
  document.getElementById('steps').replaceChildren(...steps(training).map(([state, name, text]) =>
    el('li', { class: `step ${state}` },
      el('span', { class: 'mark' }, state === 'done' ? '✓' : ''),
      el('div', {}, el('strong', {}, name), el('span', {}, text)))));
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

const when = (iso) => (iso ? new Date(iso).toLocaleString('en-CA', {
  timeZone: 'America/Toronto', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
}) : '—');
const loss = (x) => (x == null ? '—' : x.toFixed(3));
const thousands = (n) => n.toLocaleString('en-CA');

// The live accounts whose net label matches, e.g. "Vasos5306 and Tedi4960".
function players(bots, pattern) {
  const names = bots.filter((b) => b.status !== 'retired' && pattern.test(b.net || '')).map((b) => b.name);
  if (!names.length) return 'No account plays it right now.';
  const list = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0];
  return `Played by ${list}.`;
}

function renderPeople(model, bots) {
  const box = document.getElementById('people');
  const rows = model?.refreshes || [];
  if (!rows.length) { box.hidden = true; return; }
  box.hidden = false;
  const last = rows.at(-1);
  document.getElementById('peopleNow').replaceChildren(
    `${players(bots, /people/i)} Last refresh ${when(last.at)}: the new net picks the person's move `,
    el('strong', {}, pct(last.newAccuracy)), ` of the time (${last.replaced ? 'now in use' : 'kept the previous net'}).`);
  document.getElementById('refreshes').replaceChildren(...[...rows].reverse().map((r) => el('tr', {},
    el('td', {}, when(r.at)),
    el('td', {}, r.rows ? thousands(r.rows) : '—'),
    el('td', {}, pct(r.newAccuracy)),
    el('td', {}, loss(r.newCrossEntropy)),
    el('td', {}, pct(r.currentAccuracy)),
    el('td', {}, loss(r.currentCrossEntropy)),
    el('td', {}, el('span', { class: `result-tag ${r.replaced ? 'up' : 'kept'}` }, r.replaced ? 'Replaced' : 'Kept')))));
}

function renderE54(run, bots) {
  const box = document.getElementById('e54');
  if (!run || !(run.iterations.length || run.gates.length)) { box.hidden = true; return; }
  box.hidden = false;
  const last = run.iterations.at(-1);
  const games = run.iterations.reduce((a, r) => a + r.games, 0);
  document.getElementById('e54Now').replaceChildren(
    `${players(bots, /e54/i)} `,
    ...(last ? ['Iteration ', el('strong', {}, String(last.iter)), ` (${when(last.at)}); `,
      el('strong', {}, thousands(games)), ' training games in all.'] : []));
  const margin = (r) => `${pct(r.score)} ± ${(100 * r.margin).toFixed(1)}%`;
  document.getElementById('e54Gates').replaceChildren(...[...run.gates].reverse().map((r) =>
    el('tr', { class: r.pooled ? 'best' : '' },
      el('td', {}, r.pooled ? 'Pooled' : r.name, r.pooled ? el('span', { class: 'chip' }, 'overall') : ''),
      el('td', {}, String(r.iter)),
      el('td', {}, `round ${r.opponent}`),
      el('td', {}, thousands(r.games)),
      el('td', {}, margin(r)),
      el('td', {}, when(r.at)))));
  document.getElementById('e54Evals').replaceChildren(...[...run.evals].reverse().map((r) =>
    el('tr', {}, el('td', {}, String(r.iter)), el('td', {}, thousands(r.games)), el('td', {}, margin(r)))));
}

function renderCredits(rows) {
  const box = document.getElementById('credits');
  if (!rows || !rows.length) { box.hidden = true; return; }
  box.hidden = false;
  document.getElementById('creditCards').replaceChildren(...rows.map((c) => {
    const known = c.remaining != null && c.total;
    const sign = c.currency === 'CAD' ? 'CA$' : 'US$';
    const share = known ? Math.max(0, Math.min(1, c.remaining / c.total)) : 0;
    const level = share < 0.2 ? 'low' : share < 0.5 ? 'mid' : 'ok';
    const asOf = c.asOf ? new Date(c.asOf).toLocaleDateString('en-CA', { timeZone: 'America/Toronto', month: 'short', day: 'numeric' }) : null;
    return el('div', { class: 'credit' },
      el('div', { class: 'credit-top' }, el('strong', {}, c.provider),
        el('span', {}, known ? `${sign}${c.remaining.toFixed(2)} of ${sign}${c.total.toFixed(0)}` : 'not read yet')),
      el('div', { class: 'bar' }, el('span', { class: `fill ${level}`, style: `width: ${(100 * share).toFixed(1)}%` })),
      el('small', {}, asOf ? `as of ${asOf}` : (c.provider === 'GCP' ? 'from the console' : 'needs a sign-in')));
  }));
}

async function refresh() {
  const box = document.getElementById('bots');
  try {
    const response = await fetch('/api/live', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.bots)) throw new Error('bad response');
    renderTraining(data.training, data.netRecords);
    renderPeople(data.peopleModel, data.bots);
    renderE54(data.e54, data.bots);
    renderCredits(data.credits);
    const active = data.bots.filter((b) => b.status !== 'retired');
    const retired = data.bots.filter((b) => b.status === 'retired');
    box.replaceChildren(...(active.length ? active.map(card) : [el('p', { class: 'empty' }, 'No bots reporting yet.')]));
    document.getElementById('retired').hidden = !retired.length;
    document.getElementById('retiredBots').replaceChildren(...retired.map(card));
    document.getElementById('updated').textContent = data.updatedAt
      ? `Updated ${new Date(data.updatedAt).toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: 'numeric', minute: '2-digit' })} Toronto time`
      : '';
  } catch {
    document.getElementById('updated').textContent = 'Can’t reach the status feed; retrying.';
  }
}

refresh();
setInterval(refresh, 15000);
