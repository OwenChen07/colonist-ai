#!/usr/bin/env bash
# Lists, or kills, a stuck landing: the processes of land.sh and nothing else.
#   scripts/vm/orchestrator/kill_landing.sh              list them
#   scripts/vm/orchestrator/kill_landing.sh --yes        kill them all
#   scripts/vm/orchestrator/kill_landing.sh --yes PID    kill one (it must be listed) and the landing processes under it
# A landing process is one of the user's own: a running land.sh, or under one
# of those either `flock ... 8` or a `-m pytest` (with its `timeout` wrapper
# and the test's own children). Anything else is refused: the training loop,
# the bots, discord_bot and agent runs are never descendants of land.sh.
set -euo pipefail
yes=0
target=
for arg in "$@"; do
  case "$arg" in
    --yes) yes=1 ;;
    *[!0-9]* | '') echo "usage: $0 [--yes] [PID]"; exit 2 ;;
    *) target=$arg ;;
  esac
done

me=$(id -un)
declare -A parent args
while read -r pid ppid user rest; do
  [ "$user" = "$me" ] || continue
  parent[$pid]=$ppid
  args[$pid]=$rest
done < <(ps -eo pid=,ppid=,user=,args= -ww)

is_land() { # argv0 is land.sh, or a shell running land.sh
  local a
  read -ra a <<<"${args[$1]}"
  [ "$(basename -- "${a[0]}")" = land.sh ] && return 0
  case "$(basename -- "${a[0]}")" in bash | sh) [ "${#a[@]}" -ge 2 ] && [ "$(basename -- "${a[1]}")" = land.sh ] ;; *) return 1 ;; esac
}
under() { # under PID SET: is PID a descendant of a member of SET (a space-joined list)?
  local p=${parent[$1]:-0}
  while [ "$p" -gt 1 ]; do
    case " $2 " in *" $p "*) return 0 ;; esac
    p=${parent[$p]:-0}
  done
  return 1
}

lands=
for pid in "${!args[@]}"; do is_land "$pid" && lands+=" $pid"; done
tests= locks=
for pid in "${!args[@]}"; do
  case " $lands " in *" $pid "*) continue ;; esac
  under "$pid" "$lands" || continue
  a=" ${args[$pid]} "
  case "$a" in
    *" -m pytest "*) tests+=" $pid" ;;
    " flock "*" 8 ") locks+=" $pid" ;;
  esac
done
# The tests' own children (multiprocessing workers and the like).
kids=
for pid in "${!args[@]}"; do
  case " $tests " in *" $pid "*) continue ;; esac
  under "$pid" "$tests" && kids+=" $pid"
done
# Kill order: the tests' children and the tests, the lock, then land.sh.
victims="$kids $tests $locks $lands"

if [ -n "$target" ]; then
  case " $victims " in *" $target "*) ;; *) echo "refused: $target is not a landing process"; exit 1 ;; esac
  # The PID and the landing processes under it, which would be orphaned (and
  # no longer recognised) once it is gone.
  picked=
  for pid in $victims; do
    { [ "$pid" = "$target" ] || under "$pid" "$target"; } && picked+=" $pid"
  done
  victims=$picked
fi
if [ -z "${victims// /}" ]; then echo "no landing processes"; exit 0; fi
for pid in $victims; do
  echo "$pid $(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ') ${args[$pid]}"
done
[ "$yes" = 1 ] || { echo "(listed only; add --yes to kill)"; exit 0; }
for pid in $victims; do kill -TERM "$pid" 2>/dev/null && echo "killed $pid" || echo "$pid already gone"; done
