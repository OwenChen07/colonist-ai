# Colonist AI live table

Public dashboard: https://colonist-ai-spectator.vercel.app/

The ranked bridge writes a checked, redacted board snapshot after moves. The
publisher on the ranked VM reads that snapshot and the runner's verified capture
list, calculates results by model version, and posts a bounded rewind window to
`/api/ingest` about every 40 seconds. Vercel stores only this allowlisted JSON.
The site polls `/api/live` every 15 seconds. During a match, the rewind covers
the 55 most recent positions. After a verified finish, the replay samples up to
55 positions across the whole game, including its opening and final position.

Raw captures stay on the VM. They include details that must never be published.
The public snapshot contains only board layout, public scores, card counts,
move types, bot seat colour, and a hashed game key. The upload endpoint rejects
unknown fields at every level and requires `SPECTATOR_INGEST_KEY`.

The producer currently stops at the ranked monitoring deadline. The last
verified replay and statistics remain on the site after it stops, while the
header reports a delayed feed. To use this site for a later ranked queue, start
the publisher for that queue with a new explicit deadline. Do not run it from a
second Colonist browser session.

## Deployment

The Vercel project is `colonist-ai-spectator`. It has a Blob store linked and
`SPECTATOR_INGEST_KEY` set in its production environment. The VM holds the
same key in a private file at `~/colonist-ai-ranked/data/ranked/.spectator-ingest-key`.
Never commit or print either credential.

Run `npm install`, `npm run check`, then `vercel --prod --yes` from this folder
to deploy changes. The public data contract is checked in `api/ingest.js`; keep
it synchronized with `catanatron.colonist.spectator` and
`catanatron.colonist.spectator_publish`.
