# OSS Launch Kit

This folder contains reusable artifacts for open-sourcing `nexus-agentic-search`.

Use it for:

- README examples
- screenshot capture
- release prep
- final pre-public review

Contents:

- [`demo-response.json`](demo-response.json): sanitized example HTTP response using the real transport schema
- [`demo-snippets.md`](demo-snippets.md): copy-ready README blocks for HTTP, MCP, and example response sections
- [`screenshot-checklist.md`](screenshot-checklist.md): repeatable capture guide for terminal or MCP screenshots
- [`launch-checklist.md`](launch-checklist.md): public-release checklist for docs, metadata, secrets, and release notes
- [`release-notes-v0.1.0.md`](release-notes-v0.1.0.md): publish-ready draft release notes
- [`final-publication-checklist.md`](final-publication-checklist.md): last-step checklist before making the repo public

Recommended order:

1. Start the service locally with safe demo credentials.
2. Use [`demo-snippets.md`](demo-snippets.md) to draft or refine the README examples.
3. Capture one screenshot using [`screenshot-checklist.md`](screenshot-checklist.md).
4. Adapt [`release-notes-v0.1.0.md`](release-notes-v0.1.0.md) into the GitHub Release body.
5. Run [`launch-checklist.md`](launch-checklist.md) and [`final-publication-checklist.md`](final-publication-checklist.md) before publishing or tagging a release.
