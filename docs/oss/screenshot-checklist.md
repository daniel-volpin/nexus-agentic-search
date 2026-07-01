# Screenshot Checklist

Use this checklist before capturing a screenshot for the README, release notes, or repository social preview.

## Best Screenshot Types

For this repo, prefer one of these:

1. terminal screenshot showing one `curl` request and a trimmed JSON response
2. MCP client screenshot showing the `agentic_search` tool call and grounded answer
3. architecture or flow diagram only if you want a more polished overview image

## Before Capture

- use demo-only tokens and local endpoints
- use a safe query with non-sensitive output
- close unrelated tabs, notifications, and terminals
- avoid showing local filesystem paths if possible
- avoid showing browser bookmarks, personal accounts, or machine names

## Redaction Rules

Do not expose:

- bearer tokens
- API keys
- private IPs or internal hostnames you do not want public
- machine-local paths with personal information
- unrelated shell history

## Terminal Capture Recipe

1. Start the service locally.
2. Export demo tokens from `.env.local`.
3. Run one clean `curl` example.
4. Pipe through a formatter if needed so the response fits cleanly on screen.
5. Crop to the command and the top of the response.

Suggested commands:

```bash
curl -s http://127.0.0.1:8186/v1/search \
  -H "Authorization: Bearer $NEXUS_HTTP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How is Python used in automation?",
    "max_results": 5
  }'
```

If `jq` is available:

```bash
curl -s http://127.0.0.1:8186/v1/search \
  -H "Authorization: Bearer $NEXUS_HTTP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How is Python used in automation?",
    "max_results": 5
  }' | jq
```

## Save Location

Store screenshots under:

- `docs/images/` for README and docs assets

Suggested names:

- `docs/images/http-search-example.png`
- `docs/images/mcp-tool-example.png`

## README Embed

```md
![HTTP search example](docs/images/http-search-example.png)
```
