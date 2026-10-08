# Customer-cloud deployment runbook

Deploy Agent-Shield into the **customer's own cloud** so agent traffic never
leaves their network. Two supported paths. The services team runs either one
end-to-end; this document is also the self-serve guide.

## Path A — Cloudflare Workers (recommended)

The full product (REST `/v1/scan`, MCP server, signup, sensitivity, quotas)
deployed into the customer's Cloudflare account. Same code that runs the
hosted API.

### What the services team needs from the customer

- A Cloudflare API token, scoped to the target account, with:
  - Workers Scripts: Edit
  - Workers KV Storage: Edit
- (Optional) the zone added to their Cloudflare account if they want a
  custom domain; otherwise the deployment uses `*.workers.dev`.
- Token is time-boxed and revoked after handoff. We never ask for the
  account's Global API Key.

### Steps

```bash
git clone https://github.com/startekenterprises-ai/agent-shield
cd agent-shield/cloudflare-worker
npm install

# Authenticate as the customer (their token, their account)
export CLOUDFLARE_API_TOKEN='<customer-scoped-token>'

# One-time: KV namespace for API keys
wrangler kv:namespace create API_KEYS
# → paste the returned id into wrangler.toml [[kv_namespaces]] (binding API_KEYS)

# One-time: point the route at the customer's domain (or keep workers_dev)
# edit wrangler.toml: [route] pattern + zone_name

wrangler deploy
```

### Verify

```bash
curl https://<customer-domain>/health
# → {"ok":true,...}

# Provision a key and scan (replace with the customer's domain)
curl -X POST https://<customer-domain>/v1/signup \
  -H 'Content-Type: application/json' -d '{"label":"verify"}'
curl -X POST https://<customer-domain>/v1/scan \
  -H "X-API-Key: <key>" -H 'Content-Type: application/json' \
  -d '{"content":"My SSN is 078-05-1120","content_type":"text"}'
# → {"verdict":"malicious","findings":[{"class":"ssn","match_count":1}],...}
```

### Notes

- Quotas: free 1,000 / pro 100,000 scans/day per key (KV counters).
- Pro billing (Stripe) is optional and off by default; the deployment runs
  fully on free-tier keys until the customer enables it.
- KV contents: API key records only (`tier`, `label`, `created_at`,
  sensitivity, mode). Scan content is processed in memory and never stored;
  only a content hash and match counts are retained per scan.
- Detector updates: re-deploy from `main`; no data migration needed.

## Path B — Self-hosted Open WebUI Pipelines filter (on-prem / air-gapped)

For customers whose agents run on Open WebUI with Ollama. Ships as a
Pipelines filter (`pipelines/agent_shield_filter.py`).

- **Inlet:** snapshots request metadata + content hashes (never raw prompts),
  extracts SSRF-candidate URLs, enqueues for the background worker.
  Returns the body **unmodified**.
- **Outlet:** scans the in-memory response for PII/secret leak patterns,
  enqueues findings. Returns the body **unmodified**.
- **Background worker:** calls the local decision model (log-only),
  resolves SSRF candidate hostnames, writes analysis records.
- Hard rules: inlet/outlet never mutate the body and never raise;
  classification happens in the background worker, never synchronously.

Install: copy `pipelines/agent_shield_filter.py` into the Open WebUI
Pipelines directory, enable the filter, set valves per the file header.

## Services engagement model

1. **Scoping call (30 min).** Map agent workflows, choose Path A or B,
   agree success criteria (typically a 30-day pilot on one workflow).
2. **Access grant.** Customer provisions the least-privilege, time-boxed
   access above (Path A) or SSH/container access (Path B).
3. **Deploy + verify (1–2 weeks).** We deploy, run the verification
   checklist against the customer's traffic, tune sensitivity
   (`low`/`medium`/`high`, per-key or per-request) and detect/enforce mode,
   then hand off runbooks and docs and revoke our access.
4. **Operate.** Annual plan includes detector updates, re-deploys, and
   support. Starting at $12K/year per deployment.

## Handoff checklist

- [ ] `/health` returns ok on the customer domain
- [ ] Signup + scan verified end-to-end (benign and malicious samples)
- [ ] Sensitivity and mode (`detect`/`enforce`) set per customer policy
- [ ] Custom domain / route confirmed (Path A)
- [ ] Runbook + API docs delivered to customer team
- [ ] Services-team access revoked, revocation confirmed in writing
