#!/usr/bin/env bash
# Stop paying for Azure resources.
#   azure/teardown.sh        delete every VM (with its disk, IP and network); keep data
#   azure/teardown.sh --all  also delete the storage account and everything in it
set -euo pipefail
cd "$(dirname "$0")/.."
source azure/common.sh
echo "deleting resource group $VM_GROUP (all VMs)"
az group delete -n "$VM_GROUP" --yes -o none 2>/dev/null || echo "(no VMs)"
if [ "${1:-}" = "--all" ]; then
  read -r -p "Delete storage account $ACCOUNT and all data and models in it? [y/N] " ok
  [ "$ok" = "y" ] && az group delete -n "$GROUP" --yes -o none && echo "deleted $GROUP"
fi
