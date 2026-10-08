import { get } from '@vercel/blob';

export default async function handler(request, response) {
  if (request.method !== 'GET') {
    response.setHeader('Allow', 'GET');
    return response.status(405).json({ error: 'Method not allowed' });
  }
  response.setHeader('Cache-Control', 'no-store');
  try {
    const result = await get('spectator/live.json', {
      access: 'public',
      useCache: false,
    });
    if (!result || result.statusCode !== 200 || !result.stream) {
      return response.status(200).json({ schema: 1, state: 'waiting' });
    }
    const text = await new Response(result.stream).text();
    response.setHeader('Content-Type', 'application/json; charset=utf-8');
    return response.status(200).send(text);
  } catch (error) {
    if (error?.name === 'BlobNotFoundError') {
      return response.status(200).json({ schema: 1, state: 'waiting' });
    }
    console.error('spectator read failed', error?.name || 'unknown');
    return response.status(503).json({ error: 'Live feed temporarily unavailable' });
  }
}
