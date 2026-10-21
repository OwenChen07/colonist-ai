import { timingSafeEqual } from 'node:crypto';
import { KEY, redis } from './_store.js';

// Each bot VM posts its bots' status. Only these fields are accepted.
const STATUSES = new Set(['playing', 'searching', 'paused', 'offline', 'retired']);

function exact(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && Object.keys(value).every((key) => keys.includes(key));
}

function count(value) { return Number.isInteger(value) && value >= 0 && value < 1e6; }
function record(value) { return exact(value, ['wins', 'losses']) && count(value.wins) && count(value.losses); }
function nullOr(value, test) { return value === null || (typeof value === 'string' && test.test(value)); }

function validBot(bot) {
  const keys = ['name', 'status', 'queue', 'net', 'gameId', 'today', 'ranked'];
  return (exact(bot, keys) || exact(bot, [...keys, 'recent']))
    && (bot.recent === undefined || (typeof bot.recent === 'string' && /^[WL]{0,10}$/.test(bot.recent)))
    && typeof bot.name === 'string' && /^[A-Za-z0-9_]{1,32}$/.test(bot.name)
    && STATUSES.has(bot.status)
    && (bot.queue === null || ['ranked', 'casual'].includes(bot.queue))
    && nullOr(bot.net, /^[\w .-]{1,40}$/)
    && nullOr(bot.gameId, /^[A-Za-z0-9]{1,32}$/)
    && record(bot.today) && record(bot.ranked);
}

function fraction(value) { return value === null || (typeof value === 'number' && value >= 0 && value <= 1.5); }
function iso(value) { return typeof value === 'string' && value.length < 40 && !Number.isNaN(Date.parse(value)); }

function validRound(row) {
  return exact(row, ['round', 'games', 'score', 'promoted', 'rolledBack', 'benchmark', 'original', 'liveError'])
    && count(row.round) && count(row.games)
    && fraction(row.score) && fraction(row.benchmark) && fraction(row.original) && fraction(row.liveError)
    && typeof row.promoted === 'boolean' && typeof row.rolledBack === 'boolean';
}

function nullCount(value) { return value === null || count(value); }

function validProgress(p) {
  if (p === null) return true;
  if (!exact(p, ['selfplay', 'training', 'match'])) return false;
  const { selfplay: s, training: t, match: m } = p;
  return (s === null || (exact(s, ['started', 'games', 'done', 'localDone', 'localTotal'])
      && iso(s.started) && nullCount(s.games) && typeof s.done === 'boolean'
      && count(s.localDone) && nullCount(s.localTotal)))
    && (t === null || (exact(t, ['started', 'epochs', 'total']) && iso(t.started) && count(t.total)
      && Array.isArray(t.epochs) && t.epochs.length <= 20 && t.epochs.every((e) =>
        exact(e, ['epoch', 'valueLoss', 'accuracy', 'seconds']) && count(e.epoch)
        && fraction(e.valueLoss) && fraction(e.accuracy) && count(e.seconds))))
    && (m === null || (exact(m, ['started', 'maxGames', 'games', 'score', 'margin'])
      && iso(m.started) && nullCount(m.maxGames) && count(m.games)
      && fraction(m.score) && fraction(m.margin)));
}

function validTraining(t) {
  const keys = ['round', 'step', 'bestRound', 'since', 'rounds'];
  const extra = Object.keys(t || {}).filter((k) => !keys.includes(k));
  return t === null || (keys.every((k) => k in t)
    && extra.every((k) => ['progress', 'epochs'].includes(k))
    && (!('progress' in t) || validProgress(t.progress))
    && (!('epochs' in t) || (Number.isInteger(t.epochs) && t.epochs > 0 && t.epochs < 100))
    && count(t.round) && count(t.bestRound) && iso(t.since)
    && typeof t.step === 'string' && /^[a-z_]{0,20}$/.test(t.step)
    && Array.isArray(t.rounds) && t.rounds.length <= 200 && t.rounds.every(validRound));
}

function validNetRecords(rows) {
  return Array.isArray(rows) && rows.length <= 200 && rows.every((row) =>
    exact(row, ['round', 'wins', 'losses']) && count(row.round) && count(row.wins) && count(row.losses));
}

function money(value) { return value === null || (typeof value === 'number' && value >= 0 && value < 1e5); }

function validCredits(rows) {
  return rows === null || (Array.isArray(rows) && rows.length <= 6 && rows.every((row) =>
    (exact(row, ['provider', 'remaining', 'total', 'asOf', 'source'])
      || (exact(row, ['provider', 'remaining', 'total', 'asOf', 'source', 'currency'])
        && ['USD', 'CAD'].includes(row.currency)))
    && typeof row.provider === 'string' && /^[A-Za-z]{1,12}$/.test(row.provider)
    && money(row.remaining) && money(row.total) && (row.asOf === null || iso(row.asOf))
    && ['api', 'console'].includes(row.source)));
}

function loss(value) { return value === null || (typeof value === 'number' && value >= 0 && value < 10); }

// The people model's hourly refreshes (opponent_model.py refresh).
function validPeopleModel(p) {
  return p === null || (exact(p, ['refreshes']) && Array.isArray(p.refreshes)
    && p.refreshes.length <= 48 && p.refreshes.every((r) =>
      exact(r, ['at', 'replaced', 'rows', 'newAccuracy', 'newCrossEntropy', 'currentAccuracy', 'currentCrossEntropy'])
      && iso(r.at) && typeof r.replaced === 'boolean' && count(r.rows)
      && fraction(r.newAccuracy) && fraction(r.currentAccuracy)
      && loss(r.newCrossEntropy) && loss(r.currentCrossEntropy)));
}

function result(row) {
  return count(row.iter) && count(row.opponent) && count(row.games)
    && fraction(row.score) && fraction(row.margin);
}

// E54, research's league run: iterations, raw evaluations, searched gates.
function validE54(e) {
  return e === null || (exact(e, ['iterations', 'evals', 'gates'])
    && Array.isArray(e.iterations) && e.iterations.length <= 200 && e.iterations.every((r) =>
      exact(r, ['iter', 'games', 'at']) && count(r.iter) && count(r.games) && iso(r.at))
    && Array.isArray(e.evals) && e.evals.length <= 50 && e.evals.every((r) =>
      exact(r, ['iter', 'opponent', 'games', 'score', 'margin']) && result(r))
    && Array.isArray(e.gates) && e.gates.length <= 30 && e.gates.every((r) =>
      exact(r, ['name', 'iter', 'opponent', 'games', 'score', 'margin', 'pooled', 'at']) && result(r)
      && typeof r.name === 'string' && /^[\w .-]{1,40}$/.test(r.name)
      && typeof r.pooled === 'boolean' && (r.at === null || iso(r.at))));
}

export function validBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return false;
  const base = ['schema', 'source', 'updatedAt', 'bots'];
  const allowed = [...base, 'training', 'netRecords', 'credits', 'peopleModel', 'e54'];
  const keys = Object.keys(body);
  return base.every((k) => keys.includes(k)) && keys.every((k) => allowed.includes(k))
    && ('training' in body) === ('netRecords' in body)
    && (!('training' in body) || (validTraining(body.training) && validNetRecords(body.netRecords)))
    && (!('credits' in body) || validCredits(body.credits))
    && (!('peopleModel' in body) || validPeopleModel(body.peopleModel))
    && (!('e54' in body) || validE54(body.e54))
    && body.schema === 2
    && typeof body.source === 'string' && /^[a-z0-9-]{1,32}$/.test(body.source)
    && typeof body.updatedAt === 'string' && !Number.isNaN(Date.parse(body.updatedAt))
    && Array.isArray(body.bots) && body.bots.length <= 8 && body.bots.every(validBot);
}

export function authorized(request) {
  const secret = process.env.SPECTATOR_INGEST_KEY;
  const supplied = request.headers.authorization?.replace(/^Bearer\s+/i, '') || '';
  if (!secret || !supplied) return false;
  const a = Buffer.from(secret);
  const b = Buffer.from(supplied);
  return a.length === b.length && timingSafeEqual(a, b);
}

export default async function handler(request, response) {
  if (request.method !== 'POST') {
    response.setHeader('Allow', 'POST');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  if (!authorized(request)) return response.status(401).json({ error: 'Unauthorized' });
  // Publishers from before 16 Oct still send the retired orchestrator report: drop it.
  const raw = request.body;
  const body = raw && typeof raw === 'object' && !Array.isArray(raw)
    ? Object.fromEntries(Object.entries(raw).filter(([k]) => k !== 'orchestrator')) : raw;
  if (!validBody(body)) return response.status(400).json({ error: 'Invalid status' });
  try {
    await (await redis()).hSet(KEY, body.source, JSON.stringify(body));
    response.setHeader('Cache-Control', 'no-store');
    return response.status(200).json({ ok: true });
  } catch (error) {
    console.error('spectator write failed', error?.name || 'unknown', String(error?.message || '').slice(0, 200));
    return response.status(503).json({ error: 'Status could not be stored' });
  }
}
