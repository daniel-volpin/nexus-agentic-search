# Gitea -> GitHub Mirror (Deploy-First)

Use this when Gitea is your deployment source of truth and GitHub is backup/mirror.

## Target topology

- Primary: Gitea (`main` receives deploy-intended pushes)
- Mirror: GitHub (one-way copy from Gitea)

## 1) Add GitHub remote on the Gitea server bare repo

On your Gitea host, inside the bare repo directory:

```bash
cd /var/lib/gitea/data/git/repositories/daniel/nexus-agentic-search.git
git remote add github git@github.com:daniel-volpin/nexus-agentic-search.git
# or update if it already exists:
git remote set-url github git@github.com:daniel-volpin/nexus-agentic-search.git
```

Use an SSH key on the Gitea host that has write access to the GitHub repo.

## 2) Install post-receive hook

Create/replace `hooks/post-receive` in that same bare repo:

```bash
#!/usr/bin/env bash
set -euo pipefail

script="/ABS/PATH/TO/nexus-agentic-search/deploy/scripts/push-github-mirror.sh"

while read -r _oldrev _newrev refname; do
  if [[ "$refname" == "refs/heads/main" ]]; then
    "$script" github main
  fi
done
```

Then:

```bash
chmod +x hooks/post-receive
```

## 3) Validate

Push once to `main` in Gitea, then verify refs:

```bash
git ls-remote github refs/heads/main
git ls-remote origin refs/heads/main
```

Hashes should match after hook execution.

## Notes

- Keep this one-way (Gitea -> GitHub) to avoid drift loops.
- If hook push fails, Gitea push still succeeds; monitor hook logs on server.
