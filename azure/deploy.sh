#!/usr/bin/env bash
# Run a long job (data recording, training, self-play) on an Azure VM.
# Run from your laptop after `az login`; see azure/README.md.
#
#   azure/deploy.sh datagen ab2 --games 40000      # -> data/ab2 in blob storage
#   azure/deploy.sh train ab2 --epochs 20          # data/ab2 -> models/ab2
#   MODEL=ab2 azure/deploy.sh selfplay sp1 --games 5000   # models/ab2 -> data/sp1
#
# The VM runs the code of your current commit (uncommitted changes are not
# included), uploads its output to blob storage every few minutes, and
# deallocates itself when the job ends, so it stops costing compute. A rerun
# with the same name resumes from what is already in storage.
#
# Settings (environment variables):
#   LOCATION   Azure region (default eastus; Azure for Students allows only some)
#   VM_SIZE    one size, or a space-separated list to try in order. Default:
#              4-vCPU compute/general sizes, then 2-vCPU ones; the first one
#              Azure has capacity for (on this subscription and region) is used
#   PRIORITY   Regular (default) or Spot (much cheaper; may be evicted, and
#              a rerun resumes)
#   KEEP_VM=1  leave the VM running after the job, for debugging
set -euo pipefail
cd "$(dirname "$0")/.."
source azure/common.sh

JOB=${1:?usage: azure/deploy.sh datagen|train|selfplay NAME [args...]}
NAME=${2:?usage: azure/deploy.sh datagen|train|selfplay NAME [args...]}
shift 2
JOB_ARGS="$*"
case "$JOB" in
  datagen | train) ;;
  selfplay) : "${MODEL:?selfplay needs MODEL=<name of a trained model in storage>}" ;;
  *) echo "unknown job $JOB (datagen, train or selfplay)" >&2; exit 1 ;;
esac

VM_SIZE=${VM_SIZE:-Standard_F4s_v2 Standard_F4as_v6 Standard_D4as_v5 Standard_D4s_v5 Standard_D4as_v6 Standard_D4s_v6 Standard_D4ads_v5 Standard_F2s_v2 Standard_D2as_v5 Standard_D2s_v5}
PRIORITY=${PRIORITY:-Regular}
VM=${VM:-colonist-$JOB-$(date +%m%d-%H%M)}

if ! git diff --quiet HEAD; then
  echo "note: uncommitted changes are not sent to the VM (it runs $(git rev-parse --short HEAD))"
fi

ensure_storage

echo "== quota in $LOCATION (vCPUs used / limit)"
az vm list-usage -l "$LOCATION" \
  --query "[?name.value=='cores' || name.value=='lowPriorityCores'].[localName, currentValue, limit]" \
  -o tsv

echo "== uploading code ($(git rev-parse --short HEAD))"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git archive --format=tar.gz -o "$tmp/code.tgz" HEAD
az storage blob upload --account-name "$ACCOUNT" --auth-mode key -c "$CONTAINER" \
  -n "code/$VM.tgz" -f "$tmp/code.tgz" --overwrite --only-show-errors -o none

# What the VM needs to know, and its first script (it then runs azure/vm-job.sh
# from the uploaded code).
cat >"$tmp/cloud-init.yaml" <<EOF
#cloud-config
package_update: true
packages: [python3-venv, python3-pip, git, curl]
write_files:
  - path: /opt/colonist/job.env
    content: |
      ACCOUNT=$ACCOUNT
      CONTAINER=$CONTAINER
      SUBSCRIPTION=$SUBSCRIPTION
      VM_GROUP=$VM_GROUP
      VM=$VM
      JOB=$JOB
      NAME=$NAME
      MODEL=${MODEL:-}
      JOB_ARGS="$JOB_ARGS"
      KEEP_VM=${KEEP_VM:-}
  - path: /opt/colonist/boot.sh
    permissions: "0755"
    content: |
      #!/bin/bash
      set -uo pipefail
      exec >>/var/log/colonist-job.log 2>&1
      source /opt/colonist/job.env
      export HOME=/root AZCOPY_AUTO_LOGIN_TYPE=MSI
      curl -sSL https://aka.ms/downloadazcopy-v10-linux -o /tmp/azcopy.tgz
      tar xzf /tmp/azcopy.tgz -C /tmp && cp /tmp/azcopy_linux_amd64_*/azcopy /usr/local/bin/
      # The VM's access to storage can take a few minutes to take effect.
      for i in \$(seq 60); do
        azcopy copy "https://\$ACCOUNT.blob.core.windows.net/\$CONTAINER/code/\$VM.tgz" /opt/colonist/code.tgz && break
        sleep 20
      done
      mkdir -p /opt/colonist/repo && tar xzf /opt/colonist/code.tgz -C /opt/colonist/repo
      exec bash /opt/colonist/repo/azure/vm-job.sh
runcmd:
  - [/opt/colonist/boot.sh]
EOF

ensure_group "$VM_GROUP"
spot=()
if [ "$PRIORITY" = "Spot" ]; then
  spot=(--priority Spot --eviction-policy Deallocate --max-price -1)
fi
# Which sizes have capacity varies by subscription and region (Azure for
# Students is restricted), so try the candidates in order.
created=
for size in $VM_SIZE; do
  echo "== creating VM $VM ($size, $PRIORITY) in $LOCATION"
  if az vm create -g "$VM_GROUP" -n "$VM" --image Ubuntu2404 --size "$size" \
    ${spot[@]+"${spot[@]}"} --assign-identity '[system]' --admin-username azureuser \
    --generate-ssh-keys --os-disk-size-gb 64 --custom-data "$tmp/cloud-init.yaml" \
    --tags project=colonist-ai job="$JOB" name="$NAME" --only-show-errors -o none \
    2>"$tmp/err"; then
    created=$size
    break
  fi
  if grep -qE "SkuNotAvailable|NotAvailableForSubscription|QuotaExceeded|exceeding approved|Capacity" "$tmp/err"; then
    echo "   $size isn't available here (capacity, subscription or quota); trying the next size"
  else
    cat "$tmp/err" >&2
    exit 1
  fi
done
if [ -z "$created" ]; then
  echo "None of these sizes could be created in $LOCATION: $VM_SIZE" >&2
  echo "Try another allowed region (LOCATION=...), or list sizes with:" >&2
  echo "  az vm list-skus -l $LOCATION --resource-type virtualMachines --query \"[?restrictions==\\\`[]\\\`].name\" -o tsv" >&2
  exit 1
fi

echo "== granting the VM access to storage and to deallocating itself"
principal=$(az vm show -g "$VM_GROUP" -n "$VM" --query identity.principalId -o tsv)
vm_id=$(az vm show -g "$VM_GROUP" -n "$VM" --query id -o tsv)
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$ACCOUNT_ID" -o none
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
  --role "Virtual Machine Contributor" --scope "$vm_id" -o none

echo
echo "Started. Follow it with:  azure/status.sh"
echo "Fetch results with:       azure/fetch.sh $( [ "$JOB" = train ] && echo models || echo data )/$NAME"
