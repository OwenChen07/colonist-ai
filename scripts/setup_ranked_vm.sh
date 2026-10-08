#!/usr/bin/env bash
# Run once on the Ubuntu cloud VM before using ranked_runner.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -e '.[colonist,automation]'
.venv/bin/python -m playwright install --with-deps --no-shell chromium
sudo apt-get update -qq
sudo apt-get install -y xvfb x11vnc novnc websockify

echo 'Cloud browser dependencies are ready.'
