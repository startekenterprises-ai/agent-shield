# Agent-Shield — Grok Build Plugin

Security scanner for AI agent workflows, exposed as a remote MCP server.

## What it does

One tool — `shield.scan` — scans text for prompt injection, leaked
secrets/API keys, exposed PII, and SSRF-risk URLs, and returns a verdict
(`clean` / `suspicious` / `malicious`) with per-class scores. Two modes:
`detect` (report only) and `enforce` (returns `allow` / `review` / `block`).

## Endpoints and credentials

- **MCP endpoint:** `https://agent-shield.startekenterprises.com/mcp`
  (Streamable HTTP, JSON-RPC 2.0)
- **Auth:** API key in the `X-API-Key` header, read from the
  `AGENT_SHIELD_API_KEY` environment variable at runtime.
- **Get a free key** (1,000 scans/day, no email, no credit card):

  ```bash
  curl -X POST https://agent-shield.startekenterprises.com/v1/signup \
    -H 'Content-Type: application/json' \
    -d '{"label":"grok-build"}'
  ```

No other credentials are read from disk. No OAuth flow — the plugin
never touches the user's local files beyond the declared env var.

## Privacy

Scanned content is processed in memory and never stored — only a content
hash and match counts are retained. Findings carry classes and counts,
never matched text. Full policy:
https://agent-shield.startekenterprises.com/privacy
