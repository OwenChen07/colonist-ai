import { get, list } from '@vercel/blob';

// A VM that hasn't posted for this long is shown as offline.
const STALE_MS = 3 * 60 * 1000;

async function readSource(pathname) {
  const result = await get(pathname, { access: 'public', useCache: false });
  if (!result || result.statusCode !== 200 || !result.stream) return null;
  return JSON.parse(await new Response(result.stream).text());
}

export default async function handler(request, response) {
  if (request.method !== 'GET') {
    response.setHeader('Allow', 'GET');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  response.setHeader('Cache-Control', 'no-store');
  try {
    const { blobs } = await list({ prefix: 'spectator/bots/' });
    const sources = (await Promise.all(blobs.map((b) => readSource(b.pathname).catch(() => null))))
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
