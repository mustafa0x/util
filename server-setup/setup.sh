#!/bin/bash
set -euxo pipefail

[ "$EUID" -ne 0 ] && echo "Please run as root" && exit 1
export DEBIAN_FRONTEND=noninteractive

#####################################################################
####################### CONFIG #######################
#####################################################################

readonly CONFIG_HOSTNAME=""
readonly CONFIG_USERNAME="web"
PACKAGES="htop unzip zip tree git build-essential nnn brotli fd-find ripgrep rename sqlite3 ncdu trash-cli jq"

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
  cp -R /root/.ssh /home/${username}/
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
  mv ./caddy.service /lib/systemd/system/caddy.service
  sudo systemctl daemon-reload
  sudo service caddy restart

  print_done
}

install_pandoc() {
  VER=$(curl -s https://api.github.com/repos/jgm/pandoc/releases | jq -r '.[0].tag_name')
  curl -SsL https://github.com/jgm/pandoc/releases/download/${VER}/pandoc-${VER}-1-arm64.deb -o pandoc.deb
  sudo dpkg -i pandoc.deb
  rm pandoc.deb
}

install_php() {
  echo -n "-> Installing PHP... "
  apt-get install -y php8.1-fpm php8.1-mbstring php8.1-sqlite3 php8.1-zip php8.1-curl # php8.1-gd php8.1-xml
}

install_rtx() {
  echo -n "-> Installing rtx..."
  curl https://rtx.pub/rtx-latest-linux-arm64 > /usr/local/bin/rtx
  chmod +x /usr/local/bin/rtx
  sudo -i -u $CONFIG_USERNAME bash <<'EOF'
    eval "$(rtx activate bash)"
    echo 'eval "$(rtx activate bash)"' >> ~/.bashrc
EOF
  echo -e '#!/bin/bash\nexec rtx x $1 -- "$@"' | cat - > /usr/local/bin/rtxx
  chmod +x /usr/local/bin/rtxx
  print_done
}

install_python() {
  apt-get install -y zlib1g zlib1g-dev libssl-dev libbz2-dev libsqlite3-dev libffi-dev liblzma-dev libncurses5-dev libreadline-dev
  rtx install python@latest
  rtx global python@latest
  rtx plugin add poetry
  rtx install poetry
  rtx global poetry
  pip install ipython regex
}

install_nodejs() {
  echo -n "-> Installing Node.js... "

  sudo -i -u $CONFIG_USERNAME bash <<'EOF'
    rtx install nodejs@lts
    rtx global nodejs@lts
    npm install -g npm@latest
    npm install -g pnpm
    pnpm setup
    source ~/.bashrc
    pnpm install -g zx
EOF

  print_done
}

install_docker() {
  curl -fsSL https://get.docker.com -o get-docker.sh && sh ./get-docker.sh
  DOCKER_CONFIG=/usr/libexec/docker
  mkdir -p $DOCKER_CONFIG/cli-plugins
  curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-armv7 -o $DOCKER_CONFIG/cli-plugins/docker-compose
  chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose
  usermod -aG docker $CONFIG_USERNAME
}

#####################################################################
#####################################################################
#####################################################################

main() {
  if [[ ! -f caddy.service ]]; then
     echo "caddy.service missing"
     exit 1
  fi

  hostnamectl set-hostname "$CONFIG_HOSTNAME"
  apt-get update
  apt-get upgrade
  apt-get install -y $PACKAGES

  add_user $CONFIG_USERNAME
  ssh_prep

  ufw allow OpenSSH && ufw allow http && ufw allow https && ufw --force enable

  # Create a 2gb empty file on the server to be able to immediately space if there isn't any
  fallocate -l 2G /var/tmp/EMERGENCY_RESERVE

  ##################
  # Install software
  ##################
  install_rtx
  install_python
  install_nodejs
  install_docker
  install_caddy_server

  ##################
  # Config
  ##################
  chown -R $CONFIG_USERNAME:$CONFIG_USERNAME /srv

  sudo -i -u $CONFIG_USERNAME bash <<'EOF'
    mkdir -p /srv/{apps,conf} ~/.local/bin ~/.config

    # make conf a git repo, useful to track changes
    git config --global user.name web
    git config --global user.email "web@web"
    $(cd conf; git init && git add . && git commit -m init)

    echo -e "{email nuqayah@gmail.com}\nimport *.caddy" > /srv/conf/Caddyfile
    caddy fmt --overwrite /srv/conf/Caddyfile
    sudo update-alternatives --set editor /usr/bin/vim.basic
    curl -sSL https://github.com/moparisthebest/static-curl/releases/download/v8.5.0/curl-armv7 > ~/.local/bin/curl
    git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf && ~/.fzf/install --all
    wget -P ~/.local/ https://raw.githubusercontent.com/mustafa0x/util/master/sqlite_upsert.py

    # .bashrc
    echo "alias ls='nnn -de'" >> ~/.bashrc
    echo "alias ipy=ipython3" >> ~/.bashrc
    echo 'export PATH="~/.local/bin:$PATH"' >> ~/.bashrc
    echo 'cd /srv' >> ~/.bashrc
    echo HISTSIZE=9999999 >> ~/.bashrc
    echo HISTFILESIZE=9999999 >> ~/.bashrc

    ln -s $(which fdfind) ~/.local/bin/fd
EOF

  # Cleanup
  apt-get autoremove
}

main
