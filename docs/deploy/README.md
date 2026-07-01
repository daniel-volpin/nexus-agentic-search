# Deployment Notes

This directory contains optional deployment patterns and operator scripts.

Important scope limits:

- these files are examples, not a supported one-command production installer
- you must adapt networking, secrets management, and access control to your own environment
- anything here should be reviewed before public or multi-tenant exposure

Recommended interpretation:

- [`cloud-run-private-searxng.md`](cloud-run-private-searxng.md): one possible managed-service pattern
- [`forgejo-github-mirror.md`](forgejo-github-mirror.md): one possible repository mirroring pattern
- [`../../deploy`](../../deploy): helper scripts for operators, not part of the minimal local developer workflow
