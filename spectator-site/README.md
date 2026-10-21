# Colonist AI bots

Public page: https://colonist-ai-spectator.vercel.app/

Shows each bot account: whether it is in a game, its record today (Toronto) and
in ranked, the net it plays, and a **Watch live** link to its current game on
colonist.io (`https://colonist.io/#<game id>`; spectating needs a colonist
login).

`catanatron.colonist.spectator_publish` runs on every bot VM
(`colonist-spectator`). Every 30 seconds it posts that VM's bots to
`/api/ingest`, which accepts only the schema in `api/ingest.js` and stores it in the
`spectator:bots` hash in Redis (Redis Cloud free plan, `REDIS_URL`). `/api/live` merges the VMs and shows a
VM silent for 3 minutes as offline. The page polls it every 15 seconds.

The people model's last 48 hourly refreshes and research's E54 run (iterations,
raw checks against round 73, searched matches) come from
`catanatron.colonist.spectator_extra`, which `scripts/vm/sync_bot.sh` runs on the
coordinator and copies to each bot VM as `data/spectator-extra.json`. A site
that rejects those sections still gets the bots (the publisher resends without them).

The bots shown and their colonist names come from `COLONIST_BOT_NAMES` in each
VM's `scripts/vm/bot/own_accounts.env`. Nothing about opponents, chat, cards or
captures is sent.

## Deployment

The Vercel project is `colonist-ai-spectator`, with a Redis database
(`REDIS_URL`) and `SPECTATOR_INGEST_KEY` in production. Each bot VM holds the same key in
`data/.spectator-ingest-key`. Never commit or print it.

Run `npm install`, `npm run check`, then `vercel --prod --yes` from this folder.
