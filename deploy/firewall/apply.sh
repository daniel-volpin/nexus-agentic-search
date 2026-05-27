#!/usr/bin/env bash
# Spec 12 §Network egress firewall. Defense-in-depth with the app-layer
# SSRF guard: drops outbound traffic from the agentic-net subnet to
# RFC1918 / link-local / CGNAT / loopback / IPv6 ULA.
#
# Idempotent: re-running adds rules only if not already present.
# Tag every rule with `nexus-firewall` so deploy/firewall/remove.sh
# can clean up by tag.
#
# Run as root (or via sudo) on the Docker host. Assumes Docker's
# DOCKER-USER chain exists (it's created when the Docker daemon starts).

set -euo pipefail

NETWORK_NAME="${NETWORK_NAME:-nexus_agentic-net}"
COMMENT_TAG="nexus-firewall"

if ! command -v iptables >/dev/null 2>&1; then
  echo "[apply.sh] iptables not found; aborting" >&2
  exit 127
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[apply.sh] docker not found; aborting" >&2
  exit 127
fi

# Resolve agentic-net subnet from compose-managed network.
SUBNET=$(docker network inspect "$NETWORK_NAME" \
  --format '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null || true)
if [[ -z "$SUBNET" ]]; then
  echo "[apply.sh] could not resolve subnet for network '$NETWORK_NAME'" >&2
  echo "[apply.sh] start the compose stack first (docker compose up -d --no-start)" >&2
  exit 1
fi
echo "[apply.sh] agentic-net subnet: $SUBNET"

_already_present() {
  iptables -C "$@" 2>/dev/null
}

_add_drop_rule() {
  local dest="$1"
  if _already_present DOCKER-USER -s "$SUBNET" -d "$dest" -j DROP -m comment --comment "$COMMENT_TAG"; then
    echo "[apply.sh]  already present: DROP $SUBNET -> $dest"
  else
    iptables -I DOCKER-USER -s "$SUBNET" -d "$dest" -j DROP -m comment --comment "$COMMENT_TAG"
    echo "[apply.sh]  added: DROP $SUBNET -> $dest"
  fi
}

# RFC1918 / link-local / CGNAT / loopback / multicast / reserved
for dest in \
  10.0.0.0/8 \
  172.16.0.0/12 \
  192.168.0.0/16 \
  169.254.0.0/16 \
  127.0.0.0/8 \
  100.64.0.0/10 \
  0.0.0.0/8 \
  224.0.0.0/4 \
  240.0.0.0/4 ; do
  _add_drop_rule "$dest"
done

# IPv6 equivalents (if ip6tables present)
if command -v ip6tables >/dev/null 2>&1; then
  _add_drop_rule_v6() {
    local dest="$1"
    if ip6tables -C DOCKER-USER -d "$dest" -j DROP -m comment --comment "$COMMENT_TAG" 2>/dev/null; then
      echo "[apply.sh]  v6 already present: DROP -> $dest"
    else
      ip6tables -I DOCKER-USER -d "$dest" -j DROP -m comment --comment "$COMMENT_TAG"
      echo "[apply.sh]  v6 added: DROP -> $dest"
    fi
  }
  for dest in \
    ::1/128 \
    fc00::/7 \
    fe80::/10 \
    ff00::/8 ; do
    _add_drop_rule_v6 "$dest"
  done
fi

echo "[apply.sh] done. To remove: deploy/firewall/remove.sh"
