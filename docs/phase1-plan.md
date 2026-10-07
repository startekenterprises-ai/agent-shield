# Agent-Shield Phase 1 — logging-only inlet filter (implementation)

**Status:** implemented 2026-10-07 on branch `phase-1-logging-filter`.
Implements the panel-approved plan
(`workspace/goals/monday-morning-operator-brief/panel-chatgpt-agentshield-phase1-2026-10-05.md`)
plus both SSRF addenda (`workspace/agent-shield-ssrf-addenda-2026-10-06.md`,
approved by Glenn 2026-10-07).

**Scope (frozen):** observe, log, calibrate. No blocking, no redaction
enforcement, no fine-tuning, no distributed queues. Phase 1 output = a
labeled log corpus + `thresholds_v1.json` for Phase 2.

## Architecture

Open WebUI Pipelines filter (`pipelines/agent_shield_filter.py`,
`type = "filter"`). Attach per-model in Open WebUI (Admin → Models →
Filters) for scoped rollout.

```
user chat → Open WebUI → Pipelines → inlet() ── snapshot metadata+hashes
                                              ── enqueue (put_nowait)
                                              ── return body UNMODIFIED
          → LLM responds → outlet() ── inherit event_id via correlation map
                                    ── leak scan (Addendum B, in-memory)
                                    ── enqueue ── return body UNMODIFIED
          → background worker ── Tev1 classify (one call/event/direction)
                              ── DNS-resolve SSRF candidates (Addendum A)
                              ── write analysis / ssrf_probe records
```

## Decisions locked (from the panel session)

1. **Background worker, not sync classification.** Inlet snapshots and
   returns immediately (p99 target < 5 ms). Records join on `event_id`.
2. **Outlet logging enabled** (lightweight) — response-side signals are
   needed to calibrate Phase 2 response handling.
3. **Event correlation:** outlet inherits the inlet `event_id` via a bounded
   in-memory map keyed `(chat_hash, turn_seq)` (LRU, 10k entries). The outlet
   hook cannot know the turn sequence from the response body alone, so it
   resolves the **highest turn_seq seen for the chat_hash**; on miss it mints
   a fresh id and flags `correlation: "miss"` (e.g. filter restarted
   mid-conversation). New ids are generated in inlet only.
4. **Drop-newest on queue full:** the incoming snapshot gets the `drop`
   record (`reason: "queue_full"`); FIFO order of queued work is preserved
   so the calibration dataset is never silently biased.
5. **Coverage buckets:** `classified_ok / classifier_error
   (timeout|error|unavailable) / queue_dropped / malformed`, over
   `eligible = (request + response records) − malformed`. The 99% bar
   applies to `classified_ok / eligible`; every non-ok event has a matching
   drop/error record — no silent gaps.
6. **Provenance on every analysis/ssrf_probe record:** `tev1_model`, Ollama
   `model_digest`, `systemone_contract_version`, `prompt_template_version`,
   `classifier_config_hash` (sha256 of effective valves). Heartbeats repeat
   digest + config hash; `calibrate.py` refuses to mix provenance versions
   without `--allow-mixed-provenance`.

## Tev1 integration (log-only)

- Worker POSTs `{OLLAMA_BASE_URL}{SYSTEMONE_PATH}` with
  `{"model": "tev1:0.8b", "input": <role-tagged payload>}`.
- One call per event per direction; payload sections labeled
  `[system]` / `[user]` / `[assistant]` / `[tool]`, truncated oldest-first
  at `MAX_CLASSIFY_CHARS` (flagged).
- 5 s timeout, 1 retry on timeout → `timeout`/`error` status. Circuit
  breaker: 10 consecutive failures → 60 s cooldown (records become
  `unavailable`; heartbeats report `degraded`).
- Score normalization via `schemas/score_bands_v1.json`; `score_raw` and
  `normalization_version` always persisted.
- `decision` is always `"log"`; `would_block` (≥ 0.9) / `would_redact`
  (≥ 0.7) are provisional calibration labels, never acted on.

## SSRF addenda (approved 2026-10-07)

- **Addendum A (`ssrf_probe`):** inlet extracts candidate URLs from
  `tool_calls[].function.arguments` and tool-message text (bounded regex,
  max 50/event). Stored: `url_hash` (sha256 of URL with query/fragment
  stripped — secrets live in query strings), `host`, `host_port`, `source`.
  The worker resolves via `socket.getaddrinfo` (2 s timeout, in a thread)
  and flags IPs: `cloud_metadata` (169.254.0.0/16) → `loopback` →
  `private` → `link_local` → `reserved` → `multicast` → `public`.
  Honest limitation: log-time DNS can differ from what the agent hit
  (rebinding/TOCTOU) — a prevalence detector, not a per-request guarantee.
- **Addendum B (`response_leak`):** outlet scans the first 64 KB of the
  in-memory response for SSN / credit-card (+Luhn) / AWS key / GitHub token
  / PEM key patterns; writes `{pattern_class, match_count}` only —
  raw matches never leave memory. Directly measures the VA-server failure
  mode from CVE-2026-14540.

Both are additive records, stdlib-only, nothing in the hot path, and feed
`calibrate.py` prevalence context + cheap known-positive review labels.

## Log layout

- `events.jsonl` (+ daily `events-YYYYMMDD.jsonl.gz`, 30-day retention):
  `request` / `response` / `analysis` / `ssrf_probe` / `response_leak` /
  `label` / `drop` / `filter_error` / `heartbeat`. Hashes and metadata
  only — **no raw prompts or responses, ever** (enforced by
  `scripts/validate_logs.py`).
- `samples.jsonl` (mode 0600): the 1% random sample + red-team probe raw
  text. The ONLY place raw content may exist.

## Files

| Path | Purpose |
|---|---|
| `pipelines/agent_shield_filter.py` | The filter: inlet/outlet/worker/SSRF |
| `config/pipeline.example.yaml` | Valves reference (env overrides documented) |
| `schemas/log_schema_v1.json` | Record-type reference |
| `schemas/score_bands_v1.json` | Score normalization map |
| `scripts/calibrate.py` | Week of logs → `thresholds_v1.json` + report |
| `scripts/validate_logs.py` | Schema + no-raw-content validation |
| `scripts/redteam_probe.py` | Daily synthetic attacks → known-positive labels |
| `tests/test_filter_logonly.py` | Log-only guarantees, correlation, drops, SSRF |
| `tests/test_worker.py` | Worker fallback, breaker, provenance, labels |
| `docs/calibration-runbook.md` | Week-long run operations |
| `docs/tev1-contract.md` | Provisional `/v1/systemone` contract |

## Acceptance criteria (from the plan)

- [x] inlet/outlet never mutate the body, never raise (proven by tests)
- [ ] 7 consecutive days of logs; `classified_ok/eligible ≥ 99%` with
      error/drop/malformed buckets reported separately
- [ ] `calibrate.py` produces `thresholds_v1.json` + report (single
      provenance version or explicitly flagged mix)
- [x] `validate_logs.py` passes: schema-conformant, zero raw user prompts
      in `events.jsonl`

The unchecked items are operational — they complete after the
calibration-week run, not in this implementation.
