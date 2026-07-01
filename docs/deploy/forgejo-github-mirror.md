# Forgejo To GitHub Mirror

Use this pattern only if you intentionally keep one repository as the deploy source of truth and mirror to another.

## Target Topology

- primary remote: Forgejo
- mirror remote: GitHub
- sync direction: one-way

## 1) Add The Mirror Remote

Inside the bare repository on the Forgejo host:

```bash
cd /path/to/bare/repository.git
git remote add github git@github.com:your-org/your-repo.git
# or update if it already exists
git remote set-url github git@github.com:your-org/your-repo.git
```

Use an SSH key on the Forgejo host that has write access to the GitHub mirror.

## 2) Install A Post-Receive Hook

Create `hooks/post-receive` in the bare repository:

```bash
#!/usr/bin/env bash
set -euo pipefail

script="/absolute/path/to/deploy/scripts/push-github-mirror.sh"

while read -r _oldrev _newrev refname; do
  if [[ "$refname" == "refs/heads/main" ]]; then
    "$script" github main
  fi
done
```

Then make it executable:

```bash
chmod +x hooks/post-receive
```

## 3) Validate

Push to the primary remote, then compare refs:

```bash
git ls-remote origin refs/heads/main
git ls-remote github refs/heads/main
```

## Notes

- keep mirroring one-way to avoid drift loops
- mirror failures should be monitored explicitly
- do not treat this as a backup substitute unless you also retain independent restore procedures
