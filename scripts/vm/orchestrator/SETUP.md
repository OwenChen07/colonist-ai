# Setting up the Discord orchestrator

The owner runs these steps once. The bot runs on the GCP coordinator as `colonist-orchestrator.service`, so it keeps going with the laptop off, and you talk to it from the Discord app on your phone.

## 1. Discord (about 5 minutes, on any device)
1. Create a private server, for example "colonist-ai".
2. At https://discord.com/developers/applications: **New Application** → **Bot**.
   - Turn on **Message Content Intent**.
   - **Reset Token** and copy it. Only ever paste it into the env file in step 2.
3. **OAuth2 → URL Generator**: scope `bot`. Permissions: View Channels, Send Messages, Read Message History, Add Reactions, Manage Channels. Open the URL and add the bot to your server.
4. Discord settings → Advanced → **Developer Mode** on. Right-click the server → Copy Server ID. Right-click your own name → Copy User ID.
5. On your phone, set notifications for #orchestrator to "All messages" and for the workstream channels to "Only @mentions", or all of them if you want every worker's progress. The bot creates the channels itself on first start.

## 2. The coordinator
```bash
ssh -i ~/.ssh/google_compute_engine owen@34.60.203.129
cd ~/colonist-ai && git pull --rebase origin main-9mnmbh
.venv/bin/pip install -r scripts/vm/orchestrator/requirements.txt
mkdir -p ~/.config
cp scripts/vm/orchestrator/orchestrator.env.example ~/.config/colonist-orchestrator.env
chmod 600 ~/.config/colonist-orchestrator.env
nano ~/.config/colonist-orchestrator.env   # token, server ID, user ID; budget and worker count
crontab -e                                 # delete the scripts/vm/orchestrator/run.sh line
sudo cp scripts/vm/colonist-orchestrator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now colonist-orchestrator
tail -f data/orchestrator/bot.log
```
`claude` (and `codex`, the fallback) must stay signed in on the coordinator, as they are for the current orchestrator.

## 3. Check it
- In #orchestrator, send `!status`. You get the numbers at once, with no tokens spent.
- `!budget` shows today's spend. `!major` runs a major report now. Send it once to see the whole loop.

## Plan and budget
- **Pro:** keep `ORCHESTRATOR_MAX_WORKERS=1`. Expect Claude's limit to cut in; Codex takes over for an hour each time.
- **Max 5x:** set `ORCHESTRATOR_MAX_WORKERS=3`, or send `!workers 3`, and raise `ORCHESTRATOR_DAILY_BUDGET_USD` (try 60). Then watch `!budget` and the "limit" rows in `data/orchestrator/usage.jsonl` for a few days.
- After changing the env file: `sudo systemctl restart colonist-orchestrator`. Running workers survive the restart.

## Where things are
Everything is under `data/orchestrator/` on the coordinator:
- `reports/`: major reports;
- `journal.md` and `experiments.md`: the lead's notes and the experiments ledger;
- `workstreams/<name>/`: each worker's `brief.md`, `inbox.md`, `progress.md` and run logs;
- `decisions.jsonl` and `answers.jsonl`: the decisions you were asked and your answers;
- `usage.jsonl`: the cost of every run;
- `bot.log`: the bot's own log.

Worker worktrees are in `~/colonist-ai-wt/<name>`. A workstream can be added by naming it in `ORCHESTRATOR_WORKSTREAMS` and writing `workstreams/<name>.md`.
