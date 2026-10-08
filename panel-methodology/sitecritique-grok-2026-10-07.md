# Grok site critique — Agent-Shield website (2026-10-07)

## Core diagnosis
"Fix the identity + docs + benchmark framing and this becomes a credible early product. Leave them and it will be ignored by the exact audiences it is built for."

## Developer
Almost 5-minute start (instant key, simple scan). Missing: real docs surface (no curl/Python/JS blocks, error codes, rate limits, SDKs, changelog, versioning), thin OpenAPI (0.1.0 vs live heuristic_v5), demo doesn't show full JSON, no integration patterns (LangChain/CrewAI/MCP config), no status page.

## Buyer/manager
"Seatbelt" metaphor clear but: ZERO social proof (no logos, cases, testimonials, audits, team page — and Startek Enterprises googles as a small Florida consumer-electronics company = massive trust gap). Benchmarks without roadmap narrative = "commercially suicidal." No ROI framing, no "what happens when it flags" story. "Coming soon" on paid tier is vague.

## AI agent (strongest)
Above average: dedicated bot section, good llms.txt, clean manifest, no-email signup. Missing: performance caveats in machine files (agent over-estimates capability), full MCP tool schema, rate-limit/retry docs, robots.txt. GitHub describes a different product (local vs hosted confusion).

## The identity crisis (highest impact)
Site sells hosted scanner API; GitHub repo describes local-first privacy gateway/proxy with messy history (v1 aspirational claims, Phase 0 demolition). "Is the hosted API the same engine?" — kills trust instantly.

## Top 5 changes (ranked)
1. Fix product identity + credibility vacuum: site and GitHub tell same story; add About/Team/Company, contact path, at least one credibility signal.
2. Real developer docs + 5-min path: curl/Python/JS, full response schema, errors, rate limits, MCP schema, copy-paste agent snippet.
3. Reframe benchmarks: lead with deterministic wins (PII 100%, secrets ~80%, SSRF ~77%, zero FP benign); 4.7% in Tev1-roadmap context with timeline; "when to rely / not rely" box.
4. Status/trust footer: live status, privacy policy, terms, human contact, operational credibility.
5. Enrich machine surface + demo: performance caveats in llms.txt/manifest, full MCP schema, full-JSON demo, one-click key+curl flow.
