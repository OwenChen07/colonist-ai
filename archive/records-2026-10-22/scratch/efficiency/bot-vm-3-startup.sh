#!/bin/bash
# bot-vm-3 first boot (E57): packages, then, once sync_bot.sh has copied the
# code to ~ubuntu/colonist-ai, setup_ranked_vm.sh and the bot's unit files.
# Runs as root on every boot; does its work once.
DONE=/var/lib/bot-setup-done
[ -e "$DONE" ] && exit 0
exec > >(tee -a /var/log/bot-setup.log) 2>&1
echo "bot-setup: start $(date -u)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3-venv python3-pip git rsync
id ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo ubuntu
R=/home/ubuntu/colonist-ai
echo "bot-setup: packages done, waiting for $R/pyproject.toml"
for i in $(seq 1 720); do
  [ -e "$R/pyproject.toml" ] && [ -e "$R/scripts/setup_ranked_vm.sh" ] && break
  sleep 15
done
[ -e "$R/pyproject.toml" ] || { echo "bot-setup: FAILED no code after 3 h"; exit 1; }
sleep 60  # let the rest of the first sync land
echo "bot-setup: running setup_ranked_vm.sh $(date -u)"
if sudo -u ubuntu -H bash "$R/scripts/setup_ranked_vm.sh"; then
  cp "$R/scripts/vm/bot/colonist-casual@.service" "$R/scripts/vm/bot/colonist-spectator.service" /etc/systemd/system/
  systemctl daemon-reload
  touch "$DONE"
  echo "bot-setup: DONE $(date -u)"
else
  echo "bot-setup: FAILED setup_ranked_vm.sh exit $? $(date -u)"
fi
