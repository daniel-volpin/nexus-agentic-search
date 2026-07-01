# Releasing

This project uses tagged releases with `CHANGELOG.md` as the human-readable release summary.

## Before Tagging

- confirm the public README still reflects the current HTTP and MCP surfaces
- confirm `pyproject.toml` metadata and repository URLs are current
- confirm `LICENSE`, `SECURITY.md`, `SUPPORT.md`, and `CODE_OF_CONDUCT.md` are still accurate
- run the current CI-equivalent checks locally:

```bash
make lint
make test
make typecheck
```

- review `git status` for accidental local files, especially env files and secrets
- update `CHANGELOG.md` with the user-visible changes in the release

## Create The Release

1. Choose the next semantic version tag, for example `v0.1.1`.
2. Create an annotated tag locally.
3. Push the tag to the canonical GitHub remote.
4. Publish a GitHub Release using the matching `CHANGELOG.md` section as the release notes base.

Example:

```bash
git tag -a v0.1.1 -m "v0.1.1"
git push origin v0.1.1
```

## Release Notes Checklist

Include:

- what changed for users
- any setup or config changes
- any known limitations or experimental areas
- any migration notes if behavior changed

## After Release

- confirm the GitHub release page renders correctly
- confirm badges in `README.md` point at the expected tag/release
- if needed, mirror the release to any secondary remote after GitHub is correct
