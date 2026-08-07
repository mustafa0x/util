#!/usr/bin/env bash

set -euo pipefail

if (($# != 1)); then
  echo "Usage: $0 [root@]host" >&2
  exit 1
fi

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/server-setup" && pwd)
host=${1#*@}
root_target=$1
[[ $root_target == "$host" ]] && root_target="root@$host"
web_target="web@$host"

if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$web_target" true 2>/dev/null; then
  mise bootstrap remote \
    --host "$root_target" \
    --source "$project_dir" \
    --only accounts,packages \
    --update \
    --yes

  mise bootstrap remote \
    --host "$root_target" \
    --source "$project_dir" \
    --only files,services,firewall \
    --yes
fi

web_args=(
  bootstrap remote
  --host "$web_target"
  --source "$project_dir"
  --update
  --yes
)
if ssh -o BatchMode=yes "$web_target" test -x /usr/local/bin/mise; then
  web_args+=(--remote-mise /usr/local/bin/mise)
fi
mise "${web_args[@]}"
