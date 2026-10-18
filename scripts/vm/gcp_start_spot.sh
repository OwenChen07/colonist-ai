#!/usr/bin/env bash
# Before every training round (train.sh's --before-round): start the GCP spot
# self-play VMs (selfplay-spot-*) and the training GPU VM (gpu-train-*) that
# a preemption stopped. They keep their disk, venv, code and internal IP, so
# distributed.py can use them again as soon as they boot; a self-play VM that
# isn't back yet only loses its shards to the other hosts, and a GPU VM that
# isn't back yet makes the round train here. Never fatal: no gcloud, or no
# capacity in the zone, and the round plays on.
set -uo pipefail
gcloud=$(command -v gcloud || echo /snap/bin/gcloud)
[ -x "$gcloud" ] || exit 0
start_stopped() { # $1: gcloud configuration ("" for the default one)
    local conf=()
    [ -n "$1" ] && conf=(--configuration "$1")
    "$gcloud" compute instances list "${conf[@]}" \
        --filter="(name~^selfplay-spot- OR name~^gpu-train-) AND status=TERMINATED" \
        --format="value(name,zone.basename())" 2>/dev/null |
        while read -r name zone; do
            echo "gcp_start_spot: starting $name ($zone${1:+, $1})"
            "$gcloud" compute instances start "$name" "${conf[@]}" --zone "$zone" --async --quiet 2>&1 | tail -1
        done
}
start_stopped ""
# E42's GPU VM lives in the owner's trial project (configuration trial2,
# static external IP gpu-train-1-ip, so the train host's address survives).
"$gcloud" config configurations describe trial2 >/dev/null 2>&1 && start_stopped trial2
exit 0
