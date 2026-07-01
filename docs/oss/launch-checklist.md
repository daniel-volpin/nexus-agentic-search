# Launch Checklist

Use this before making the repository public, announcing it, or cutting a release intended for outside users.

## Secrets And Safety

- [ ] No real credentials are tracked in git
- [ ] `.env`, `.env.local`, and compose secrets are ignored
- [ ] README examples use fake tokens and safe hosts only
- [ ] screenshots do not expose secrets, local paths, or personal machine details

## Public Repo Surface

- [ ] `README.md` explains what the project is in the first screenful
- [ ] setup instructions have one clear fastest path
- [ ] HTTP and MCP examples match the current code surface
- [ ] `LICENSE`, `SECURITY.md`, `SUPPORT.md`, and `CODE_OF_CONDUCT.md` are present
- [ ] `pyproject.toml` metadata and URLs point to the public repository

## Docs And Positioning

- [ ] scope and non-goals are explicit
- [ ] experimental or operator-specific areas are labeled as such
- [ ] architecture description matches the current request flow
- [ ] release notes summarize user-visible changes clearly

## GitHub Hygiene

- [ ] issue templates exist and blank issues are disabled if desired
- [ ] `CODEOWNERS` is present
- [ ] dependency update policy is configured
- [ ] repository description, topics, and homepage are set in GitHub

## Demo Assets

- [ ] `docs/oss/demo-response.json` still matches the real response contract
- [ ] one screenshot or example response is ready for the README
- [ ] asset filenames and paths are stable before publishing links

## Release Prep

- [ ] `CHANGELOG.md` is updated
- [ ] local CI-equivalent checks ran successfully
- [ ] version tag and release notes are ready
- [ ] release copy explains limitations as well as strengths
