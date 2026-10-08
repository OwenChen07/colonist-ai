import { timingSafeEqual } from 'node:crypto';
import { put } from '@vercel/blob';

const FORBIDDEN = /reconnect|token|username|chat|cookie|password|email|hand|secret/i;
const COLORS = new Set(['RED', 'BLUE', 'ORANGE', 'WHITE']);
const RESOURCES = new Set(['WOOD', 'BRICK', 'SHEEP', 'WHEAT', 'ORE', null]);

function exact(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).every((key) => keys.includes(key));
}

function isColor(value) { return value == null || COLORS.has(value); }
function isNumber(value) { return Number.isFinite(value); }
function validFrame(frame) {
  if (!exact(frame, ['gameKey', 'phase', 'observedAt', 'moveNumber', 'turn', 'toMove', 'winner', 'botColor', 'tiles', 'nodes', 'buildings', 'roads', 'players', 'recentMoves'])) return false;
  if (!/^[0-9a-f]{16}$/.test(frame.gameKey || '') || !['live', 'paused', 'finished'].includes(frame.phase)
      || !isNumber(frame.observedAt) || !isNumber(frame.moveNumber) || !isNumber(frame.turn)
      || !isColor(frame.toMove) || !isColor(frame.winner) || !isColor(frame.botColor)) return false;
  return Array.isArray(frame.tiles) && frame.tiles.length <= 19 && frame.tiles.every((tile) =>
    exact(tile, ['q', 'r', 'resource', 'number', 'robber']) && isNumber(tile.q) && isNumber(tile.r)
    && RESOURCES.has(tile.resource) && (tile.number == null || isNumber(tile.number)) && typeof tile.robber === 'boolean')
    && Array.isArray(frame.nodes) && frame.nodes.length <= 54 && frame.nodes.every((node) =>
      exact(node, ['id', 'x', 'y']) && isNumber(node.id) && isNumber(node.x) && isNumber(node.y))
    && Array.isArray(frame.buildings) && frame.buildings.length <= 54 && frame.buildings.every((building) =>
      exact(building, ['node', 'color', 'type']) && isNumber(building.node) && COLORS.has(building.color)
      && ['SETTLEMENT', 'CITY'].includes(String(building.type).toUpperCase()))
    && Array.isArray(frame.roads) && frame.roads.length <= 72 && frame.roads.every((road) =>
      exact(road, ['a', 'b', 'color']) && isNumber(road.a) && isNumber(road.b) && COLORS.has(road.color))
    && Array.isArray(frame.players) && frame.players.length <= 4 && frame.players.every((player) =>
      exact(player, ['color', 'bot', 'points', 'resources', 'developmentCards']) && COLORS.has(player.color)
      && typeof player.bot === 'boolean' && isNumber(player.points) && isNumber(player.resources)
      && isNumber(player.developmentCards))
    && Array.isArray(frame.recentMoves) && frame.recentMoves.length <= 10 && frame.recentMoves.every((move) =>
      exact(move, ['color', 'type']) && COLORS.has(move.color) && /^[A-Z0-9_]{1,64}$/.test(move.type));
}

export function validPublicBody(body) {
  return exact(body, ['schema', 'updatedAt', 'live', 'rewind', 'stats', 'recentGames', 'meta'])
    && body.schema === 1 && !Number.isNaN(Date.parse(body.updatedAt))
    && (body.live == null || validFrame(body.live))
    && Array.isArray(body.rewind) && body.rewind.length <= 55 && body.rewind.every(validFrame)
    && Array.isArray(body.stats) && body.stats.length <= 20 && body.stats.every((row) =>
      exact(row, ['version', 'wins', 'losses']) && isNumber(row.version) && isNumber(row.wins) && isNumber(row.losses))
    && Array.isArray(body.recentGames) && body.recentGames.length <= 16 && body.recentGames.every((game) =>
      exact(game, ['version', 'result', 'at']) && isNumber(game.version)
      && ['win', 'loss'].includes(game.result) && !Number.isNaN(Date.parse(game.at)))
    && exact(body.meta, ['activeVersion', 'verifiedGames'])
    && (body.meta.activeVersion == null || isNumber(body.meta.activeVersion))
    && isNumber(body.meta.verifiedGames);
}

function containsForbiddenKey(value) {
  if (!value || typeof value !== 'object') return false;
  return Object.entries(value).some(
    ([key, item]) => FORBIDDEN.test(key) || containsForbiddenKey(item),
  );
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
  if (!validPublicBody(body) || containsForbiddenKey(body)) {
    return response.status(400).json({ error: 'Invalid public snapshot' });
  }
  const encoded = JSON.stringify(body);
  if (Buffer.byteLength(encoded) > 850_000) {
    return response.status(413).json({ error: 'Snapshot too large' });
  }
  try {
    await put('spectator/live.json', encoded, {
      access: 'public',
      allowOverwrite: true,
      contentType: 'application/json',
    });
    response.setHeader('Cache-Control', 'no-store');
    return response.status(200).json({ ok: true, updatedAt: body.updatedAt });
  } catch (error) {
    console.error('spectator write failed', error?.name || 'unknown');
    return response.status(503).json({ error: 'Snapshot could not be stored' });
  }
}
