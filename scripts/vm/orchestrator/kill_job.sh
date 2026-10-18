#!/usr/bin/env bash
# Lists, or kills, a long job: the process in data/orchestrator/jobs/NAME.pid,
# its children (the ssh's driving remote workers and the like) and, if it
# leads its own process group (nohup ... & from a script), that group.
#   scripts/vm/orchestrator/kill_job.sh NAME          list them
#   scripts/vm/orchestrator/kill_job.sh NAME --yes    kill them (TERM, then KILL after 10 s)
# Refused: a name without a pid file, a pid that is not the user's own, a pid
# that started after its pid file was written (the job is gone and the pid was
# reused), and any job that would take down the training loop, the bots, the
# Discord bot, an agent run or a landing.
set -euo pipefail
jobs_dir=${KILL_JOB_DIR:-"$(cd "$(dirname "$0")/../../.." && pwd)/data/orchestrator/jobs"}
name= yes=0
for arg in "$@"; do
  case "$arg" in
    --yes) yes=1 ;;
    *[!A-Za-z0-9._-]* | '' | .*) echo "usage: $0 NAME [--yes]"; exit 2 ;;
    *) name=$arg ;;
  esac
done
[ -n "$name" ] || { echo "usage: $0 NAME [--yes]"; exit 2; }
file=$jobs_dir/$name.pid
[ -f "$file" ] || { echo "refused: no pid file $file"; exit 1; }
target=$(tr -d ' \n' <"$file")
case "$target" in *[!0-9]* | '' | 0 | 1) echo "refused: $file holds no pid"; exit 1 ;; esac

me=$(id -un)
declare -A parent group owner args
while read -r pid ppid pgid user rest; do
  parent[$pid]=$ppid
  group[$pid]=$pgid
  owner[$pid]=$user
  args[$pid]=$rest
done < <(ps -eo pid=,ppid=,pgid=,user=,args= -ww)

[ -n "${args[$target]:-}" ] || { echo "$name: $target is not running"; exit 0; }
[ "${owner[$target]}" = "$me" ] || { echo "refused: $target is not $me's"; exit 1; }
started=$(($(date +%s) - $(ps -o etimes= -p "$target" | tr -d ' ')))
if [ "$started" -gt $(($(stat -c %Y "$file") + 5)) ]; then
  echo "refused: $target started after $file was written (a reused pid)"
  exit 1
fi

under() { # under PID ROOT: is PID a descendant of ROOT?
  local p=${parent[$1]:-0}
  while [ "$p" -gt 1 ]; do
    [ "$p" = "$2" ] && return 0
    p=${parent[$p]:-0}
  done
  return 1
}

victims=
for pid in "${!args[@]}"; do
  [ "$pid" = "$target" ] && continue
  if under "$pid" "$target" || { [ "${group[$target]}" = "$target" ] && [ "${group[$pid]}" = "$target" ]; }; then
    victims+=" $pid"
  fi
done
victims+=" $target" # children first, the job last

for pid in $victims; do
  [ "${owner[$pid]}" = "$me" ] || { echo "refused: $pid (${args[$pid]}) is not $me's"; exit 1; }
  case " ${args[$pid]} " in
    *catanatron.colonist.loop* | *discord_bot* | *agent.py* | *land.sh* | *bridge* | *ranked_runner* | *"claude "*)
      echo "refused: $pid is not a job's (${args[$pid]})"
      exit 1
      ;;
  esac
done
for pid in $victims; do
  echo "$pid $(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ') ${args[$pid]}"
done
[ "$yes" = 1 ] || { echo "(listed only; add --yes to kill)"; exit 0; }
for pid in $victims; do kill -TERM "$pid" 2>/dev/null && echo "killed $pid" || echo "$pid already gone"; done
for _ in $(seq 20); do
  left=
  for pid in $victims; do kill -0 "$pid" 2>/dev/null && left+=" $pid"; done
  [ -z "$left" ] && exit 0
  sleep 0.5
done
for pid in $left; do kill -KILL "$pid" 2>/dev/null && echo "forced $pid"; done
