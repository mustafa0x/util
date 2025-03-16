#!/bin/bash

set -euxo pipefail

APP_SLUG=$1
[ -z "${APP_SLUG}" ] && echo "APP_SLUG is required" && exit 1

ssh $HOST "
set -euxo pipefail
mkdir -p /srv/apps/${APP_SLUG}
echo -e '${APP_SLUG}.slk.is {
  root * /srv/apps/${APP_SLUG}
  file_server
  try_files {path} {path}/ /
  log {
    output file /var/log/caddy/${APP_SLUG}.log
  }
}
' >> /srv/conf/${APP_SLUG}.caddy
caddy fmt --overwrite /srv/conf/${APP_SLUG}.caddy
"
