import { KEY, redis } from './_store.js';
import { ranks } from './_rank.js';

// A VM that hasn't posted for this long is shown as offline.
const STALE_MS = 3 * 60 * 1000;
// One that hasn't posted for this long isn't shown at all.
const GONE_MS = 60 * 60 * 1000;

export default async function handler(request, response) {
  if (request.method !== 'GET') {
    response.setHeader('Allow', 'GET');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  response.setHeader('Cache-Control', 'no-store');
  try {
    const db = await redis();
    const now = Date.now();
    // A source silent for an hour is a VM gone or renamed: drop it.
    const sources = Object.values(await db.hGetAll(KEY))
      .map((text) => { try { return JSON.parse(text); } catch { return null; } })
      .filter((s) => s && s.schema === 2 && now - Date.parse(s.updatedAt) <= GONE_MS);
    // A bot posted by several sources (a VM that changed hostname) is shown
    // once, from the source that posted last.
    const latest = new Map();
    for (const source of sources) {
      const at = Date.parse(source.updatedAt);
      const stale = now - at > STALE_MS;
      for (const bot of source.bots) {
        if (latest.has(bot.name) && latest.get(bot.name).at >= at) continue;
        latest.set(bot.name, { at, bot: stale && bot.status !== 'retired' ? { ...bot, status: 'offline', gameId: null } : bot });
      }
    }
    const bots = [...latest.values()].map((entry) => entry.bot);
    bots.sort((a, b) => a.name.localeCompare(b.name));
    const standing = await ranks(db, bots.map((b) => b.name));
    for (const bot of bots) bot.rank = standing[bot.name] ?? null;
    // Active bots before retired ones; highest Elo first; bots without a
    // rating (placement games) last.
    bots.sort((a, b) => ((a.status === 'retired') - (b.status === 'retired'))
      || (b.rank?.rating ?? -1) - (a.rank?.rating ?? -1));
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
    // Credits and the side runs: every VM has the same copy, so the newest.
    const newest = (key) => sources.filter((s) => s[key] && typeof s[key] === 'object')
      .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0]?.[key] || null;
    return response.status(200).json({
      schema: 2,
      updatedAt: updated,
      bots,
      training,
      netRecords: Object.values(byRound),
      credits: newest('credits'),
      peopleModel: newest('peopleModel'),
      e54: newest('e54'),
    });
  } catch (error) {
    console.error('spectator read failed', error?.name || 'unknown');
    return response.status(503).json({ error: 'Status temporarily unavailable' });
  }
}
