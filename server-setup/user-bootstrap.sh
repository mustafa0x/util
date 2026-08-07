#!/usr/bin/env bash

set -euo pipefail

if [[ $(id -un) != web ]]; then
  echo "user bootstrap must run as web" >&2
  exit 1
fi

if [[ ! -x /usr/local/bin/mise ]] || ! cmp -s "$MISE_BOOTSTRAP_BIN" /usr/local/bin/mise; then
  sudo install -m 0755 "$MISE_BOOTSTRAP_BIN" /usr/local/bin/mise
fi

sudo timedatectl set-timezone UTC
if [[ ! -e /var/tmp/EMERGENCY_RESERVE ]]; then
  sudo fallocate -l 2G /var/tmp/EMERGENCY_RESERVE
fi

sudo ln -sfn "$(mise which rg)" /usr/local/bin/rg
sudo ln -sfn "$(mise which fd)" /usr/local/bin/fd
sudo update-alternatives --set editor /usr/bin/vim.basic
sudo caddy trust

git config --global user.name "$USER"
git config --global user.email "$USER@$USER"
git config --global init.defaultBranch main
if [[ ! -d /srv/conf/.git ]]; then
  git -C /srv/conf init
  git -C /srv/conf add Caddyfile
  git -C /srv/conf commit -m init
fi

for log_file in /var/log/caddy/*.log; do
  [[ -e $log_file ]] || continue
  sudo setfacl -m u:web:r-- "$log_file"
done
sudo setfacl -d -m u:web:r-- /var/log/caddy

install -d -m 0755 ~/.local
curl -fsSL https://raw.githubusercontent.com/mustafa0x/util/main/list_services_hosts.py \
  -o ~/.local/list_services_hosts.py
chmod 0755 ~/.local/list_services_hosts.py

if [[ ! -f ~/.ssh/id_ed25519 ]]; then
  ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/id_ed25519
fi

mise x -- python -m pip install ipython regex requests
mise x -- npm install -g npm

if ! hishtory_bin=$(mise which hishtory); then
  mise install --force hishtory
  hishtory_bin=$(mise which hishtory)
fi
hishtory_tmp=$(mktemp /tmp/hishtory-bootstrap.XXXXXXXX)
trap 'rm -f -- "$hishtory_tmp"' EXIT
install -m 0755 "$hishtory_bin" "$hishtory_tmp"
PATH=/usr/local/bin:/usr/bin:/bin "$hishtory_tmp" install --offline
rm -- "$hishtory_tmp"
trap - EXIT
~/.hishtory/hishtory config-set enable-control-r false
