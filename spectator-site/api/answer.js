import { randomUUID, timingSafeEqual } from 'node:crypto';
import { ANSWERS_KEY, redis } from './_store.js';

// The owner's reply to the orchestrator, from the site's reply box. Only with
// the reply key (ORCHESTRATOR_REPLY_KEY), which the owner enters once.
function keyMatches(supplied) {
  const secret = process.env.ORCHESTRATOR_REPLY_KEY;
  if (!secret || typeof supplied !== 'string' || !supplied) return false;
  const a = Buffer.from(secret);
  const b = Buffer.from(supplied);
  return a.length === b.length && timingSafeEqual(a, b);
}

export default async function handler(request, response) {
  if (request.method !== 'POST') {
    response.setHeader('Allow', 'POST');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  const { questionId, text, key } = request.body || {};
  if (!keyMatches(key)) return response.status(401).json({ error: 'Wrong reply key' });
  if (typeof questionId !== 'string' || !/^[a-z0-9-]{1,40}$/.test(questionId)
    || typeof text !== 'string' || !text.trim() || text.length > 4000) {
    return response.status(400).json({ error: 'Invalid reply' });
  }
  try {
    const db = await redis();
    const answer = { id: randomUUID(), questionId, text: text.trim(), at: new Date().toISOString() };
    await db.rPush(ANSWERS_KEY, JSON.stringify(answer));
    await db.lTrim(ANSWERS_KEY, -200, -1);
    return response.status(200).json({ ok: true, id: answer.id });
  } catch {
    return response.status(503).json({ error: 'Reply could not be stored' });
  }
}
