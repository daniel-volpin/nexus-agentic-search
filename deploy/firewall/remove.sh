#!/usr/bin/env bash
# Remove all rules tagged "nexus-firewall" from the DOCKER-USER chain
# (v4 and v6). Idempotent.

set -euo pipefail

COMMENT_TAG="nexus-firewall"

_strip_chain() {
  local cmd="$1"
  while $cmd -L DOCKER-USER --line-numbers 2>/dev/null | awk \
      -v tag="$COMMENT_TAG" '$0 ~ tag { print $1 }' | tac | \
      while read -r linenum; do
        [[ -n "$linenum" ]] && $cmd -D DOCKER-USER "$linenum"
      done
  do
    :
  done
}

_strip_chain iptables
if command -v ip6tables >/dev/null 2>&1; then
  _strip_chain ip6tables
fi

echo "[remove.sh] all nexus-firewall rules removed"
