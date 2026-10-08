const $ = (id) => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';
const colors = { RED: '#c46a58', BLUE: '#499ab6', ORANGE: '#dc9a4c', WHITE: '#e5e2d8' };
const terrain = { WOOD: '#6b9270', BRICK: '#bd7965', SHEEP: '#9ead75', WHEAT: '#d9b967', ORE: '#85959b', DESERT: '#d0ae7f' };
const symbols = { WOOD: '♠', BRICK: '▤', SHEEP: '●', WHEAT: '✳', ORE: '◆', DESERT: '·' };
let payload = null;
let frameIndex = -1;
let lastGameKey = null;
let followLatest = true;

function svg(tag, attrs, parent) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  parent.appendChild(node);
  return node;
}
function hexPoint(cx, cy, radius = 0.98) {
  return Array.from({ length: 6 }, (_, i) => {
    const a = (i * 60 - 30) * Math.PI / 180;
    return `${(cx + radius * Math.cos(a)).toFixed(3)},${(cy + radius * Math.sin(a)).toFixed(3)}`;
  }).join(' ');
}
function board(frame) {
  const canvas = $('board');
  canvas.replaceChildren();
  $('boardEmpty').hidden = !!frame;
  if (!frame) return;
  const nodes = new Map((frame.nodes || []).map((n) => [n.id, n]));
  for (const tile of frame.tiles || []) {
    const x = Math.sqrt(3) * (tile.q + tile.r / 2);
    const y = 1.5 * tile.r;
    svg('polygon', { points: hexPoint(x, y), fill: terrain[tile.resource] || terrain.DESERT, stroke: '#f0dfc2', 'stroke-width': '.08' }, canvas);
    svg('text', { x, y: y - .15, 'text-anchor': 'middle', 'font-size': '.44', fill: '#273b3c', 'font-family': 'Georgia, serif', opacity: '.65' }, canvas).textContent = symbols[tile.resource] || '·';
    if (tile.number != null) {
      svg('circle', { cx: x, cy: y + .3, r: .25, fill: '#f7ecd6', stroke: '#4d5550', 'stroke-width': '.025' }, canvas);
      svg('text', { x, y: y + .4, 'text-anchor': 'middle', 'font-size': '.28', 'font-weight': '700', fill: tile.number === 6 || tile.number === 8 ? '#a04b40' : '#3c4946' }, canvas).textContent = tile.number;
    }
    if (tile.robber) {
      svg('circle', { cx: x + .45, cy: y - .38, r: .22, fill: '#253940', stroke: '#f5e8cc', 'stroke-width': '.04' }, canvas);
      svg('text', { x: x + .45, y: y - .3, 'text-anchor': 'middle', 'font-size': '.27', fill: '#f5e8cc' }, canvas).textContent = 'R';
    }
  }
  for (const road of frame.roads || []) {
    const a = nodes.get(road.a), b = nodes.get(road.b);
    if (!a || !b) continue;
    svg('line', { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: '#35423e', 'stroke-width': '.25', 'stroke-linecap': 'round' }, canvas);
    svg('line', { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: colors[road.color] || '#fff', 'stroke-width': '.18', 'stroke-linecap': 'round' }, canvas);
  }
  for (const building of frame.buildings || []) {
    const p = nodes.get(building.node);
    if (!p) continue;
    const city = String(building.type).toUpperCase().includes('CITY');
    svg(city ? 'rect' : 'circle', city
      ? { x: p.x - .23, y: p.y - .23, width: .46, height: .46, rx: .06, fill: colors[building.color] || '#fff', stroke: '#283a3c', 'stroke-width': '.07' }
      : { cx: p.x, cy: p.y, r: .18, fill: colors[building.color] || '#fff', stroke: '#283a3c', 'stroke-width': '.06' }, canvas);
  }
}
function formatMove(value) {
  return String(value || 'Move').replaceAll('_', ' ').toLowerCase().replace(/^\w/, (s) => s.toUpperCase());
}
function renderFrame(frame) {
  board(frame);
  if (!frame) return;
  $('boardTitle').textContent = frame.phase === 'finished' ? 'Latest replay' : 'Current game';
  $('boardPill').textContent = frame.phase === 'finished' ? 'FINAL POSITION' : frame.phase === 'live' ? 'LIVE / DELAYED' : 'PAUSED';
  $('currentTurn').textContent = frame.toMove || (frame.winner ? `${frame.winner} won` : '—');
  $('moveNumber').textContent = String(frame.moveNumber ?? '—');
  const cards = $('playerCards'); cards.replaceChildren();
  for (const player of frame.players || []) {
    const card = document.createElement('div'); card.className = 'player-card';
    const icon = document.createElement('span'); icon.className = 'player-icon'; icon.style.setProperty('--player', colors[player.color] || '#ddd');
    const detail = document.createElement('div');
    const name = document.createElement('strong'); name.textContent = player.bot ? `BOT · ${player.color}` : `OPPONENT · ${player.color}`;
    const counts = document.createElement('small'); counts.textContent = `${player.resources ?? 0} resources · ${player.developmentCards ?? 0} dev cards`;
    detail.append(name, counts);
    const points = document.createElement('span'); points.className = 'player-points'; points.textContent = String(player.points ?? 0);
    card.append(icon, detail, points); cards.appendChild(card);
  }
  const moves = $('moveList'); moves.replaceChildren();
  for (const move of [...(frame.recentMoves || [])].reverse().slice(0, 7)) {
    const li = document.createElement('li');
    const action = document.createElement('span'); action.textContent = formatMove(move.type);
    const who = document.createElement('span'); who.textContent = move.color === frame.botColor ? 'BOT' : 'OPPONENT';
    li.append(action, who); moves.appendChild(li);
  }
  if (!moves.children.length) moves.textContent = 'The first move is coming up.';
}
function setTimeline(frames) {
  const slider = $('timelineSlider');
  slider.disabled = frames.length < 2;
  slider.max = String(Math.max(0, frames.length - 1));
  slider.value = String(Math.max(0, frameIndex));
  $('timelineStart').textContent = frames.length ? `MOVE ${frames[0].moveNumber}` : 'OPENING';
  $('timelinePosition').textContent = frameIndex < 0 ? 'WAITING' : `MOVE ${frames[frameIndex]?.moveNumber ?? '—'} / ${frames.at(-1)?.moveNumber ?? '—'}`;
  $('returnLive').disabled = frameIndex >= frames.length - 1;
  $('timelineDetail').textContent = frames.length > 1 ? `${frames.length} saved positions` : 'Replay available as moves arrive';
}
function renderStats(data) {
  const stats = Array.isArray(data.stats) ? data.stats : [];
  const total = stats.reduce((n, s) => n + (s.wins || 0) + (s.losses || 0), 0);
  const wins = stats.reduce((n, s) => n + (s.wins || 0), 0);
  $('totalGames').textContent = String(total);
  $('overallRate').textContent = total ? `${Math.round(wins / total * 100)}%` : '—';
  $('overallRecord').textContent = total ? `${wins} wins · ${total - wins} losses` : 'from verified results';
  $('latestModel').textContent = data.meta?.activeVersion != null ? `V${data.meta.activeVersion}` : '—';
  $('nowVersion').textContent = data.meta?.activeVersion != null ? `Version ${data.meta.activeVersion}` : 'Awaiting game';
  $('nowDetail').textContent = data.live?.phase === 'live' ? 'Ranked match in progress' : 'Showing the latest checked game';
  const versions = $('versionStats'); versions.replaceChildren();
  for (const stat of stats.slice().sort((a, b) => b.version - a.version)) {
    const count = (stat.wins || 0) + (stat.losses || 0), rate = count ? Math.round((stat.wins || 0) / count * 100) : 0;
    const card = document.createElement('div'); card.className = `version-card${stat.version === data.meta?.activeVersion ? ' active' : ''}`;
    const top = document.createElement('div'); top.className = 'version-top';
    const label = document.createElement('span'); label.textContent = `VERSION ${stat.version}`;
    const tag = document.createElement('span'); tag.textContent = stat.version === data.meta?.activeVersion ? 'CURRENT' : 'ARCHIVE'; top.append(label, tag);
    const value = document.createElement('strong'); value.textContent = `${rate}%`;
    const track = document.createElement('div'); track.className = 'version-track'; const fill = document.createElement('span'); fill.style.width = `${rate}%`; track.appendChild(fill);
    const foot = document.createElement('small'); foot.textContent = `${stat.wins || 0} ${stat.wins === 1 ? 'win' : 'wins'} · ${stat.losses || 0} ${stat.losses === 1 ? 'loss' : 'losses'} · ${count} verified`;
    card.append(top, value, track, foot); versions.appendChild(card);
  }
  if (!stats.length) versions.textContent = 'Version results will appear after verified matches are synced.';
  const history = $('gameHistory'); history.replaceChildren();
  for (const game of (data.recentGames || []).slice(0, 12)) {
    const row = document.createElement('div'); row.className = 'history-item';
    const left = document.createElement('span'); left.textContent = `V${game.version} · ${new Date(game.at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`;
    const result = document.createElement('strong'); result.className = game.result === 'win' ? 'result-win' : 'result-loss'; result.textContent = game.result === 'win' ? 'WIN' : 'LOSS';
    row.append(left, result); history.appendChild(row);
  }
  if (!history.children.length) history.textContent = 'No verified matches have been synced yet.';
}
function render(data) {
  payload = data;
  const frames = Array.isArray(data.rewind) ? data.rewind : data.live ? [data.live] : [];
  const gameKey = data.live?.gameKey || frames.at(-1)?.gameKey;
  if (gameKey !== lastGameKey) followLatest = true;
  if (followLatest || frameIndex < 0) frameIndex = frames.length - 1;
  else frameIndex = Math.min(frameIndex, frames.length - 1);
  lastGameKey = gameKey;
  renderFrame(frames[frameIndex] || data.live || null);
  setTimeline(frames);
  renderStats(data);
  const age = Date.now() - Date.parse(data.updatedAt || 0);
  const stale = !Number.isFinite(age) || age > 90_000;
  $('feedStatus').className = `feed-status ${stale ? 'offline' : data.live?.phase === 'live' ? '' : 'waiting'}`;
  $('feedStatus').lastChild.textContent = stale ? 'Feed delayed' : data.live?.phase === 'live' ? 'Receiving board' : 'Between games';
  $('lastSignal').textContent = data.updatedAt ? new Date(data.updatedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—';
}
async function refresh() {
  try {
    const response = await fetch('/api/live', { cache: 'no-store' });
    if (!response.ok) throw new Error('feed unavailable');
    const data = await response.json();
    if (data.state === 'waiting') { $('feedStatus').className = 'feed-status waiting'; $('feedStatus').lastChild.textContent = 'Waiting for feed'; return; }
    render(data);
  } catch { $('feedStatus').className = 'feed-status offline'; $('feedStatus').lastChild.textContent = 'Feed delayed'; }
}
$('timelineSlider').addEventListener('input', (event) => {
  frameIndex = Number(event.target.value);
  const frames = payload?.rewind || [];
  followLatest = frameIndex >= frames.length - 1;
  renderFrame(frames[frameIndex] || null);
  setTimeline(frames);
});
$('returnLive').addEventListener('click', () => { followLatest = true; render(payload); });
setInterval(() => { $('headerClock').textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }); }, 1000);
refresh(); setInterval(refresh, 15_000);
