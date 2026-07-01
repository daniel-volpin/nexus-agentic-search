#!/usr/bin/env bash
set -euo pipefail

# Push selected refs to a GitHub backup mirror from a Forgejo-hosted repo.
# Intended for use in a server-side post-receive hook.

usage() {
  cat <<'EOF'
Usage:
  push-github-mirror.sh <github-remote-name> [branch]

Examples:
  push-github-mirror.sh github main
  push-github-mirror.sh github
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

remote_name="${1:-}"
branch="${2:-main}"

if [[ -z "${remote_name}" ]]; then
  usage
  exit 2
fi

if ! git remote get-url "${remote_name}" >/dev/null 2>&1; then
  echo "error: remote '${remote_name}' does not exist in $(pwd)" >&2
  exit 3
fi

echo "sync: pushing ${branch} and tags to ${remote_name}"
git push "${remote_name}" "refs/heads/${branch}:refs/heads/${branch}"
git push "${remote_name}" --tags
echo "sync: done"
