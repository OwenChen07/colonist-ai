import { timingSafeEqual } from 'node:crypto';
import { KEY, redis } from './_store.js';

// Each bot VM posts its bots' status. Only these fields are accepted.
const STATUSES = new Set(['playing', 'searching', 'paused', 'offline']);

function exact(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && Object.keys(value).every((key) => keys.includes(key));
}

function count(value) { return Number.isInteger(value) && value >= 0 && value < 1e6; }
function record(value) { return exact(value, ['wins', 'losses']) && count(value.wins) && count(value.losses); }
function nullOr(value, test) { return value === null || (typeof value === 'string' && test.test(value)); }

function validBot(bot) {
  return exact(bot, ['name', 'status', 'queue', 'net', 'gameId', 'today', 'ranked'])
    && typeof bot.name === 'string' && /^[A-Za-z0-9_]{1,32}$/.test(bot.name)
    && STATUSES.has(bot.status)
    && (bot.queue === null || ['ranked', 'casual'].includes(bot.queue))
    && nullOr(bot.net, /^[\w .-]{1,40}$/)
    && nullOr(bot.gameId, /^[A-Za-z0-9]{1,32}$/)
    && record(bot.today) && record(bot.ranked);
}

export function validBody(body) {
  return exact(body, ['schema', 'source', 'updatedAt', 'bots'])
    && body.schema === 2
    && typeof body.source === 'string' && /^[a-z0-9-]{1,32}$/.test(body.source)
    && typeof body.updatedAt === 'string' && !Number.isNaN(Date.parse(body.updatedAt))
    && Array.isArray(body.bots) && body.bots.length <= 8 && body.bots.every(validBot);
}

function authorized(request) {
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
  const body = request.body;
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
