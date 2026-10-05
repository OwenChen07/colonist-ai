# Shared settings for the azure/ scripts (sourced, not run).
#
# Two resource groups: $GROUP holds the storage account (data and models live
# here between jobs) and $VM_GROUP holds the VMs and their disks, IPs and
# network, so `azure/teardown.sh` can remove every VM without touching data.

LOCATION=${LOCATION:-eastus}  # Azure for Students: see allowed_locations below
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
# (Computed on its own line: macOS's bash 3.2 can't parse it inside ${...}.)
if command -v sha1sum >/dev/null; then
  HASH=$(printf %s "$SUBSCRIPTION" | sha1sum | cut -c1-8)
else
  HASH=$(printf %s "$SUBSCRIPTION" | shasum | cut -c1-8)
fi
ACCOUNT=${STORAGE_ACCOUNT:-colonistai$HASH}

# Fail early, and clearly, if the subscription can't create resources yet (a
# brand-new Azure for Students subscription can take a while to activate), and
# opt the subscription in to the services the scripts use (new subscriptions
# start with them unregistered; registering is a no-op once done).
preflight() {
  if ! az group list -o none 2>/dev/null; then
    echo "Azure can't list resource groups in subscription $SUBSCRIPTION." >&2
    echo "If it was just activated, wait 10-30 minutes, run 'az account clear && az login'," >&2
    echo "and check in the portal (Subscriptions) that it shows as Active." >&2
    exit 1
  fi
  # Azure for Students allows only a few regions, set by a policy on the
  # subscription. Read them from the policy and stop early if LOCATION isn't one.
  allowed=$(allowed_locations)
  if [ -n "$allowed" ] && ! printf '%s\n' $allowed | grep -qx "$LOCATION"; then
    echo "Region $LOCATION isn't allowed on this subscription. Allowed regions:" >&2
    printf '  %s\n' $allowed >&2
    echo "Rerun with one of them, e.g.  LOCATION=$(printf '%s\n' $allowed | head -1) azure/deploy.sh ..." >&2
    exit 1
  fi
  for ns in Microsoft.Storage Microsoft.Compute Microsoft.Network; do
    state=$(az provider show -n "$ns" --query registrationState -o tsv 2>/dev/null)
    if [ "$state" != "Registered" ]; then
      echo "== registering $ns (one-time, can take a few minutes)"
      az provider register -n "$ns" --wait -o none
    fi
  done
}

# The regions allowed by policy on this subscription (empty if none is set).
allowed_locations() {
  az policy assignment list -o json 2>/dev/null | python3 -c '
import json, sys
found = set()
for a in json.load(sys.stdin):
    for name, p in (a.get("parameters") or {}).items():
        v = p.get("value") if isinstance(p, dict) else None
        if "location" in name.lower() and isinstance(v, list):
            found.update(x for x in v if isinstance(x, str))
print(" ".join(sorted(found)))
' 2>/dev/null
}

# Create a resource group unless it exists (a group keeps the region it was
# made in; its resources can be in any region).
ensure_group() {
  if [ "$(az group exists -n "$1")" != "true" ]; then
    az group create -n "$1" -l "$LOCATION" -o none
  fi
}

ensure_storage() {
  preflight
  if ! az storage account show -n "$ACCOUNT" -g "$GROUP" -o none 2>/dev/null; then
    echo "== creating storage account $ACCOUNT in $LOCATION"
    ensure_group "$GROUP"
    az storage account create -n "$ACCOUNT" -g "$GROUP" -l "$LOCATION" \
      --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
      --allow-blob-public-access false -o none
  fi
  az storage container create --account-name "$ACCOUNT" --auth-mode key --only-show-errors \
    -n "$CONTAINER" -o none
  ACCOUNT_ID=$(az storage account show -n "$ACCOUNT" -g "$GROUP" --query id -o tsv)
}
