# Cloud Run + Private SearXNG

This document describes one optional deployment pattern.
It is intentionally generic and should be treated as an operator checklist, not a turnkey recipe.

## Pattern

- run `nexus-agentic-search` on Cloud Run
- keep SearXNG on a private VM or private container host
- force Cloud Run egress through controlled networking
- require a shared application-layer credential between the service and SearXNG

## Minimal Environment

Service env:

- `SEARXNG_BASE_URL=https://your-searxng-host`
- `SEARXNG_ENGINES=google,duckduckgo`
- `SEARXNG_API_KEY=replace-me`
- `VERTEX_PROJECT=your-project-id`
- `VERTEX_LOCATION=global`
- `NEXUS_HTTP_TOKEN=replace-me`
- `NEXUS_MCP_TOKEN=replace-me`

## Network Controls

1. Route Cloud Run egress through a controlled VPC path.
2. Expose SearXNG only behind TLS.
3. Restrict inbound access to the SearXNG reverse proxy so only the expected egress source can reach it.
4. Do not expose a direct unauthenticated SearXNG port publicly.

## SearXNG Controls

- limit enabled engines to the subset you intend to operate
- terminate TLS before SearXNG
- require `X-Searx-Key` or an equivalent shared secret at the reverse proxy
- keep SearXNG bound to a private interface where possible

## Validation

1. `GET /v1/health` returns `200`.
2. Authenticated `POST /v1/search` succeeds.
3. Requests to SearXNG without the expected shared key are rejected.
4. No unintended public listener exposes the raw SearXNG instance.
