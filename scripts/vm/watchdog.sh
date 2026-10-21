#!/usr/bin/env bash
# Every 15 minutes (cron): a cheap health check of the bots and training loop.
# Only when something looks wrong does it start an agent to diagnose and fix it.
# Claude Code uses the fixed allowlist below; Codex is the fallback when Claude
# reaches its usage limit. One run at a time.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/../.."
mkdir -p data/watchdog
exec 9>data/watchdog/.lock
flock -n 9 || { echo "$(date -u +%FT%TZ) previous watchdog still running" >> data/watchdog/health.log; exit 0; }

problems=()
age() { echo $(( $(date +%s) - $(stat -c %Y "$1" 2>/dev/null || echo 0) )); }

# A problem the watchdog handed to the owner (data/<account>/NEEDS_OWNER.md)
# only silences that account; everything else is still checked.
waiting() { [ -e "data/$1/NEEDS_OWNER.md" ]; }
# Accounts the owner chose not to run (bot10, Oct 20): "inactive" is expected,
# so only that check is skipped; failed, needs_attention etc. still count.
not_started="bot10"
expected_inactive() { [ "$2" = inactive ] && [[ " $not_started " == *" $1 "* ]]; }
systemctl is-active --quiet colonist-train || problems+=("colonist-train is not active ($(systemctl is-active colonist-train))")
# The ranked bot runs on its own VM; its data comes back every 5 minutes.
bot=$(scripts/vm/bot_ctl.sh is-active 2>&1)
waiting ranked || [ "$bot" = active ] || problems+=("colonist-ranked on the bot VM is not active ($bot)")
# The extra accounts (colonist-casual@NAME): each line is "NAME service state".
while read -r name service state; do
  [ -n "$name" ] || continue
  waiting "$name" && continue
  # "activating" is also the few minutes between an account's browser batches
  # (it restarts after every 50 games): a problem only if two checks see it.
  if [ "$service" = activating ]; then
    [ -e "data/watchdog/.activating-$name" ] && problems+=("bot account $name: service activating for 15+ minutes")
    touch "data/watchdog/.activating-$name"
  else
    rm -f "data/watchdog/.activating-$name"
    [ "$service" = active ] || expected_inactive "$name" "$service" || problems+=("bot account $name: service $service")
  fi
  [ "$state" = needs_attention ] && problems+=("bot account $name: runner needs_attention")
done < <(scripts/vm/bot_ctl.sh accounts 2>/dev/null)
# A crash loop (e.g. running out of memory) can look active between restarts:
# compare each service's restart counter with the last check's.
for unit in colonist-ranked colonist-train; do
  if [ "$unit" = colonist-ranked ]; then
    waiting ranked && continue
    now=$(scripts/vm/bot_ctl.sh restarts 2>/dev/null || echo 0)
  else
    now=$(systemctl show -p NRestarts --value "$unit" 2>/dev/null || echo 0)
  fi
  before=$(cat "data/watchdog/.restarts-$unit" 2>/dev/null || echo "$now")
  echo "$now" > "data/watchdog/.restarts-$unit"
  [ $(( now - before )) -ge 3 ] && problems+=("$unit restarted $(( now - before )) times since the last check ($(systemctl show -p Result --value "$unit"))")
done
state=$(python3 -c 'import json;print(json.load(open("data/ranked/runner-status.json")).get("state",""))' 2>/dev/null || echo missing)
if ! waiting ranked; then
  [ "$state" = needs_attention ] && problems+=("ranked runner status is needs_attention")
  [ "$(age data/ranked/bridge.log)" -gt 2700 ] && problems+=("bridge.log has not changed for $(( $(age data/ranked/bridge.log) / 60 )) minutes")
  [ "$(age data/ranked/games.jsonl)" -gt 10800 ] && problems+=("no ranked game verified for $(( $(age data/ranked/games.jsonl) / 60 )) minutes")
fi
[ "$(age runs/train.log)" -gt 10800 ] && problems+=("training log has not changed for $(( $(age runs/train.log) / 60 )) minutes")
used=$(df --output=pcent / | tail -1 | tr -dc 0-9)
[ "$used" -gt 90 ] && problems+=("disk is ${used}% full")

if [ ${#problems[@]} -eq 0 ]; then
  echo "$(date -u +%FT%TZ) ok" >> data/watchdog/health.log
  exit 0
fi
stamp=$(date -u +%Y%m%d-%H%M%S)
printf '%s problems: %s\n' "$(date -u +%FT%TZ)" "$(IFS='; '; echo "${problems[*]}")" >> data/watchdog/health.log
prompt="$(cat scripts/vm/watchdog.md)

## This run ($(date -u +%FT%TZ))

The health check found:
$(printf -- '- %s\n' "${problems[@]}")"
allowed=(
  Read Edit Write Grep Glob
  "Bash(git status:*)" "Bash(git diff:*)" "Bash(git log:*)" "Bash(git add:*)"
  "Bash(git commit:*)" "Bash(git pull --rebase:*)" "Bash(git push origin main-9mnmbh:*)"
  "Bash(.venv/bin/python -m pytest:*)" "Bash(.venv/bin/python -m black:*)"
  "Bash(.venv/bin/python -m catanatron.colonist.convert:*)"
  "Bash(.venv/bin/python -m catanatron.colonist.replay:*)"
  "Bash(.venv/bin/python -m catanatron.colonist.capture:*)"
  "Bash(sleep:*)" "Bash(tail:*)" "Bash(head:*)" "Bash(ls:*)" "Bash(grep:*)" "Bash(wc:*)" "Bash(df:*)"
  "Bash(systemctl status:*)" "Bash(journalctl:*)"
  "Bash(sudo systemctl restart colonist-train)"
  "Bash(scripts/vm/bot_ctl.sh status)" "Bash(scripts/vm/bot_ctl.sh log)"
  "Bash(scripts/vm/bot_ctl.sh restart)" "Bash(scripts/vm/bot_ctl.sh stop)"
  "Bash(scripts/vm/bot_ctl.sh accounts)" "Bash(scripts/vm/bot_ctl.sh log-account:*)"
  "Bash(scripts/vm/bot_ctl.sh restart-account:*)" "Bash(scripts/vm/bot_ctl.sh stop-account:*)"
)
agent=claude
log="data/watchdog/run-$stamp-claude.log"
timeout 3000 claude -p "$prompt" --permission-mode dontAsk --allowedTools "${allowed[@]}" \
  > "$log" 2>&1
code=$?
if grep -qiE "usage limit|weekly limit|limit reached|rate limit|out of (credits|usage)" "$log" \
   && command -v codex >/dev/null; then
  echo "$(date -u +%FT%TZ) claude hit its limit; running codex" >> data/watchdog/health.log
  agent=codex
  log="data/watchdog/run-$stamp-codex.log"
  timeout 3000 codex exec --approve-for-me \
    -c sandbox_workspace_write.network_access=true \
    "$prompt" > "$log" 2>&1
  code=$?
fi
echo "$(date -u +%FT%TZ) watchdog run finished ($agent, exit $code): $log" >> data/watchdog/health.log
