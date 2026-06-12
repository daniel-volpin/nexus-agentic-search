#!/usr/bin/env bash
# Build the nexus-agentic-search image and pin its digest in compose.yaml.
#
# Usage:
#   ./deploy/scripts/build-and-pin.sh              # build :latest
#   ./deploy/scripts/build-and-pin.sh v0.1.0       # build with tag

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-latest}"
IMAGE="nexus-agentic-search:${TAG}"
GIT_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"

echo "[build-and-pin] building $IMAGE  (GIT_SHA=$GIT_SHA)"

docker build \
  --build-arg "GIT_SHA=${GIT_SHA}" \
  -t "$IMAGE" \
  -f "$ROOT/Dockerfile" \
  "$ROOT"

DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || echo "")"
if [[ -z "$DIGEST" ]]; then
  DIGEST="$(docker inspect --format='{{.Id}}' "$IMAGE")"
fi

echo "[build-and-pin] image: $IMAGE"
echo "[build-and-pin] digest: $DIGEST"

# Write a .digest file so rollback.sh can track the previous image.
DIGEST_FILE="$ROOT/.last-image-digest"
if [[ -f "$DIGEST_FILE" ]]; then
  cp "$DIGEST_FILE" "${DIGEST_FILE}.prev"
fi
echo "$DIGEST" > "$DIGEST_FILE"

echo "[build-and-pin] done. Start with:  docker compose up -d"
