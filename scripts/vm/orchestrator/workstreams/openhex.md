# Workstream: openhex

**Aim:** get the bot playing on OpenHex (https://open-hex.web.app, a bot-vs-bot 1v1 Catan-style ladder) and climb its 1v1 leaderboard. It is a second yardstick, against other engines instead of people. colonist.io rank 1 stays the project's goal; this workstream must never slow that down.

**Owns:** `catanatron/openhex/` (the OpenHex client, new), `data/openhex/` (bot token, game logs; gitignored), the bot's registration on the site, experiment E20 and everything that follows it in `experiments.md` about OpenHex.

**What the lead found from outside (Oct 17, 6:25 PM), without a browser:**
- `GET /api/health` returns `{"status":"ok"}`. `GET /api/leaderboard/1v1` returns `{"mode":"1v1","entries":[{"bot_id","name","elo","games","wins","rank"}]}`. 18 bots, top Elo about 1700 after 1100 games, names like "2s-NN-Medium-Greedy".
- `GET /api/games` returns 401 (needs a bot token). `GET /api/bots` returns 405, so registration is probably `POST /api/bots`.
- No docs at /api/docs or /openapi.json and nothing in web search. The docs are most likely a page inside the single-page app: curl the index, follow the script tags, and read the JavaScript bundle for the API routes, the game-state and action JSON, how a bot learns it is its turn (polling or websocket) and the move clock.

**First tasks, in order; report after each:**
1. Protocol discovery, read-only. Write up the endpoints, the state and action format, the rules variant (standard 1v1 Catan? what differs from colonist's rules: victory points to win, discard limit, trading, dev cards, robber) and the move clock. If registering needs an email, a password, a captcha or anything the owner must type, stop and raise a decision; do not invent credentials. Registering a bot with just a name through the API is fine: call it "colonist-ai-r66" and keep the token in `data/openhex/`, never in a commit.
2. A thin adapter, `catanatron/openhex/client.py`, in the shape of `catanatron/colonist/bridge.py`: map their state to our engine's state and our action to theirs. Start with round 66's net and the normal live search budget. Where their rules differ from ours, note the gap and play anyway; a rules gap is a finding, not a blocker.
3. Play 20 games on the coordinator with at most 1 core, then report win rate and Elo. Only if that works, propose how many games a day to run, and where.

**Rules for this workstream:**
- No money, no new VMs, nothing taken from the self-play fleet or the live colonist bots. At most 1 core on the coordinator until the lead raises it.
- Never touch the colonist live modules (`bridge.py`, `ranked_runner.py`, the accounts). Your client is a separate module.
- Respect the site: its rate limits, its terms, one bot entry. Do not scrape other bots' games beyond what the public leaderboard shows.
- Every result rests on a count: games played, wins, Elo, confidence interval.

**Writing your updates (owner's rule, Oct 17, 6:09 PM):**
- Every time in a progress note, inbox message, decision or Discord post is Toronto time, written like "6:09 PM" or "Oct 17, 6:09 PM". Never UTC, never "Z".
- Plain, simple language. The owner reads these on a phone. Say what you did, what changed and what is next in short sentences. Keep function names, flags, pids, IPs and commit hashes out of the prose unless the owner has to act on them; put details like that at the end, in a short "Details" line.
