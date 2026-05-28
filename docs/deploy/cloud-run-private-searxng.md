# Cloud Run + Private SearXNG Runbook

This is the Cloud Run deployment variant for this repo.

## Architecture

- `nexus-agentic-search` runs on Cloud Run.
- SearXNG runs on a private VM.
- Cloud Run egress is forced through Serverless VPC Connector + Cloud NAT.
- VM firewall only allows inbound from the single NAT public IP.
- Reverse proxy (Caddy) requires `X-Searx-Key` before forwarding to SearXNG.

## Required env vars (Cloud Run service)

- `SEARXNG_BASE_URL=https://<your-searxng-host>`
- `SEARXNG_ENGINES=google,duckduckgo`
- `SEARXNG_API_KEY=<shared-key>`
- `VERTEX_PROJECT=<gcp-project-id>`
- `VERTEX_LOCATION=global`

## GCP networking controls

1. Create Serverless VPC Connector in the Cloud Run region.
2. Create Cloud Router + Cloud NAT with a reserved static external IP.
3. Configure Cloud Run with:
   - `--vpc-connector=<connector>`
   - `--vpc-egress=all-traffic`
4. On VM firewall, allow only:
   - source range: `<nat_static_ip>/32`
   - destination port: `443`

## SearXNG controls

- Restrict engines to `google` and `duckduckgo` in `settings.yml`.
- Terminate TLS at Caddy.
- Require `X-Searx-Key` at Caddy for `/search`.
- Keep SearXNG bound to localhost/private interface behind proxy.

## Validation checks

1. Cloud Run:
   - `GET /v1/health` returns `200`
   - authenticated `POST /v1/search` returns `200`
2. VM:
   - `curl` without `X-Searx-Key` returns `401`
   - `curl` with `X-Searx-Key` returns SearXNG JSON
3. Firewall:
   - no public `8088` (or other cleartext fallback) open
   - only `443` allowed from NAT IP
