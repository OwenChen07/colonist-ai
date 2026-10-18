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
"$gcloud" compute instances list \
    --filter="(name~^selfplay-spot- OR name~^gpu-train-) AND status=TERMINATED" \
    --format="value(name,zone.basename())" 2>/dev/null |
    while read -r name zone; do
        echo "gcp_start_spot: starting $name ($zone)"
        "$gcloud" compute instances start "$name" --zone "$zone" --async --quiet 2>&1 | tail -1
    done
exit 0
