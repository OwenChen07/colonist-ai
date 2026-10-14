#!/usr/bin/env bash
# Cron calls this hourly, but the full orchestrator runs only around 9:10 am
# and 9:10 pm Toronto time. The cheap watchdog and sync jobs run separately.
# The orchestrator builds the context
# report, then runs a coding agent with ORCHESTRATOR.md (its standing brief)
# to interpret training and live results and research, implement and deploy
# improvements toward rank 1; it reports to the owner on the spectator site
# (data/orchestrator/site.json, carried there by sync_bot.sh) and reads the
# owner's replies from it.
#
# Agent: Claude Code (`claude -p`) by default; Codex (`codex exec`) when Claude
# reports its usage limit, or always with ORCHESTRATOR_AGENT=codex. The owner
# signs either one in; this script never does.
#
# Permissions: a fixed allowlist, as the watchdog has: reading and editing
# this checkout, web research, subagents, git (no force), the tests, the
# project's own modules, ssh with the worker key (the bot VMs, workers and
# the PC), and the safe-restart scripts. Anything else is refused, so the
# brief tells the agent to ask the owner for what it lacks. Codex runs in
# its workspace-write sandbox with network on. One run at a time; a run is
# stopped after 55 minutes.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/../../.."
# Using Toronto's clock here keeps both reports at the same local time across
# daylight saving changes, without relying on cron's timezone support.
if [ "${ORCHESTRATOR_FORCE:-0}" != 1 ]; then
  case "$(TZ=America/Toronto date +%H)" in
    09|21) ;;
    *) exit 0 ;;
  esac
fi
D=data/orchestrator
mkdir -p "$D/runs" "$D/jobs"
touch "$D/journal.md"
[ -e "$D/experiments.md" ] || printf '# Experiments ledger\n\n' > "$D/experiments.md"
exec 9>"$D/.lock"
flock -n 9 || { echo "$(date -u +%FT%TZ) previous run still going; skipped" >> "$D/health.log"; exit 0; }
stamp=$(date -u +%Y%m%d-%H%M)
git pull -q --rebase origin main-9mnmbh 2>/dev/null || true
.venv/bin/python scripts/vm/orchestrator/context.py > "$D/runs/$stamp-context.md" 2>&1
prompt="$(cat scripts/vm/orchestrator/ORCHESTRATOR.md)

$(cat "$D/runs/$stamp-context.md")"
before=$(stat -c %Y "$D/site.json" 2>/dev/null || echo 0)
agent=${ORCHESTRATOR_AGENT:-claude}
log="$D/runs/$stamp-$agent.log"
if [ "$agent" = claude ]; then
  allowed=(
    Read Edit Write Grep Glob WebSearch WebFetch Agent Task TodoWrite
    "Bash(git status:*)" "Bash(git diff:*)" "Bash(git log:*)" "Bash(git show:*)"
    "Bash(git add:*)" "Bash(git commit:*)" "Bash(git pull --rebase:*)"
    "Bash(git push origin main-9mnmbh:*)"
    "Bash(git push origin main-9mnmbh:main)"
    "Bash(.venv/bin/python -m pytest:*)" "Bash(.venv/bin/python -m black:*)"
    # Explicit entry points: directory/package prefixes do not match children.
  )
  for module in loss_report calibrate diagnostics evaluate selfplay train loop \
      human_dataset human_data opponent_model starts; do
    allowed+=("Bash(.venv/bin/python -m catanatron.colonist.$module:*)")
    allowed+=("Bash(nohup .venv/bin/python -m catanatron.colonist.$module:*)")
  done
  for script in scripts/vm/orchestrator/context.py \
      scripts/handoff_distributed_training.py scripts/credits.py; do
    allowed+=("Bash(.venv/bin/python $script:*)")
    allowed+=("Bash(nohup .venv/bin/python $script:*)")
  done
  allowed+=(
    "Bash(ssh -i ~/.ssh/colonist_worker_ed25519:*)"
    "Bash(scp -i ~/.ssh/colonist_worker_ed25519:*)"
    "Bash(rsync:*)"
    "Bash(scripts/vm/orchestrator/safe_restart_training.sh:*)"
    "Bash(scripts/vm/orchestrator/safe_restart_account.sh:*)"
    "Bash(scripts/vm/bot_ctl.sh:*)" "Bash(scripts/vm/sync_bot.sh:*)"
    "Bash(tail:*)" "Bash(head:*)" "Bash(cat:*)" "Bash(ls:*)" "Bash(grep:*)"
    "Bash(wc:*)" "Bash(df:*)" "Bash(free:*)" "Bash(nproc:*)" "Bash(date:*)"
    "Bash(mkdir:*)" "Bash(echo:*)" "Bash(sleep:*)"
    "Bash(systemctl status:*)" "Bash(systemctl is-active:*)" "Bash(journalctl:*)"
  )
  timeout 3300 claude -p "$prompt" --permission-mode dontAsk --allowedTools "${allowed[@]}" \
    > "$log" 2>&1
  code=$?
  if grep -qiE "usage limit|weekly limit|limit reached|rate limit|out of (credits|usage)" "$log" \
     && command -v codex >/dev/null; then
    echo "$(date -u +%FT%TZ) claude hit its limit; running codex" >> "$D/health.log"
    agent=codex
    log="$D/runs/$stamp-codex.log"
  fi
fi
if [ "$agent" = codex ]; then
  if command -v codex >/dev/null; then
    timeout 3300 codex exec --approve-for-me \
      -c sandbox_workspace_write.network_access=true \
      "$prompt" > "$log" 2>&1
    code=$?
  else
    echo "codex is not installed" > "$log"; code=127
  fi
fi
after=$(stat -c %Y "$D/site.json" 2>/dev/null || echo 0)
if [ "$after" != "$before" ] && .venv/bin/python -c "import json,sys;d=json.load(open('$D/site.json'));sys.exit(not isinstance(d.get('summary'),str))"; then
  # Replies listed in this run's context were handled.
  .venv/bin/python scripts/vm/orchestrator/context.py --mark-seen
  echo "$(date -u +%FT%TZ) run finished ($agent, exit $code): $log" >> "$D/health.log"
else
  echo "$(date -u +%FT%TZ) run did not write a report ($agent, exit $code): $log" >> "$D/health.log"
fi
# Keep two weeks of run logs.
find "$D/runs" -type f -mtime +14 -delete
# Carry the report to the bot VMs' publishers now rather than in 5 minutes.
scripts/vm/sync_bot.sh >/dev/null 2>&1 || true
