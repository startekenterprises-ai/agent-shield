# Agent-Shield Cloudflare Worker (open core)

The hosted Agent-Shield connector: REST + MCP scan API, self-serve signup,
Stripe billing, and the public product site. Live at
https://agent-shield.startekenterprises.com.

## What's open here

- `src/index.ts` — Worker router: public site, signup, scan, MCP, billing webhook
- `src/detect.ts` — deterministic detection layer (heuristic_v5): prompt-injection
  signals, secret/PII patterns, SSRF URL classification. Raw matches never leave
  this module.
- `src/site.ts` — public site content: landing page, benchmarks, llms.txt, OpenAPI
- `openapi.yaml`, `llms.txt` — machine-readable API docs

## What's NOT here (closed, hosted-only)

- Tev1 decision-model weights and hosting
- Signed audit vault and billing/policy server internals
- The private hold-out eval set (dev benchmarks are public at `/benchmarks`)

## Open-core model

Self-host this worker for your own use. The hosted service adds the Tev1
classifier, signed audit trails, compliance tiers (Team/Enterprise), SLAs,
and managed billing. Detection benchmarks are published at
https://agent-shield.startekenterprises.com/benchmarks — including the misses.

## Deploy

```bash
npm install
# set your KV namespace id in wrangler.toml, then:
npx wrangler deploy
# secrets (never in repo): STRIPE_SECRET_KEY, STRIPE_PRICE_ID, STRIPE_WEBHOOK_SECRET
```
