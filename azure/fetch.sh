#!/usr/bin/env bash
# Download a folder from storage into this checkout, and/or upload one.
#   azure/fetch.sh models/ab2         storage -> ./models/ab2
#   azure/fetch.sh --up data/ab2      ./data/ab2 -> storage (e.g. laptop data, to train on Azure)
set -euo pipefail
cd "$(dirname "$0")/.."
source azure/common.sh
if [ "${1:-}" = "--up" ]; then
  DIR=${2:?usage: azure/fetch.sh --up DIR}
  ensure_storage
  az storage blob upload-batch --account-name "$ACCOUNT" --auth-mode key --only-show-errors \
    -d "$CONTAINER" --destination-path "$DIR" -s "$DIR" --overwrite -o none
  echo "uploaded $DIR"
else
  DIR=${1:?usage: azure/fetch.sh DIR}
  mkdir -p "$DIR"
  az storage blob download-batch --account-name "$ACCOUNT" --auth-mode key --only-show-errors \
    -s "$CONTAINER" --pattern "$DIR/*" -d . -o none
  echo "downloaded $DIR ($(ls "$DIR" | wc -l) files)"
fi
