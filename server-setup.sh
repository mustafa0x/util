#!/bin/bash

set -euxo pipefail

# Allow non-root only for the explicit user phase
if [[ "${1:-}" != "--as-user-phase" ]] && [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive

# ---------- Minimal checkpoints ----------
SCRIPT_PATH=$(readlink -f "$0" 2>/dev/null || echo "$0")
STATE_FILE=/var/tmp/server-setup.stage
STAGE=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
# ----------------------------------------

ARCH_RAW=$(uname -m)
ARCH=$([[ "$ARCH_RAW" == "x86_64" ]] && echo "amd64" || ([[ "$ARCH_RAW" == "aarch64" ]] && echo "arm64" || (echo "Unsupported architecture: $ARCH_RAW" >&2 && return 1)))

#####################################################################
####################### CONFIG #######################
#####################################################################

readonly CONFIG_HOSTNAME=""
readonly CONFIG_USERNAME="web"
PACKAGES="htop btop unzip zip tree git build-essential nnn brotli rename sqlite3 ncdu trash-cli jq tig"  #ffmpeg

#####################################################################
######################################################
#####################################################################

### Helpers ###

USER_HOME=/home/$CONFIG_USERNAME
USER_LOCAL=$USER_HOME/.local
USER_CONF=$USER_HOME/.config

GREEN="\e[32m"
NORMAL="\e[0m"

print_done() {
  echo -e "${GREEN}Done!${NORMAL}"
}

### Setup functions ###

add_user() {
  local username="$1"

  echo -n "-> Adding new user... "

  # Add new user and disable password login
  adduser --disabled-password --quiet --gecos "" "${username}"
  # Add new user to sudo group
  usermod -aG sudo "${username}"
  # Delete the password of the new user
  passwd --quiet --delete "${username}"
  # Disable password prompt for sudo commands
  echo "${username} ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers && visudo -c

  print_done
}

ssh_prep() {
  local username="$CONFIG_USERNAME"

  echo -n "-> SSH"

  # Copy authorized_keys from root, then change owner
  mv /root/.ssh /home/${username}/
  chown -R $username:$username "/home/${username}/.ssh"
  sed -ri 's/^#?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
  sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
  systemctl reload ssh

  print_done
}

install_caddy_server() {
  echo -n "-> Installing Caddy Server... "

  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
  mkdir -p /etc/systemd/system/caddy.service.d/
  cat <<EOF > /etc/systemd/system/caddy.service.d/override.conf
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --environ --config /srv/conf/Caddyfile
ExecReload=
ExecReload=/usr/bin/caddy reload --config /srv/conf/Caddyfile --force
LimitNOFILE=1048576:1048576
StandardOutput=append:/var/log/caddy/caddy.log
StandardError=append:/var/log/caddy/caddy-error.log
EOF
  systemctl daemon-reload
  caddy trust  # to install root store

  print_done
}

install_php() {
  echo -n "-> Installing PHP... "
  apt-get install -y php8.3-fpm php8.3-mbstring php8.3-sqlite3 php8.3-zip php8.3-curl # php8.3-gd php8.3-xml
}

install_docker() {
  curl -fsSL https://get.docker.com | sh
  DOCKER_PLUGINS=/usr/libexec/docker/cli-plugins
  mkdir -p $DOCKER_PLUGINS

  curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH_RAW} -o $DOCKER_PLUGINS/docker-compose
  chmod +x $DOCKER_PLUGINS/docker-compose

  LATEST=$(curl -s https://api.github.com/repos/docker/buildx/releases/latest | jq -r .tag_name)
  curl -sSL https://github.com/docker/buildx/releases/download/${LATEST}/buildx-${LATEST}.linux-${ARCH} -o $DOCKER_PLUGINS/docker-buildx
  chmod +x $DOCKER_PLUGINS/docker-buildx

  usermod -aG docker $CONFIG_USERNAME
}

#####################################################################
#####################################################################
#####################################################################

main() {
  timedatectl set-timezone UTC
  apt-get update
  apt-get -y upgrade
  apt-get install -y --no-install-recommends $PACKAGES

  curl https://mise.run | MISE_INSTALL_PATH=/usr/local/bin/mise sh

  add_user $CONFIG_USERNAME
  ssh_prep

  if [ -n "$CONFIG_HOSTNAME" ]; then
    hostnamectl set-hostname "$CONFIG_HOSTNAME"
  fi

  ufw allow OpenSSH && ufw allow http && ufw allow https && ufw --force enable

  # Create a 2gb empty file on the server to be able to immediately free space if there isn't any
  fallocate -l 2G /var/tmp/EMERGENCY_RESERVE

  ##################
  # Install software
  ##################
  install_caddy_server
  install_docker
  # install_php

  apt-get autoremove -y

  ##################
  # Config
  ##################
  chown -R $CONFIG_USERNAME:$CONFIG_USERNAME /srv
}

##################
# User script
##################
user_script() {
  eval "$(mise activate --status bash)"
  mise settings experimental=true
  mise use -g github:burntsushi/ripgrep github:sharkdp/fd github:starship/starship
  sudo ln -s $(which rg) /usr/local/bin/rg
  sudo ln -s $(which fd) /usr/local/bin/fd

  mkdir -p /srv/{apps,conf} ~/.local/bin ~/.config

  echo -e "{ email nuqayah@gmail.com }\nimport *.caddy" > /srv/conf/Caddyfile
  caddy fmt --overwrite /srv/conf/Caddyfile

  # make conf a git repo, useful to track changes
  cd /srv
  git config --global user.name $USER
  git config --global user.email "$USER@$USER"
  git config --global init.defaultBranch main
  (cd conf; git init && git add . && git commit -m init)

  sudo service caddy restart
  sudo setfacl -m "u:web:r--" "/var/log/caddy"/*.log
  sudo setfacl -d -m "u:web:r--" "/var/log/caddy"
  getfacl -p "/var/log/caddy" | rg "^default:user:web:r--$"
  sudo update-alternatives --set editor /usr/bin/vim.basic
  git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf && ~/.fzf/install --all
  # wget -P ~/.local/ https://raw.githubusercontent.com/mustafa0x/util/main/sqlite_upsert.py
  wget -P ~/.local/ https://raw.githubusercontent.com/mustafa0x/util/main/list_services_hosts.py
  ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519

  cat <<'EOF' >> ~/.bashrc
eval "$(mise activate --status bash)"
alias dc='docker compose'
alias n='nnn -de'
alias ipy=ipython
alias s='sudo systemctl'
alias r='mise run'
if [ "$PWD" == "$HOME" ]; then cd /srv/apps; fi
HISTSIZE=9999999
HISTFILESIZE=9999999
export RIPGREP_CONFIG_PATH=~/.config/.ripgreprc
python ~/.local/list_services_hosts.py
eval "$(starship init bash)"
EOF

  cat <<EOF >> ~/.config/.ripgreprc
--max-columns=150
--max-columns-preview
--smart-case
EOF
  echo -e '[directory]\ntruncation_length = 0\ntruncation_symbol = ""\ntruncate_to_repo=false' >> ~/.config/starship.toml

  mise use -g python uv nodejs@lts pnpm
  # Non-interactive scripts do not reliably trigger hook-env/PROMPT_COMMAND between commands.
  mise x -- python -m pip install ipython regex requests
  mise x -- npm install -g npm

  curl -SsL https://hishtory.dev/install.py | mise x -- python - --offline
  ~/.hishtory/hishtory config-set enable-control-r false
}

########################################
########## Minimal 3-run flow ##########
########################################

# If invoked for user-phase explicitly, run (allowed as non-root) and finish.
if [[ "${1:-}" == "--as-user-phase" ]]; then
  user_script
  sudo rm -f "$STATE_FILE" || true
  echo "All done."
  exit 0
fi

echo "[checkpoint] current stage: $STAGE"

case "$STAGE" in
  0)
    echo "[stage 0] system update/upgrade"
    apt-get update
    apt-get upgrade -y
    echo 1 > "$STATE_FILE"
    echo "[checkpoint] advanced to stage 1 — run this script again as root."
    exit 0
    ;;
  1)
    echo "[stage 1] root phase (main)"
    main
    echo 2 > "$STATE_FILE"
    echo "[checkpoint] advanced to stage 2."
    echo "Now run user-phase as $CONFIG_USERNAME:"
    echo "    sudo -u $CONFIG_USERNAME \"$SCRIPT_PATH\" --as-user-phase"
    exit 0
    ;;
  2)
    echo "[stage 2] awaiting user-phase: run as $CONFIG_USERNAME:"
    echo "    sudo -u $CONFIG_USERNAME \"$SCRIPT_PATH\" --as-user-phase"
    exit 0
    ;;
  *)
    echo "Unknown stage '$STAGE'. Reset with: rm -f $STATE_FILE" >&2
    exit 1
    ;;
esac
