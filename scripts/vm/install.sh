#!/usr/bin/env bash
# Install the 24/7 services on the coordinator VM (run once, from the checkout
# at /home/owen/colonist-ai, as owen with sudo). They start at boot:
#   colonist-ranked     ranked 1v1 games with the promoted net
#   colonist-train      the self-play training loop
#   colonist-spectator  the redacted public feed
# and cron runs scripts/vm/watchdog.sh every hour.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p data/ranked data/watchdog models/ranked captures/ranked runs
for unit in colonist-ranked colonist-train colonist-spectator; do
  sudo install -m 644 "scripts/vm/$unit.service" /etc/systemd/system/
done
sudo systemctl daemon-reload
sudo systemctl enable colonist-ranked colonist-train colonist-spectator
line="17 * * * * $PWD/scripts/vm/watchdog.sh"
( crontab -l 2>/dev/null | grep -v scripts/vm/watchdog.sh; echo "$line" ) | crontab -
echo "Installed. Start with: sudo systemctl start colonist-train colonist-ranked colonist-spectator"
