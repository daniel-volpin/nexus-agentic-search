# Demo Snippets

These blocks are intended to be pasted into `README.md`, release notes, or project docs.

The response schema below matches the current transport contract:

- `answer_text`
- `citations`
- `rejected_citations`
- `documents`
- `cost_usd`
- `tokens_in`
- `tokens_out`
- `latency_ms`
- `degraded`
- `ungrounded`

## HTTP Example

```bash
curl -s http://127.0.0.1:8186/v1/search \
  -H "Authorization: Bearer $NEXUS_HTTP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How is Python used in automation?",
    "max_results": 5
  }'
```

## MCP Example

```python
import asyncio

from fastmcp import Client


async def main() -> None:
    async with Client("http://127.0.0.1:8185/mcp", auth="YOUR_NEXUS_MCP_TOKEN") as client:
        result = await client.call_tool(
            "agentic_search",
            {"query": "How is Python used in automation?", "max_results": 5},
        )
        print(result.structured_content["answer_text"])


asyncio.run(main())
```

## Example Response

```json
{
  "answer_text": "Python is widely used in automation and scripting workflows.[^claim-1]",
  "citations": [
    {
      "url": "https://example.com/python-automation",
      "content_hash": "doc-1",
      "byte_start": 0,
      "byte_end": 56,
      "quote": "Python is a programming language used widely in automation.",
      "claim_id": "claim-1"
    }
  ],
  "rejected_citations": [],
  "documents": [
    {
      "url": "https://example.com/python-automation",
      "content_hash": "doc-1"
    }
  ],
  "cost_usd": 0.2,
  "tokens_in": 100,
  "tokens_out": 25,
  "latency_ms": 1840,
  "degraded": false,
  "ungrounded": false
}
```

## Caption Copy

Short version:

> `nexus-agentic-search` returns grounded answers with validated citations over HTTP or MCP.

Long version:

> `nexus-agentic-search` is a self-hosted backend for agentic web search. It searches, crawls, validates citations, and returns grounded answers through either an HTTP API or an MCP tool surface.
