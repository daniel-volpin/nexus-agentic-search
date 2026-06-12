#!/usr/bin/env bash
# Roll back to the previous image digest saved by build-and-pin.sh.
#
# Usage:
#   ./deploy/scripts/rollback.sh           # roll back one step
#   ./deploy/scripts/rollback.sh <digest>  # roll back to a specific digest

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIGEST_FILE="$ROOT/.last-image-digest"
PREV_FILE="${DIGEST_FILE}.prev"

if [[ -n "${1:-}" ]]; then
  TARGET="$1"
elif [[ -f "$PREV_FILE" ]]; then
  TARGET="$(cat "$PREV_FILE")"
else
  echo "[rollback] no previous digest found. Pass one explicitly:" >&2
  echo "  ./deploy/scripts/rollback.sh sha256:abc123..." >&2
  exit 1
fi

echo "[rollback] rolling back to: $TARGET"

# Tag the target digest as :rollback so compose can reference it.
docker tag "$TARGET" nexus-agentic-search:rollback 2>/dev/null || {
  echo "[rollback] could not tag $TARGET — is it still in the local store?" >&2
  exit 1
}

# Update the tracked digest.
echo "$TARGET" > "$DIGEST_FILE"

echo "[rollback] tagged nexus-agentic-search:rollback"
echo "[rollback] restart with:  docker compose up -d"
