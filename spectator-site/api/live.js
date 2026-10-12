import { KEY, redis } from './_store.js';
import { ranks } from './_rank.js';

// A VM that hasn't posted for this long is shown as offline.
const STALE_MS = 3 * 60 * 1000;

export default async function handler(request, response) {
  if (request.method !== 'GET') {
    response.setHeader('Allow', 'GET');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  response.setHeader('Cache-Control', 'no-store');
  try {
    const db = await redis();
    const sources = Object.values(await db.hGetAll(KEY))
      .map((text) => { try { return JSON.parse(text); } catch { return null; } })
      .filter((s) => s && s.schema === 2);
    const now = Date.now();
    const bots = sources.flatMap((source) => {
      const stale = now - Date.parse(source.updatedAt) > STALE_MS;
      return source.bots.map((bot) => (stale ? { ...bot, status: 'offline', gameId: null } : bot));
    });
    bots.sort((a, b) => a.name.localeCompare(b.name));
    const standing = await ranks(db, bots.map((b) => b.name));
    for (const bot of bots) bot.rank = standing[bot.name] ?? null;
    // Highest Elo first; bots without a rating (placement games) last.
    bots.sort((a, b) => (b.rank?.rating ?? -1) - (a.rank?.rating ?? -1));
    const updated = sources.map((s) => s.updatedAt).sort().pop() || null;
    // Every VM has a copy of the run's state: show the newest.
    const training = sources.map((s) => s.training).filter(Boolean)
      .sort((a, b) => Date.parse(b.since) - Date.parse(a.since))[0] || null;
    const byRound = {};
    for (const row of sources.flatMap((s) => s.netRecords || [])) {
      const total = (byRound[row.round] ??= { round: row.round, wins: 0, losses: 0 });
      total.wins += row.wins;
      total.losses += row.losses;
    }
    const credits = sources.filter((s) => Array.isArray(s.credits))
      .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0]?.credits || null;
    return response.status(200).json({
      schema: 2, updatedAt: updated, bots, training, netRecords: Object.values(byRound), credits,
    });
  } catch (error) {
    console.error('spectator read failed', error?.name || 'unknown');
    return response.status(503).json({ error: 'Status temporarily unavailable' });
  }
}
