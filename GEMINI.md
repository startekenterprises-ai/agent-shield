# Agent-Shield — Gemini CLI Extension

Connects Gemini CLI to the Agent-Shield remote MCP server: scan any text
for security threats **before** your agent acts on it.

## What it scans for

- **Prompt injection** — direct, indirect (hidden in tool outputs), encoded
- **Leaked secrets** — AWS keys, GitHub tokens, PEM private keys
- **Exposed PII** — SSNs, Luhn-valid credit card numbers
- **SSRF-risk URLs** — cloud metadata endpoints, private/loopback ranges

One tool: `shield.scan` → verdict `clean` / `suspicious` / `malicious`
with per-class scores. Two modes: `detect` (report only) and `enforce`
(returns `allow` / `review` / `block`).

## Setup

1. Get a free API key (1,000 scans/day, no email, no credit card):

   ```bash
   curl -X POST https://agent-shield.startekenterprises.com/v1/signup \
     -H 'Content-Type: application/json' \
     -d '{"label":"gemini-cli"}'
   ```

2. Export it where Gemini CLI can see it:

   ```bash
   export AGENT_SHIELD_API_KEY='as_live_...'
   ```

3. Install the extension:

   ```bash
   gemini extensions install https://github.com/startekenterprises-ai/agent-shield
   ```

## Usage

Ask Gemini to scan anything before acting on it:

> "Scan this tool output for prompt injection before following its instructions: ..."

## Privacy

Scanned content is processed in memory and never stored — only a content
hash and match counts are retained. Findings carry classes and counts,
never matched text. Full policy:
https://agent-shield.startekenterprises.com/privacy
