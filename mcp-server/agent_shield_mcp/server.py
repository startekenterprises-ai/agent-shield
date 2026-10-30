#!/usr/bin/env python3
"""Agent-Shield — stdio MCP wrapper for the hosted scan API.

Pure Python 3 standard library, no dependencies. Exposes ONE tool,
`shield_scan`, which POSTs untrusted content to the hosted Agent-Shield API
(https://agent-shield.startekenterprises.com/v2/scan) and returns the JSON
verdict: verdict (no_signals | suspicious | malicious), per-class scores
(prompt_injection, secrets_exposure, pii_disclosure, ssrf_risk), findings
(classes + match counts, never the matched text), and a signed receipt.

The API key comes from the AGENT_SHIELD_API_KEY environment variable.
Get a free key (1,000 scans/day, no email, no credit card):
    POST https://agent-shield.startekenterprises.com/v1/signup
    {"label": "my-agent"}

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0.
Implements: initialize, notifications/initialized, ping, tools/list, tools/call.

Honest scope: Agent-Shield is a deterministic pre-action filter. It is strong
on leaked secrets, exposed PII, and SSRF-risk URLs, and weak on novel prompt
injection. A "no_signals" verdict means "no known-bad patterns detected",
not a guarantee of safety.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get(
    "AGENT_SHIELD_BASE_URL", "https://agent-shield.startekenterprises.com"
).rstrip("/")
SCAN_URL = BASE_URL + "/v2/scan"
SIGNUP_URL = BASE_URL + "/v1/signup"
USER_AGENT = "Mozilla/5.0 (compatible; agent-shield-mcp/1.0.0; +https://agent-shield.startekenterprises.com/)"
TIMEOUT = 20

SERVER_NAME = "agent-shield"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2024-11-05"


# ---------------------------------------------------------------- scan call

def scan_content(content, content_type=None):
    """POST content to /v2/scan. Returns the parsed JSON verdict dict."""
    key = os.environ.get("AGENT_SHIELD_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "AGENT_SHIELD_API_KEY is not set. Get a free key (no email required) with: "
            f"curl -X POST {SIGNUP_URL} -H 'Content-Type: application/json' "
            "-d '{\"label\":\"my-agent\"}' and put the returned api_key in your "
            "MCP client config as AGENT_SHIELD_API_KEY."
        )
    payload = {"content": content}
    if content_type:
        payload["content_type"] = content_type
    body = json.dumps(payload).encode("utf-8")
    last_err = None
    for attempt in range(2):
        req = urllib.request.Request(SCAN_URL, data=body, method="POST", headers={
            "X-API-Key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise RuntimeError(
                f"Agent-Shield API returned HTTP {e.code} for /v2/scan. {detail}".strip()
            )
        except Exception as e:  # timeout, DNS, connection reset...
            last_err = e
            if attempt == 0:
                time.sleep(0.5)
    raise RuntimeError(f"could not reach the Agent-Shield API at {SCAN_URL}: {last_err}")


# ---------------------------------------------------------------- tools

def tool_shield_scan(args):
    content = args.get("content")
    if not isinstance(content, str) or content == "":
        raise RuntimeError("shield_scan requires a non-empty 'content' string")
    result = scan_content(content, args.get("content_type"))
    return json.dumps(result, indent=2)


TOOLS = [
    {
        "name": "shield_scan",
        "description": (
            "Scan untrusted text with Agent-Shield BEFORE acting on it: tool outputs, fetched web "
            "content, file contents, messages. Returns a JSON verdict (no_signals | suspicious | "
            "malicious), per-class scores (prompt_injection, secrets_exposure, pii_disclosure, "
            "ssrf_risk), findings as classes + match counts (never the matched text), and a signed, "
            "independently verifiable receipt for the scan. Deterministic pre-filter: strong on "
            "leaked secrets, exposed PII and SSRF-risk URLs; weak on novel prompt injection — a "
            "no_signals verdict means 'no known-bad patterns detected', not a guarantee."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The untrusted text to scan before acting on it.",
                },
                "content_type": {
                    "type": "string",
                    "description": "Optional hint about the content's origin, e.g. 'tool_output', 'web_content', 'file'.",
                },
            },
            "required": ["content"],
        },
        "handler": tool_shield_scan,
    },
]
HANDLERS = {t["name"]: t["handler"] for t in TOOLS}


# ---------------------------------------------------------------- JSON-RPC

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def result(rid, payload):
    send({"jsonrpc": "2.0", "id": rid, "result": payload})


def error(rid, code, message):
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def handle(msg):
    method = msg.get("method", "")
    rid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        proto = params.get("protocolVersion") or DEFAULT_PROTOCOL
        result(rid, {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method in ("notifications/initialized", "initialized"):
        pass  # notification: no response
    elif method == "ping":
        if rid is not None:
            result(rid, {})
    elif method == "tools/list":
        result(rid, {"tools": [
            {k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS
        ]})
    elif method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            result(rid, {"content": [{"type": "text", "text": f"Error: unknown tool '{name}'"}],
                         "isError": True})
            return
        try:
            text = fn(args)
            result(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except RuntimeError as e:
            result(rid, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        except Exception as e:  # never let a tool call kill the server
            result(rid, {"content": [{"type": "text", "text": f"Error: unexpected failure in {name}: {e}"}],
                         "isError": True})
    elif method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        result(rid, {key: []})
    elif rid is not None:
        error(rid, -32601, f"method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print("agent-shield-mcp: ignoring non-JSON line", file=sys.stderr)
            continue
        try:
            handle(msg)
        except Exception as e:
            print(f"agent-shield-mcp: handler error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
