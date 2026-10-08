/**
 * Agent-Shield Connector + public site — Cloudflare Worker.
 *
 * Security API (one detection core, see detect.ts):
 *   POST /v1/scan  (alias: POST /scan) — REST scan
 *   POST /mcp      — MCP Streamable HTTP: initialize / tools/list / tools/call
 *   GET  /health   — unauthenticated liveness
 *
 * Self-serve site (no human required):
 *   GET  /                              — landing page
 *   GET  /llms.txt                      — agent-readable docs
 *   GET  /openapi.yaml                  — machine-readable API spec
 *   GET  /.well-known/agent-shield.json — service manifest
 *   POST /v1/signup                     — mint an API key (no auth, no email required)
 *   POST /v1/subscribe                  — upgrade to Pro via Stripe Checkout
 *
 * Auth: X-API-Key header, validated against the API_KEYS KV namespace.
 * Rate limits: free 1,000 scans/day, pro 100,000/day (KV counters).
 *
 * INVARIANTS:
 *  - Raw request content is NEVER logged or persisted — only the sha256
 *    content hash and aggregate match counts leave this Worker.
 *  - Findings carry { class, match_count } — never matched text.
 */

import { scanContent, ScanResult, sha256Hex } from "./detect";
import {
  LANDING_HTML,
  LLMS_TXT,
  OPENAPI_YAML,
  BENCHMARKS_HTML,
  manifestJson,
} from "./site";

export interface Env {
  API_KEYS: KVNamespace;
  ENVIRONMENT?: string;
  STRIPE_SECRET_KEY?: string;
  STRIPE_PRICE_ID?: string;
  STRIPE_WEBHOOK_SECRET?: string;
}

const JSON_HEADERS = { "Content-Type": "application/json" };
const HTML_HEADERS = { "Content-Type": "text/html; charset=utf-8" };

const QUOTAS: Record<string, number> = { free: 1000, pro: 100000 };

// ---------------------------------------------------------------------------
// Auth + quota
// ---------------------------------------------------------------------------

interface KeyRecord {
  tier: string; // "free" | "pro"
  label?: string;
  email?: string;
  created_at?: string;
}

async function authenticate(req: Request, env: Env): Promise<KeyRecord | null> {
  const key = req.headers.get("X-API-Key");
  if (!key || key.length < 8 || key.length > 256) return null;
  const raw = await env.API_KEYS.get(key);
  if (!raw) return null;
  try {
    const rec = JSON.parse(raw) as KeyRecord;
    if (typeof rec.tier !== "string") return null;
    return rec;
  } catch {
    return null;
  }
}

function unauthorized(): Response {
  return new Response(JSON.stringify({ error: "unauthorized" }), {
    status: 401,
    headers: JSON_HEADERS,
  });
}

function dayStamp(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Returns true if the call is within quota, incrementing the daily counter. */
async function checkQuota(key: string, tier: string, env: Env): Promise<boolean> {
  const limit = QUOTAS[tier] ?? QUOTAS.free;
  const counter = `rl:${key}:${dayStamp()}`;
  const raw = await env.API_KEYS.get(counter);
  const used = raw ? parseInt(raw, 10) || 0 : 0;
  if (used >= limit) return false;
  await env.API_KEYS.put(counter, String(used + 1), { expirationTtl: 172800 });
  return true;
}

// ---------------------------------------------------------------------------
// Scan handler (shared by REST + MCP)
// ---------------------------------------------------------------------------

async function handleScan(body: unknown): Promise<Response> {
  const b = (body ?? {}) as Record<string, unknown>;
  const content = typeof b.content === "string" ? b.content : "";
  const contentType = typeof b.content_type === "string" ? b.content_type : "text";
  if (!content) {
    return new Response(JSON.stringify({ error: "content is required" }), {
      status: 400,
      headers: JSON_HEADERS,
    });
  }
  if (content.length > 200_000) {
    return new Response(JSON.stringify({ error: "content exceeds 200k char limit" }), {
      status: 413,
      headers: JSON_HEADERS,
    });
  }
  const result: ScanResult = await scanContent(content, contentType);
  return new Response(JSON.stringify(result), { status: 200, headers: JSON_HEADERS });
}

// ---------------------------------------------------------------------------
// Signup — agent-friendly, minimal friction
// ---------------------------------------------------------------------------

async function handleSignup(req: Request, env: Env): Promise<Response> {
  let body: Record<string, unknown> = {};
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    /* empty body is fine */
  }
  const label =
    typeof body.label === "string" && body.label.length <= 120
      ? body.label
      : "unlabeled";
  const email =
    typeof body.email === "string" && body.email.length <= 254
      ? body.email
      : undefined;

  const rand = [...crypto.getRandomValues(new Uint8Array(18))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const apiKey = `as_live_${rand}`;
  const rec: KeyRecord = {
    tier: "free",
    label,
    created_at: new Date().toISOString(),
    ...(email ? { email } : {}),
  };
  await env.API_KEYS.put(apiKey, JSON.stringify(rec));
  return new Response(
    JSON.stringify({
      api_key: apiKey,
      tier: "free",
      quota_per_day: QUOTAS.free,
      scan_url: "https://agent-shield.startekenterprises.com/v1/scan",
      mcp_url: "https://agent-shield.startekenterprises.com/mcp",
      docs: "https://agent-shield.startekenterprises.com/llms.txt",
    }),
    { status: 200, headers: JSON_HEADERS },
  );
}

// ---------------------------------------------------------------------------
// Subscribe — Stripe Checkout for Pro
// ---------------------------------------------------------------------------

async function handleSubscribe(req: Request, env: Env): Promise<Response> {
  const key = req.headers.get("X-API-Key") ?? "";
  const rec = await authenticate(req, env);
  if (!rec) return unauthorized();
  if (!env.STRIPE_SECRET_KEY || !env.STRIPE_PRICE_ID) {
    return new Response(
      JSON.stringify({
        error: "billing_not_configured",
        message:
          "Pro billing is not live yet. Your free tier (1,000 scans/day) keeps working — check back soon.",
      }),
      { status: 501, headers: JSON_HEADERS },
    );
  }
  const keyHash = await sha256Hex(key);
  const clientRef = keyHash.slice(0, 32);
  // Map the checkout session back to this API key when the webhook fires.
  await env.API_KEYS.put(`stripe_ref:${clientRef}`, key, { expirationTtl: 2592000 });
  const params = new URLSearchParams({
    mode: "subscription",
    "line_items[0][price]": env.STRIPE_PRICE_ID,
    "line_items[0][quantity]": "1",
    success_url: "https://agent-shield.startekenterprises.com/?upgraded=1",
    cancel_url: "https://agent-shield.startekenterprises.com/#pricing",
    client_reference_id: clientRef,
  });
  const resp = await fetch("https://api.stripe.com/v1/checkout/sessions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params.toString(),
  });
  if (!resp.ok) {
    return new Response(
      JSON.stringify({ error: "checkout_failed", message: "Could not create checkout session." }),
      { status: 502, headers: JSON_HEADERS },
    );
  }
  const session = (await resp.json()) as { url?: string };
  return new Response(JSON.stringify({ checkout_url: session.url }), {
    status: 200,
    headers: JSON_HEADERS,
  });
}

// ---------------------------------------------------------------------------
// Stripe webhook — auto-upgrade/downgrade on subscription events
// ---------------------------------------------------------------------------

async function verifyStripeSignature(
  rawBody: string,
  header: string | null,
  secret: string,
): Promise<boolean> {
  if (!header) return false;
  const parts: Record<string, string> = {};
  for (const kv of header.split(",")) {
    const [k, v] = kv.split("=");
    if (k && v) parts[k.trim()] = v.trim();
  }
  const t = parts["t"];
  const v1 = parts["v1"];
  if (!t || !v1) return false;
  // Reject stale webhooks (10 min tolerance).
  if (Math.abs(Date.now() / 1000 - parseInt(t, 10)) > 600) return false;
  const signedPayload = `${t}.${rawBody}`;
  const keyData = new TextEncoder().encode(secret);
  const msgData = new TextEncoder().encode(signedPayload);
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, msgData);
  const expected = [...new Uint8Array(sig)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  // Constant-time comparison.
  if (expected.length !== v1.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ v1.charCodeAt(i);
  }
  return diff === 0;
}

async function handleStripeWebhook(req: Request, env: Env): Promise<Response> {
  if (!env.STRIPE_WEBHOOK_SECRET) {
    return new Response(JSON.stringify({ error: "webhook_not_configured" }), {
      status: 501,
      headers: JSON_HEADERS,
    });
  }
  const rawBody = await req.text();
  const ok = await verifyStripeSignature(
    rawBody,
    req.headers.get("Stripe-Signature"),
    env.STRIPE_WEBHOOK_SECRET,
  );
  if (!ok) {
    return new Response(JSON.stringify({ error: "invalid_signature" }), {
      status: 400,
      headers: JSON_HEADERS,
    });
  }
  let event: { type?: string; data?: { object?: Record<string, unknown> } };
  try {
    event = JSON.parse(rawBody);
  } catch {
    return new Response(JSON.stringify({ error: "invalid JSON" }), {
      status: 400,
      headers: JSON_HEADERS,
    });
  }

  const obj = event.data?.object ?? {};
  const clientRef =
    typeof obj.client_reference_id === "string" ? obj.client_reference_id : "";

  if (event.type === "checkout.session.completed" && clientRef) {
    const apiKey = await env.API_KEYS.get(`stripe_ref:${clientRef}`);
    if (apiKey) {
      const raw = await env.API_KEYS.get(apiKey);
      if (raw) {
        const rec = JSON.parse(raw) as KeyRecord;
        rec.tier = "pro";
        await env.API_KEYS.put(apiKey, JSON.stringify(rec));
      }
    }
  }

  if (event.type === "customer.subscription.deleted") {
    // Best-effort downgrade: find keys whose stripe customer matches.
    // v1: downgrade via client_reference_id if present on the subscription.
    const subRef =
      typeof obj.client_reference_id === "string" ? obj.client_reference_id : "";
    if (subRef) {
      const apiKey = await env.API_KEYS.get(`stripe_ref:${subRef}`);
      if (apiKey) {
        const raw = await env.API_KEYS.get(apiKey);
        if (raw) {
          const rec = JSON.parse(raw) as KeyRecord;
          rec.tier = "free";
          await env.API_KEYS.put(apiKey, JSON.stringify(rec));
        }
      }
    }
  }

  return new Response(JSON.stringify({ received: true }), {
    status: 200,
    headers: JSON_HEADERS,
  });
}

// ---------------------------------------------------------------------------
// MCP (Streamable HTTP, JSON-RPC 2.0). POST-only in v1.
// ---------------------------------------------------------------------------

const SHIELD_SCAN_TOOL = {
  name: "shield.scan",
  description:
    "Scan text content for security threats before acting on it: prompt " +
    "injection, leaked secrets/API keys, exposed PII, and SSRF-risk URLs. " +
    "Returns a verdict (clean/suspicious/malicious) with per-class scores " +
    "and findings. Raw content is never retained — only hashes and counts.",
  inputSchema: {
    type: "object",
    properties: {
      content: {
        type: "string",
        description: "The text to scan (prompt, tool output, email, webpage, ...).",
      },
      content_type: {
        type: "string",
        description: "Hint about the content kind.",
        enum: ["text", "prompt", "tool_output", "email", "webpage"],
        default: "text",
      },
    },
    required: ["content"],
    additionalProperties: false,
  },
};

interface JsonRpc {
  jsonrpc?: string;
  id?: string | number | null;
  method?: string;
  params?: Record<string, unknown>;
}

function rpcResult(id: unknown, result: unknown): Response {
  return new Response(JSON.stringify({ jsonrpc: "2.0", id: id ?? null, result }), {
    status: 200,
    headers: JSON_HEADERS,
  });
}

function rpcError(id: unknown, code: number, message: string): Response {
  return new Response(
    JSON.stringify({ jsonrpc: "2.0", id: id ?? null, error: { code, message } }),
    { status: 200, headers: JSON_HEADERS },
  );
}

async function handleMcp(req: Request): Promise<Response> {
  let msg: JsonRpc;
  try {
    msg = (await req.json()) as JsonRpc;
  } catch {
    return rpcError(null, -32700, "parse error");
  }
  const id = msg.id ?? null;

  switch (msg.method) {
    case "initialize":
      return rpcResult(id, {
        protocolVersion: "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: { name: "agent-shield", version: "0.2.0" },
      });

    case "notifications/initialized":
      return new Response(null, { status: 202 });

    case "tools/list":
      return rpcResult(id, { tools: [SHIELD_SCAN_TOOL] });

    case "tools/call": {
      const params = msg.params ?? {};
      if (params.name !== "shield.scan") {
        return rpcError(id, -32602, `unknown tool: ${String(params.name)}`);
      }
      const args = (params.arguments ?? {}) as Record<string, unknown>;
      const scanResp = await handleScan(args);
      const result = (await scanResp.json()) as unknown;
      if (scanResp.status !== 200) {
        return rpcResult(id, {
          content: [{ type: "text", text: JSON.stringify(result) }],
          isError: true,
        });
      }
      return rpcResult(id, {
        content: [{ type: "text", text: JSON.stringify(result) }],
        isError: false,
      });
    }

    default:
      return rpcError(id, -32601, `method not found: ${String(msg.method)}`);
  }
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

async function authedScan(
  req: Request,
  env: Env,
  body: unknown,
): Promise<Response> {
  const key = req.headers.get("X-API-Key") ?? "";
  const rec = await authenticate(req, env);
  if (!rec) return unauthorized();
  if (!(await checkQuota(key, rec.tier, env))) {
    return new Response(
      JSON.stringify({
        error: "quota_exceeded",
        message: `Daily scan quota exceeded (${QUOTAS[rec.tier] ?? QUOTAS.free}/day). Upgrade: POST /v1/subscribe`,
      }),
      { status: 429, headers: JSON_HEADERS },
    );
  }
  return handleScan(body);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const p = url.pathname;

    // --- Public site ---
    if (req.method === "GET" && (p === "/" || p === "/index.html")) {
      return new Response(LANDING_HTML, { status: 200, headers: HTML_HEADERS });
    }
    if (req.method === "GET" && p === "/benchmarks") {
      return new Response(BENCHMARKS_HTML, { status: 200, headers: HTML_HEADERS });
    }
    if (req.method === "GET" && p === "/llms.txt") {
      return new Response(LLMS_TXT, {
        status: 200,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      });
    }
    if (req.method === "GET" && p === "/openapi.yaml") {
      return new Response(OPENAPI_YAML, {
        status: 200,
        headers: { "Content-Type": "text/yaml; charset=utf-8" },
      });
    }
    if (req.method === "GET" && p === "/.well-known/agent-shield.json") {
      return new Response(manifestJson(), { status: 200, headers: JSON_HEADERS });
    }
    if (req.method === "GET" && p === "/health") {
      return new Response(
        JSON.stringify({ status: "ok", analyzer: "heuristic_v5", service: "agent-shield" }),
        { status: 200, headers: JSON_HEADERS },
      );
    }

    // --- Signup / billing ---
    if (req.method === "POST" && p === "/v1/signup") {
      return handleSignup(req, env);
    }
    if (req.method === "POST" && p === "/v1/subscribe") {
      return handleSubscribe(req, env);
    }
    if (req.method === "POST" && p === "/v1/stripe-webhook") {
      return handleStripeWebhook(req, env);
    }

    // --- Authenticated scan (REST + legacy alias) ---
    if (req.method === "POST" && (p === "/v1/scan" || p === "/scan")) {
      let body: unknown;
      try {
        body = await req.json();
      } catch {
        return new Response(JSON.stringify({ error: "invalid JSON" }), {
          status: 400,
          headers: JSON_HEADERS,
        });
      }
      return authedScan(req, env, body);
    }

    // --- MCP ---
    if (p === "/mcp") {
      if (req.method === "GET") {
        return new Response(JSON.stringify({ error: "use POST for JSON-RPC" }), {
          status: 405,
          headers: { ...JSON_HEADERS, Allow: "POST" },
        });
      }
      if (req.method === "POST") {
        const key = req.headers.get("X-API-Key") ?? "";
        const rec = await authenticate(req, env);
        if (!rec) return unauthorized();
        if (!(await checkQuota(key, rec.tier, env))) {
          return rpcError(null, -32000, "daily quota exceeded");
        }
        return handleMcp(req);
      }
      return new Response("method not allowed", { status: 405 });
    }

    return new Response(JSON.stringify({ error: "not found" }), {
      status: 404,
      headers: JSON_HEADERS,
    });
  },
};
