// Each bot's 1v1 standing from its public colonist profile, cached in Redis
// for 5 minutes so the site asks colonist at most once per bot per 5 minutes.
const TIERS = ['Grandmaster', 'Master', 'Diamond', 'Platinum', 'Gold', 'Silver', 'Bronze'];
const SUBS = ['', 'I', 'II', 'III', 'IV', 'V'];
const TTL = 300;
const HEADERS = { 'User-Agent': 'colonist-ai-status/1.0', Accept: 'application/json' };

async function colonist(path) {
  const response = await fetch(`https://colonist.io/api/${path}`, { headers: HEADERS, signal: AbortSignal.timeout(4000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function number(text) {
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

async function season(redis) {
  const cached = await redis.get('spectator:season');
  if (cached) return Number(cached);
  const seasons = await colonist('profile/ranked-seasons');
  const id = Math.max(...seasons.map((s) => s.seasonType).filter(Number.isInteger));
  await redis.set('spectator:season', String(id), { EX: 6 * 3600 });
  return id;
}

async function fetchRank(name, seasonId) {
  const modes = await colonist(`profile/${encodeURIComponent(name)}/ranked/${seasonId}`);
  const duel = modes.find((m) => m.modeType === 1);
  if (!duel) return null;
  const placed = duel.division >= 0 && duel.division < TIERS.length;
  const state = duel.currentRankedState || {};
  return {
    tier: placed ? [TIERS[duel.division], SUBS[duel.subdivision] || ''].join(' ').trim() : null,
    rating: number(duel.skillRating),
    games: number(duel.gamesPlayed),
    winRate: number(duel.winRate),
    globalRank: number(duel.rankedStanding?.[0]?.rank),
    placement: placed ? null : {
      played: number(state.numberOfPlacementGamesFinished) ?? 0,
      required: number(state.numberOfPlacementGamesRequired) ?? 10,
    },
  };
}

export async function ranks(redis, names) {
  let seasonId;
  const out = {};
  await Promise.all(names.map(async (name) => {
    const key = `spectator:rank:${name}`;
    const cached = await redis.get(key);
    if (cached) { out[name] = JSON.parse(cached); return; }
    try {
      seasonId ??= await season(redis);
      out[name] = await fetchRank(name, seasonId);
      await redis.set(key, JSON.stringify(out[name]), { EX: TTL });
    } catch {
      out[name] = null; // colonist unreachable: show the card without it
    }
  }));
  return out;
}
