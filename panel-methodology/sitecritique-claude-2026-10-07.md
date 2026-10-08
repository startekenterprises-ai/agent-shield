# Claude site critique — Agent-Shield website (2026-10-07, grounded on actual page text)

## Core problem
Landing promises "one poisoned page can't hijack your whole workflow"; benchmarks show 4.7% unseen-attack recall. "The product that works today is a deterministic scanner for secrets, SSNs/cards, and SSRF URLs. Prompt injection is the headline, but it's the weakest part."

## Factual errors to fix (benchmarks page)
- 460 vs 465 samples in different places; methodology 334/126 vs table sums 340/120
- FP counts: table implies 15, text says 16, 18.8% FPR implies ~24
- "~4% across 220 injection samples" mixes two datasets (220 own-set ≠ 211 hold-out)
- Secrets "75%" text vs 80% table (32/40); 75% is hold-out (3/4)
- Stale: headline v4, roadmap says v3/three iterations
- "100% precision" on 8 detections isn't meaningful
- Three company names across pages
- "Independent three-AI panel" reads as credulous; citing LLMs as authority weakens credibility
- "Available on request" contradicts "published, not cherry-picked"; no dataset to download
- Tev1 never explained to outsiders

## Privacy (serious)
- "We never see your content" is FALSE — content is sent to the server. Correct: "processed in memory, not stored."
- Unsalted SHA-256 of content is brute-forceable for SSNs/cards; use keyed HMAC or don't store.
- "Exposed PII" = only SSNs + cards (no emails/phones/names); "Leaked secrets" = 3 formats. Labels overclaim.

## Developer
- 0.64s median latency is slow for a per-action call and suspicious for regex — investigate/publish corrected number
- Findings lack location offsets; add optional offsets without matched text
- Cut unverified "Muse" and "ChatGPT Dots" compatibility claims
- Demo: add presets including ones you don't catch
- "Clean" is a dangerous word: with 95% of unseen attacks scoring clean, agents following "act unless malicious/suspicious" are reassured wrongly. Rename to no_known_pattern + "clean does not mean safe" in llms.txt/OpenAPI/manifest
- Agent block written as imperatives — careful agents treat webpage imperatives as possible injection; keep human page descriptive, imperatives in llms.txt/spec

## Top 5 (ranked)
1. Landing matches benchmarks; lead with what works; label semantic detection "baseline ~5%, classifier in development"; rename "clean" verdict
2. Benchmarks consistent + verifiable: reconcile numbers, publish per-sample CSV + critiques, CIs, plain-English summary, stop calling panel "independent"
3. Privacy claims corrected + legal basics: in-memory-not-stored, keyed HMAC, Privacy Policy + Terms, one company name, human contact
4. Real developer path: copyable curl, full schema, content_type list, limits, errors, rate-limit headers, engine_version, latency fix, MCP config
5. Pricing honesty: Pro sells 100× quota + receipts + "coming soon" classifier while OSS covers working detectors — state what hosted adds or waitlist until classifier ships

"If you only have time for two, do #1 and #3."
