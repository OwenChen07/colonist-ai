# Shared settings for the azure/ scripts (sourced, not run).
#
# Two resource groups: $GROUP holds the storage account (data and models live
# here between jobs) and $VM_GROUP holds the VMs and their disks, IPs and
# network, so `azure/teardown.sh` can remove every VM without touching data.

LOCATION=${LOCATION:-eastus}
GROUP=${GROUP:-colonist-ai}
VM_GROUP=${VM_GROUP:-colonist-ai-vms}
CONTAINER=colonist

if ! command -v az >/dev/null; then
  echo "needs the Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  exit 1
fi
SUBSCRIPTION=$(az account show --query id -o tsv) || {
  echo "run 'az login' first" >&2
  exit 1
}
# A storage account name is global: derive a stable one from the subscription.
ACCOUNT=${STORAGE_ACCOUNT:-colonistai$(printf %s "$SUBSCRIPTION" | { sha1sum 2>/dev/null || shasum; } | cut -c1-8)}

ensure_storage() {
  if ! az storage account show -n "$ACCOUNT" -g "$GROUP" -o none 2>/dev/null; then
    echo "== creating storage account $ACCOUNT in $LOCATION"
    az group create -n "$GROUP" -l "$LOCATION" -o none
    az storage account create -n "$ACCOUNT" -g "$GROUP" -l "$LOCATION" \
      --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
      --allow-blob-public-access false -o none
  fi
  az storage container create --account-name "$ACCOUNT" --auth-mode key \
    -n "$CONTAINER" -o none
  ACCOUNT_ID=$(az storage account show -n "$ACCOUNT" -g "$GROUP" --query id -o tsv)
}
