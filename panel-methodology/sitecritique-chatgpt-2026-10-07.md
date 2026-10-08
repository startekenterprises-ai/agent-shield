# ChatGPT site critique — Agent-Shield website (2026-10-07)

Scores: AI agent 8/10, Developer 6.5/10, Buyer/manager 4.5/10.

## Core diagnosis
Positioning problem, not a website problem. Homepage sells broad prompt-injection defense; benchmarks prove the heuristic engine isn't one. "So one poisoned page can't hijack your whole workflow" vs 4.7% unseen-attack recall = serious tension.

## Developer
Good: first 60 seconds strong — endpoint, auth, signup, scan, example response, MCP, OpenAPI, free quota. Bad: "API docs" aren't docs (missing: all request fields, content_type values, max size, status codes, finding-class glossary, score semantics, suspicious-vs-malicious thresholds, rate limits, key rotation). Missing: copy/paste quickstart that actually runs. Inconsistency: llms.txt documents heuristic_v1 response shape while benchmarks say production is heuristic_v4.

## Buyer/manager (weakest)
"Seatbelt for AI agents" memorable but insufficient. Needs concrete scenario ("your agent visits a webpage with hidden instructions..."). Missing business-outcome boxes (Prevent/Integrate/Control) and explicit "what we do NOT do" statement. Pro $19 value prop unclear ("hosted AI classifier (coming soon)" = paying for receipts + support).

## AI agent (strongest)
Genuinely differentiated: signup, auth, scan, schemas, MCP, OpenAPI, llms.txt, manifest. Missing: machine-readable verdict semantics (malicious → do_not_act), max input, rate limits, error schema, content_type values, detector version, MCP tool schema. Trust risk: autonomous signup with no documented abuse controls.

## Top 5 changes (ranked)
1. Reposition around demonstrated capability: "lightweight security API for leaked secrets, exposed PII, SSRF-risk URLs, and known injection patterns" + label semantic detector as developing.
2. 5-minute quickstart under hero: curl signup → curl scan → real JSON response; language tabs.
3. Benchmarks as trust asset: visually split Deterministic (PII 40/40, secrets 32/40, SSRF 23/30) vs Semantic (internal ~59%, holdout 4.7%); lead with "we publish the misses."
4. /security trust page: retention, logging, encryption, key rotation, rate limits, abuse prevention, residency, incident response, availability, self-hosting.
5. Machine interface first-class: expand manifest + llms.txt (version, capabilities, limits, errors, verdict semantics, finding classes, MCP schema); fix version inconsistencies.
