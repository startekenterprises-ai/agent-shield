# Agent-Shield MCP Server (stdio)

<!-- mcp-name: io.github.startekenterprises-ai/agent-shield -->

A tiny, dependency-free MCP server that lets an AI agent scan untrusted
content with [Agent-Shield](https://agent-shield.startekenterprises.com/)
**before acting on it** — tool outputs, fetched web pages, file contents,
messages. It wraps the hosted Agent-Shield scan API over stdio and exposes
exactly one tool:

| Tool | Arguments | What it returns |
|---|---|---|
| `shield_scan` | `content` (required), `content_type` (optional) | JSON verdict (`no_signals` \| `suspicious` \| `malicious`), per-class scores (prompt_injection, secrets_exposure, pii_disclosure, ssrf_risk), findings as classes + match counts (never the matched text), and a signed, independently verifiable receipt |

Honest scope: Agent-Shield is a deterministic pre-action filter. It is strong
on leaked secrets, exposed PII, and SSRF-risk URLs, and weak on novel prompt
injection (published external hold-out recall: 4.7% — see the
[benchmarks](https://agent-shield.startekenterprises.com/benchmarks), misses
included). A `no_signals` verdict means "no known-bad patterns detected", not
a guarantee. Content is processed in memory and never stored.

## 1. Get a free API key

1,000 scans/day, no email, no credit card:

```bash
curl -X POST https://agent-shield.startekenterprises.com/v1/signup \
  -H 'Content-Type: application/json' \
  -d '{"label":"my-agent"}'
# → {"api_key":"as_live_...","tier":"free","scans_per_day":1000}
```

Keep the key in your MCP client config as `AGENT_SHIELD_API_KEY` — never in
the content you scan.

## 2. Install

Python 3.9+, standard library only — nothing else to install.

From a clone of this repo (works today):

```bash
git clone https://github.com/startekenterprises-ai/agent-shield.git
pip install ./agent-shield/mcp-server
# or, without installing:
python3 agent-shield/mcp-server/agent_shield_mcp/server.py
```

Via uv, from the clone:

```bash
uvx --from ./agent-shield/mcp-server agent-shield-mcp
```

> PyPI publication (`uvx agent-shield-mcp` / `pip install agent-shield-mcp`)
> is staged; until it lands, install from the repo as above.

## 3. Client configuration

Claude Code (`.mcp.json` or `~/.claude.json`):

```json
{
  "mcpServers": {
    "agent-shield": {
      "command": "agent-shield-mcp",
      "env": { "AGENT_SHIELD_API_KEY": "as_live_..." }
    }
  }
}
```

Claude Code shortcut after installing:

```sh
claude mcp add agent-shield -e AGENT_SHIELD_API_KEY=as_live_... -- agent-shield-mcp
```

Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "agent-shield": {
      "command": "agent-shield-mcp",
      "env": { "AGENT_SHIELD_API_KEY": "as_live_..." }
    }
  }
}
```

If you run from the clone without installing, use
`"command": "python3", "args": ["/absolute/path/to/agent-shield/mcp-server/agent_shield_mcp/server.py"]`
in place of `agent-shield-mcp`.

## Notes

- The server reads one optional extra variable, `AGENT_SHIELD_BASE_URL`,
  only if you run a private-cloud deployment of the API; the default is the
  public hosted API.
- Prefer plain HTTP? The same scan is `POST /v2/scan` with header
  `X-API-Key` — see the repo README Quickstart and
  [The Agent-Shield Protocol](https://agent-shield.startekenterprises.com/protocol).

Registry name: `io.github.startekenterprises-ai/agent-shield` · Version 1.0.0 · License: MIT (Agent-Shield open-core)
