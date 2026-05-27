#!/usr/bin/env bash
# Generate / rotate the bearer tokens and SearXNG secret key.
# Writes to secrets/nexus.env and secrets/searxng.env, mode 0600.
#
# Usage:
#   ./deploy/scripts/rotate-tokens.sh            # rotate, no restart
#   ./deploy/scripts/rotate-tokens.sh --restart  # rotate + compose restart

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRETS_DIR="$ROOT/secrets"
NEXUS_ENV="$SECRETS_DIR/nexus.env"
SEARXNG_ENV="$SECRETS_DIR/searxng.env"

mkdir -p "$SECRETS_DIR"
chmod 0700 "$SECRETS_DIR"

# 32 random bytes ~> 44 chars base64. Safe in env values (no `=` only).
_gen_token() { openssl rand -base64 32 | tr -d '\n='; }
_gen_hex_secret() { openssl rand -hex 32; }

NEW_HTTP="$(_gen_token)"
NEW_MCP="$(_gen_token)"
NEW_SEARXNG="$(_gen_hex_secret)"

# nexus.env — preserve existing non-token values.
if [[ -f "$NEXUS_ENV" ]]; then
  TMP=$(mktemp)
  awk -v http="$NEW_HTTP" -v mcp="$NEW_MCP" '
    BEGIN { http_seen=0; mcp_seen=0 }
    /^NEXUS_HTTP_TOKEN=/ { print "NEXUS_HTTP_TOKEN=" http; http_seen=1; next }
    /^NEXUS_MCP_TOKEN=/  { print "NEXUS_MCP_TOKEN="  mcp;  mcp_seen=1;  next }
    { print }
    END {
      if (!http_seen) print "NEXUS_HTTP_TOKEN=" http
      if (!mcp_seen)  print "NEXUS_MCP_TOKEN="  mcp
    }
  ' "$NEXUS_ENV" > "$TMP"
  mv "$TMP" "$NEXUS_ENV"
else
  printf 'NEXUS_HTTP_TOKEN=%s\nNEXUS_MCP_TOKEN=%s\n' "$NEW_HTTP" "$NEW_MCP" > "$NEXUS_ENV"
fi
chmod 0600 "$NEXUS_ENV"

# searxng.env
if [[ -f "$SEARXNG_ENV" ]]; then
  TMP=$(mktemp)
  awk -v key="$NEW_SEARXNG" '
    BEGIN { seen=0 }
    /^SEARXNG_SECRET_KEY=/ { print "SEARXNG_SECRET_KEY=" key; seen=1; next }
    { print }
    END { if (!seen) print "SEARXNG_SECRET_KEY=" key }
  ' "$SEARXNG_ENV" > "$TMP"
  mv "$TMP" "$SEARXNG_ENV"
else
  printf 'SEARXNG_SECRET_KEY=%s\n' "$NEW_SEARXNG" > "$SEARXNG_ENV"
fi
chmod 0600 "$SEARXNG_ENV"

echo "[rotate-tokens.sh] wrote $NEXUS_ENV ($(wc -c < "$NEXUS_ENV") bytes)"
echo "[rotate-tokens.sh] wrote $SEARXNG_ENV ($(wc -c < "$SEARXNG_ENV") bytes)"

if [[ "${1:-}" == "--restart" ]]; then
  ( cd "$ROOT" && docker compose restart )
fi
