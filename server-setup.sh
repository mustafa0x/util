#!/bin/bash

set -euxo pipefail

[ "$EUID" -ne 0 ] && echo "Please run as root" && exit 1
export DEBIAN_FRONTEND=noninteractive

ARCH_RAW=$(uname -m)
ARCH=$([[ "$ARCH_RAW" == "x86_64" ]] && echo "amd64" || ([[ "$ARCH_RAW" == "aarch64" ]] && echo "arm64" || (echo "Unsupported architecture: $ARCH_RAW" >&2 && return 1)))

#####################################################################
####################### CONFIG #######################
#####################################################################

readonly CONFIG_HOSTNAME=""
readonly CONFIG_USERNAME="web"
PACKAGES="htop unzip zip tree git build-essential nnn brotli fd-find ripgrep rename sqlite3 ncdu trash-cli jq ffmpeg"

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
  systemctl -q restart ssh

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
  service caddy restart

  print_done
}

install_pandoc() {
  VER=$(curl -s https://api.github.com/repos/jgm/pandoc/releases | jq -r '.[0].tag_name')
  curl -SsL https://github.com/jgm/pandoc/releases/download/${VER}/pandoc-${VER}-1-${ARCH}.deb -o pandoc.deb
  dpkg -i pandoc.deb
  rm pandoc.deb
}

install_php() {
  echo -n "-> Installing PHP... "
  apt-get install -y php8.3-fpm php8.3-mbstring php8.3-sqlite3 php8.3-zip php8.3-curl # php8.3-gd php8.3-xml
}

install_mise() {
  echo -n "-> Installing mise..."
  curl https://mise.jdx.dev/mise-latest-linux-${ARCH} > /usr/local/bin/mise
  chmod +x /usr/local/bin/mise
  print_done
}

install_docker() {
  curl -fsSL https://get.docker.com -o get-docker.sh && sh ./get-docker.sh
  DOCKER_PLUGINS=/usr/libexec/docker/cli-plugins
  mkdir -p $DOCKER_PLUGINS
  curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH_RAW} -o $DOCKER_PLUGINS/docker-compose
  chmod +x $DOCKER_PLUGINS/docker-compose
  usermod -aG docker $CONFIG_USERNAME
}

#####################################################################
#####################################################################
#####################################################################

main() {
  apt-get update
  apt-get -y upgrade
  apt-get install -y $PACKAGES

  add_user $CONFIG_USERNAME
  ssh_prep

  ufw allow OpenSSH && ufw allow http && ufw allow https && ufw --force enable

  # Create a 2gb empty file on the server to be able to immediately free space if there isn't any
  fallocate -l 2G /var/tmp/EMERGENCY_RESERVE

  ##################
  # Install software
  ##################
  install_caddy_server
  install_docker
  install_mise
  install_php

  apt-get autoremove -y

  ##################
  # Config
  ##################
  chown -R $CONFIG_USERNAME:$CONFIG_USERNAME /srv
}

user_script() {
  eval "$(mise activate --status bash)"
  echo 'eval "$(mise activate --status bash)"' >> ~/.bashrc

  mkdir -p /srv/{apps,conf} ~/.local/bin ~/.config

  # make conf a git repo, useful to track changes
  # TODO: auto commit + auto write commit message
  git config --global user.name web
  git config --global user.email "web@web"
  git config --global init.defaultBranch master
  $(cd conf; git init && git add . && git commit -m init)

  echo -e "{email nuqayah@gmail.com}\nimport *.caddy" > /srv/conf/Caddyfile
  caddy fmt --overwrite /srv/conf/Caddyfile
  sudo update-alternatives --set editor /usr/bin/vim.basic
  git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf && ~/.fzf/install --all
  wget -P ~/.local/ https://raw.githubusercontent.com/mustafa0x/util/master/sqlite_upsert.py
  ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519

  cat <<EOF >> ~/.bashrc
alias dc='docker compose'
alias ls='nnn -de'
alias ipy=ipython3
alias s='sudo systemctl'
alias r='mise run --'
if [ "\$PWD" == "\$HOME" ]; then cd /srv; fi
HISTSIZE=9999999
HISTFILESIZE=9999999
EOF

  ln -s $(which fdfind) ~/.local/bin/fd

  install_python() {
    mise install python@latest
    mise global python@latest
    mise install poetry
    mise global poetry@latest
    pip install ipython regex
  }

  install_nodejs() {
    mise install nodejs@lts
    mise global nodejs@lts
    npm install -g npm@latest
    npm install -g pnpm
    pnpm setup
    pnpm install -g zx@7.2.3
  }

  install_python
  install_nodejs
}

main
