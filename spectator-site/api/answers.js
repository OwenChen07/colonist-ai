import { authorized } from './ingest.js';
import { ANSWERS_KEY, redis } from './_store.js';

// The owner's replies, for the bot VMs' publishers (same key as ingest), which
// pass them to the orchestrator.
export default async function handler(request, response) {
  if (request.method !== 'GET') {
    response.setHeader('Allow', 'GET');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  if (!authorized(request)) return response.status(401).json({ error: 'Unauthorized' });
  response.setHeader('Cache-Control', 'no-store');
  try {
    const rows = await (await redis()).lRange(ANSWERS_KEY, 0, -1);
    const answers = rows.map((r) => { try { return JSON.parse(r); } catch { return null; } }).filter(Boolean);
    return response.status(200).json({ answers });
  } catch {
    return response.status(503).json({ error: 'Replies unavailable' });
  }
}
