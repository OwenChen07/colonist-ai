#!/bin/bash
# Runs on the VM (started by azure/deploy.sh; output in /var/log/colonist-job.log,
# copied to logs/<vm>.log in storage). Sets up, pulls what the job needs from
# storage, runs it while uploading its output every few minutes, uploads the
# rest, then deallocates the VM.
set -uo pipefail
source /opt/colonist/job.env
export HOME=/root AZCOPY_AUTO_LOGIN_TYPE=MSI
cd /opt/colonist/repo
URL="https://$ACCOUNT.blob.core.windows.net/$CONTAINER"
LOG=/var/log/colonist-job.log
read -r -a ARGS <<<"$JOB_ARGS"

upload_log() { azcopy copy "$LOG" "$URL/logs/$VM.log" --log-level ERROR >/dev/null || true; }
# copy, not sync: sync refuses a folder that doesn't exist yet in storage.
# --overwrite=ifSourceNewer skips files already there, so repeats are cheap.
pull() {
  mkdir -p "$1"
  azcopy copy "$URL/$1" "$(dirname "$1")/" --recursive --overwrite=ifSourceNewer \
    --log-level ERROR >/dev/null 2>&1 || echo "(nothing in storage yet at $1)"
}
push() {
  [ -d "$1" ] || return 0
  azcopy copy "$1" "$URL/$(dirname "$1")/" --recursive --overwrite=ifSourceNewer \
    --log-level ERROR | tail -3 || true
}

deallocate() {
  upload_log
  if [ -n "${KEEP_VM:-}" ]; then
    echo "KEEP_VM set: leaving the VM running"
    return
  fi
  local token
  token=$(curl -s -H Metadata:true \
    "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')
  curl -s -X POST -H "Authorization: Bearer $token" -H "Content-Length: 0" \
    "https://management.azure.com/subscriptions/$SUBSCRIPTION/resourceGroups/$VM_GROUP/providers/Microsoft.Compute/virtualMachines/$VM/deallocate?api-version=2024-07-01"
}
trap deallocate EXIT

echo "== $(date -u) $JOB $NAME ${JOB_ARGS} on $(nproc) cores"
upload_log
# Setup takes several minutes; keep the log in storage current meanwhile.
(while sleep 60; do upload_log; done) &
setup_logger=$!
echo "== setting up (installing packages, running tests)"
scripts/setup.sh || { kill "$setup_logger"; echo "setup failed"; exit 1; }
kill "$setup_logger" 2>/dev/null
PY=.venv/bin/python

case "$JOB" in
  datagen)
    OUT=data/$NAME
    CMD=($PY -m catanatron.colonist.datagen "$OUT" "${ARGS[@]}")
    ;;
  train)
    pull "data/$NAME"
    OUT=models/$NAME
    CMD=($PY -m catanatron.colonist.train "data/$NAME" --out "$OUT" "${ARGS[@]}")
    ;;
  loop)
    # A run directory holds its own state; a rerun continues it. --init is
    # only read when the run is new.
    pull "models/$MODEL"
    for ((i = 0; i < ${#ARGS[@]}; i++)); do  # data folders named by --extra-data
      if [ "${ARGS[$i]}" = --extra-data ]; then
        IFS=, read -r -a extra <<<"${ARGS[$((i + 1))]}"
        for d in "${extra[@]}"; do pull "$d"; done
      fi
    done
    OUT=runs/$NAME
    CMD=($PY -m catanatron.colonist.loop "$OUT" --init "models/$MODEL/net.npz" "${ARGS[@]}")
    ;;
  selfplay)
    pull "models/$MODEL"
    OUT=data/$NAME
    CMD=($PY -m catanatron.colonist.selfplay "models/$MODEL/net.npz" "$OUT" "${ARGS[@]}")
    ;;
esac
pull "$OUT"  # resume from an earlier run with the same name

echo "== running: ${CMD[*]}"
"${CMD[@]}" &
pid=$!
while kill -0 "$pid" 2>/dev/null; do
  for _ in $(seq 30); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 10
  done
  push "$OUT"
  upload_log
done
wait "$pid"
status=$?
push "$OUT"
echo "== $(date -u) finished with status $status"
exit "$status"
