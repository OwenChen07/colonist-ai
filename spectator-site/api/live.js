import { KEY, redis } from './_store.js';

// A VM that hasn't posted for this long is shown as offline.
const STALE_MS = 3 * 60 * 1000;

export default async function handler(request, response) {
  if (request.method !== 'GET') {
    response.setHeader('Allow', 'GET');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  response.setHeader('Cache-Control', 'no-store');
  try {
    const sources = Object.values(await (await redis()).hGetAll(KEY))
      .map((text) => { try { return JSON.parse(text); } catch { return null; } })
      .filter((s) => s && s.schema === 2);
    const now = Date.now();
    const bots = sources.flatMap((source) => {
      const stale = now - Date.parse(source.updatedAt) > STALE_MS;
      return source.bots.map((bot) => (stale ? { ...bot, status: 'offline', gameId: null } : bot));
    });
    bots.sort((a, b) => a.name.localeCompare(b.name));
    const updated = sources.map((s) => s.updatedAt).sort().pop() || null;
    return response.status(200).json({ schema: 2, updatedAt: updated, bots });
  } catch (error) {
    console.error('spectator read failed', error?.name || 'unknown');
    return response.status(503).json({ error: 'Status temporarily unavailable' });
  }
}
