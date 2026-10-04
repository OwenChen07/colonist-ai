#!/usr/bin/env bash
# What's running, and the end of each VM's log.
#   azure/status.sh          all VMs, last 5 log lines each
#   azure/status.sh 40       more lines
set -euo pipefail
cd "$(dirname "$0")/.."
source azure/common.sh
LINES=${1:-5}

echo "== VMs"
az vm list -g "$VM_GROUP" -d \
  --query "[].[name, powerState, hardwareProfile.vmSize, tags.job, tags.name]" -o tsv 2>/dev/null ||
  echo "(none)"
echo "== logs"
for blob in $(az storage blob list --account-name "$ACCOUNT" --auth-mode key -c "$CONTAINER" \
  --prefix logs/ --query "[].name" -o tsv 2>/dev/null); do
  echo "-- $blob"
  az storage blob download --account-name "$ACCOUNT" --auth-mode key -c "$CONTAINER" \
    -n "$blob" -f /dev/stdout -o none 2>/dev/null | tail -n "$LINES"
done
