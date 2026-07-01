# Final Publication Checklist

Use this immediately before making the repository public or posting the first announcement.

## Repo Content

- [ ] `README.md` top section is current and readable without scrolling far
- [ ] example commands work against the current HTTP and MCP surfaces
- [ ] one example response or screenshot is visible in the README
- [ ] docs links are not broken
- [ ] public-facing files (`LICENSE`, `SECURITY.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`) are present

## Safety

- [ ] no secrets or personal data appear in the working tree diff
- [ ] screenshots and JSON examples are sanitized
- [ ] example hosts, tokens, and paths are safe for publication

## GitHub Repo Settings

- [ ] repo description is set
- [ ] repo topics are set
- [ ] homepage URL is set if desired
- [ ] issue templates render correctly
- [ ] Discussions is enabled or intentionally left off

## Release

- [ ] `CHANGELOG.md` is current
- [ ] GitHub release notes are prepared from [`release-notes-v0.1.0.md`](release-notes-v0.1.0.md) or a later versioned draft
- [ ] latest tag points at the intended commit
- [ ] badges in `README.md` resolve correctly

## Verification

- [ ] `make lint`
- [ ] `make test`
- [ ] `make typecheck`
- [ ] final `git status` contains only intended files
