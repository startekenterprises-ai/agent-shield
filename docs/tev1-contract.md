# Tev1 `/v1/systemone` contract (provisional)

**Status:** provisional — pin the exact shapes below on the first
successful call against the real endpoint, then update this file.
The filter is written defensively: unparseable responses become
`classifier_status: "error"` records (never silent-pass, never block).

## Endpoint

```
POST {OLLAMA_BASE_URL}/v1/systemone
Content-Type: application/json
```

## Request (what the worker sends)

```json
{
  "model": "tev1:0.8b",
  "input": "[system]\n<system text>\n[user]\n<user text>\n[assistant]\n<assistant text>\n[tool]\n<tool output text>"
}
```

- One call per event per direction (not per message).
- Sections labeled `[system]` / `[user]` / `[assistant]` / `[tool]`.
- Payload truncated oldest-first at `MAX_CLASSIFY_CHARS` (default 8000);
  `truncated: true` is recorded when truncation happened.

## Expected response shapes (tried in order)

1. `{"scores": {"prompt_injection": 0.91, "indirect_injection": 0.12, ...}}`
2. Flat: `{"prompt_injection": 0.91, ...}`
3. Band labels: `{"prompt_injection": "high", ...}` with
   `low/medium/high/critical → 0.25/0.5/0.75/1.0`
   (see `schemas/score_bands_v1.json`, `normalization_version: "bands/v1"`)

Threat classes: `prompt_injection`, `indirect_injection`,
`secrets_exposure`, `pii_disclosure`, `poisoned_content`.
Missing classes default to `0.0`; values are clamped to `[0, 1]`.

## Timeouts / retries

- 5 s per call (`CLASSIFIER_TIMEOUT_S`), exactly 1 retry on timeout,
  then `classifier_status: "timeout"`.
- Non-2xx or transport failure → `"error"`.
- 10 consecutive failures → 60 s circuit-breaker cooldown; records become
  `"unavailable"` and heartbeats report `"degraded": true`.

## What to pin on first success

1. Exact response JSON shape (update `_normalize_scores` if needed).
2. Whether scores are calibrated probabilities or raw magnitudes
   (affects `score_bands_v1.json`, not the code path).
3. Observed p50/p95 latency at `WORKER_CONCURRENCY=4` (tune valve).
4. The Ollama `model_digest` for the provenance records.

## Notes

- Tev1 models are *decision* models (classification), not chat models —
  do not send them conversational prompts expecting text back.
- No fine-tuning in Phase 1 (panel rule): thresholds only, derived from
  the log-only week via `scripts/calibrate.py`.
