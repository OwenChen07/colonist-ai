#!/usr/bin/env bash
# Before every training round (train.sh's --before-round): start the GCP spot
# self-play VMs (selfplay-spot-*) and the training GPU VM (gpu-train-*) that
# a preemption stopped. They keep their disk, venv, code and internal IP, so
# distributed.py can use them again as soon as they boot; a self-play VM that
# isn't back yet only loses its shards to the other hosts, and a GPU VM that
# isn't back yet makes the round train here. Never fatal: no gcloud, or no
# capacity in the zone, and the round plays on.
#
# A GPU VM whose zone has no capacity (a stockout) is moved to another zone of
# its region (lead's decision, Oct 19, 12:10 PM Toronto): a machine image of
# the stopped VM, a new VM of the same name from it in the next zone, the
# static external IP moved over (the train host is addressed by it), and the
# old VM deleted only once the new one runs. This runs in the background (log
# runs/gcp_gpu_start.log) under a lock, so the round isn't held up.
set -uo pipefail
gcloud=$(command -v gcloud || echo /snap/bin/gcloud)
[ -x "$gcloud" ] || exit 0
GPU_ZONES=${GPU_ZONES:-"us-east1-c us-east1-b us-east1-d"} # T4 zones, in order
GPU_LOG=${GPU_LOG:-runs/gcp_gpu_start.log}
SPOT_LOG=${SPOT_LOG:-runs/gcp_spot_start.log}

stockout() { grep -q "ZONE_RESOURCE_POOL_EXHAUSTED\|does not have enough resources\|stockout" <<<"$1"; }

start_gpu() { # $1 name, $2 zone, $3 configuration; synchronous
    local name=$1 zone=$2 conf=(${3:+--configuration "$3"}) out
    exec 9>/tmp/gcp_gpu_start.lock
    flock -n 9 || { echo "gcp_start_spot: $name is already being started"; return 0; }
    echo "$(date '+%b %d %H:%M:%S') gcp_start_spot: starting $name ($zone${3:+, $3})"
    for try in 1 2; do
        out=$("$gcloud" compute instances start "$name" "${conf[@]}" --zone "$zone" --quiet 2>&1) && {
            echo "started $name in $zone"
            return 0
        }
        echo "$out" | tail -2
        stockout "$out" || return 0
        [ "$try" = 1 ] && sleep "${GPU_RETRY_WAIT:-60}"
    done
    local ip image="$name-move" new
    ip=$("$gcloud" compute instances describe "$name" "${conf[@]}" --zone "$zone" \
        --format="value(networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null)
    "$gcloud" compute machine-images delete "$image" "${conf[@]}" --quiet >/dev/null 2>&1
    "$gcloud" compute machine-images create "$image" "${conf[@]}" --source-instance "$name" \
        --source-instance-zone "$zone" --quiet 2>&1 | tail -1 || return 0
    for new in $GPU_ZONES; do
        [ "$new" = "$zone" ] && continue
        echo "$(date '+%b %d %H:%M:%S') stockout in $zone: moving $name to $new"
        [ -n "$ip" ] && "$gcloud" compute instances delete-access-config "$name" "${conf[@]}" \
            --zone "$zone" --access-config-name external-nat --quiet 2>&1 | tail -1
        if out=$("$gcloud" compute instances create "$name" "${conf[@]}" --zone "$new" \
            --source-machine-image "$image" ${ip:+--address "$ip"} --quiet 2>&1); then
            echo "$name runs in $new${ip:+ at $ip}; deleting the old VM in $zone"
            "$gcloud" compute instances delete "$name" "${conf[@]}" --zone "$zone" --quiet 2>&1 | tail -1
            "$gcloud" compute machine-images delete "$image" "${conf[@]}" --quiet 2>&1 | tail -1
            # The new VM gets new SSH host keys; trust them, or BatchMode ssh fails.
            if [ -n "$ip" ]; then
                sleep "${GPU_KEYSCAN_WAIT:-90}" # the guest agent writes new host keys at boot
                ssh-keygen -R "$ip" -f ~/.ssh/known_hosts >/dev/null 2>&1
                for _ in $(seq 30); do
                    ssh-keyscan -T 10 "$ip" 2>/dev/null >>~/.ssh/known_hosts.new && [ -s ~/.ssh/known_hosts.new ] && break
                    sleep 10
                done
                cat ~/.ssh/known_hosts.new >>~/.ssh/known_hosts 2>/dev/null
                rm -f ~/.ssh/known_hosts.new
            fi
            return 0
        fi
        echo "$out" | tail -2
        [ -n "$ip" ] && "$gcloud" compute instances add-access-config "$name" "${conf[@]}" \
            --zone "$zone" --access-config-name external-nat --address "$ip" --quiet 2>&1 | tail -1
    done
    "$gcloud" compute machine-images delete "$image" "${conf[@]}" --quiet 2>&1 | tail -1
    echo "no zone had capacity for $name; it stays in $zone for the next round"
}

# distributed.py runs this the moment a host drops out, when a preempted VM is
# usually still STOPPING (about a minute), so the TERMINATED filter alone
# missed it and it waited for a later call: 4-17 min down per preemption on
# Oct 20, and over an hour once. A STOPPING VM gets a background waiter that
# starts it as soon as it is TERMINATED. distributed.py names the host that
# dropped out (user@internal-ip): it usually drops a minute or more before GCP
# marks its VM STOPPING, so a RUNNING VM of that IP gets a waiter too ($4), and
# a VM that stays RUNNING (a network blip) is left alone after 5 min.
start_after_stop() { # $1 name, $2 zone, $3 configuration, $4 "running": wait while RUNNING too
    local conf=(${3:+--configuration "$3"}) status
    exec 9>"/tmp/gcp_start_spot-$1.lock"
    flock -n 9 || return 0
    for _ in $(seq "${STOP_POLLS:-30}"); do
        sleep "${STOP_POLL_WAIT:-10}"
        status=$("$gcloud" compute instances describe "$1" "${conf[@]}" --zone "$2" \
            --format="value(status)" 2>/dev/null)
        case $status in
        TERMINATED)
            echo "$(date '+%b %d %H:%M:%S') gcp_start_spot: starting $1 ($2${3:+, $3}) after its stop"
            "$gcloud" compute instances start "$1" "${conf[@]}" --zone "$2" --async --quiet 2>&1 | tail -1
            return 0
            ;;
        STOPPING) ;;
        RUNNING) [ -n "${4:-}" ] || return 0 ;; # running again
        *) return 0 ;;                         # gone
        esac
    done
}

if [ "${1:-}" = --gpu ]; then
    start_gpu "$2" "$3" "${4:-}"
    exit 0
fi
if [ "${1:-}" = --after-stop ]; then
    start_after_stop "$2" "$3" "${4:-}" "${5:-}"
    exit 0
fi
lost_ip=${1:-} lost_ip=${lost_ip#*@} # distributed.py's lost host, or empty for --before-round

start_stopped() { # $1: gcloud configuration ("" for the default one)
    local conf=()
    [ -n "$1" ] && conf=(--configuration "$1")
    "$gcloud" compute instances list "${conf[@]}" \
        --filter="(name~^selfplay-spot- OR name~^gpu-train-) AND (status=TERMINATED OR status=STOPPING)" \
        --format="value(name,zone.basename(),status)" 2>/dev/null |
        while read -r name zone status; do
            if [ "$status" = STOPPING ]; then
                [[ $name == selfplay-spot-* ]] || continue # a GPU VM: the next round's call
                echo "gcp_start_spot: $name ($zone${1:+, $1}) is stopping; starting it once stopped, log $SPOT_LOG"
                setsid "$0" --after-stop "$name" "$zone" "$1" </dev/null >>"$SPOT_LOG" 2>&1 &
                continue
            fi
            if [[ $name == gpu-train-* ]]; then
                echo "gcp_start_spot: starting $name ($zone${1:+, $1}) in the background, log $GPU_LOG"
                setsid "$0" --gpu "$name" "$zone" "$1" </dev/null >>"$GPU_LOG" 2>&1 &
                continue
            fi
            echo "gcp_start_spot: starting $name ($zone${1:+, $1})"
            "$gcloud" compute instances start "$name" "${conf[@]}" --zone "$zone" --async --quiet 2>&1 | tail -1
        done
}
start_stopped ""
if [[ $lost_ip =~ ^[0-9.]+$ ]]; then
    "$gcloud" compute instances list \
        --filter="name~^selfplay-spot- AND status=RUNNING AND networkInterfaces[0].networkIP=$lost_ip" \
        --format="value(name,zone.basename())" 2>/dev/null |
        while read -r name zone; do
            echo "gcp_start_spot: $name ($zone) dropped out while RUNNING; starting it once stopped, log $SPOT_LOG"
            setsid "$0" --after-stop "$name" "$zone" "" running </dev/null >>"$SPOT_LOG" 2>&1 &
        done
fi
# E42's GPU VM lives in the owner's trial project (configuration trial2,
# static external IP gpu-train-1-ip, so the train host's address survives).
"$gcloud" config configurations describe trial2 >/dev/null 2>&1 && start_stopped trial2
exit 0
