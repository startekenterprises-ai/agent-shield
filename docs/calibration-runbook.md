# Calibration runbook — the log-only week

Operational guide for the 7-day Phase 1 observation run. Goal: a labeled
corpus + `thresholds_v1.json` that Phase 2 can trust.

## Before day 1

1. **Ollama pinned.** Keep the OMEN's Ollama on the stable release that
   serves Tev1 (do NOT follow release candidates during the week — a
   mid-week model change invalidates the provenance gate).
2. **Pull Tev1:** `ollama pull tev1:0.8b`. Verify:
   `curl -s localhost:11434/api/show -d '{"name":"tev1:0.8b"}'`.
3. **Install the filter** in Open WebUI Pipelines (`pipelines/`
   directory), attach it to the target model(s) under Admin → Models →
   Filters. Start with ONE model to validate, then expand.
4. **Valves:** set `LOG_DIR` to a volume with headroom (~500 MB/week at
   10k turns/day). Confirm `AGENTSHIELD_OLLAMA_BASE_URL` reaches Ollama
   from the Pipelines container.
5. **Smoke test:** send one chat, then check `events.jsonl` has a
   `request` record and the heartbeat appears within 60 s. Run
   `scripts/validate_logs.py --log-dir <dir>` — must print OK.

## Daily (7 days)

- **Red-team probes:** `python scripts/redteam_probe.py --log-dir <dir>`
  (adds known-positive labels for every class, incl. the two SSRF cases).
- **Validate:** `scripts/validate_logs.py --log-dir <dir>` — any violation
  is a bug; fix before the week counts.
- **Watch the heartbeat:** `queue_depth` should hover near 0;
  `dropped_total` should stay 0 (if it climbs, raise `QUEUE_SIZE`);
  `degraded: true` means Ollama is down — fix it, the week needs
  `classified_ok` coverage.
- **Disk:** ~60 MB/day at 10k turns/day. `samples.jsonl` stays small
  (1% sample, 0600 perms).

## After day 7

1. **Human review draw:** `calibrate.py` expects labels. Review ALL events
   with `risk >= 0.5` plus a random 2% below (catches false negatives);
   write TP/FP per class to `labels.jsonl`:
   `{"event_id": "...", "label": "positive"|"negative",
     "label_class": "...", "source": "human"}`.
   Every `ssrf_probe` with `any_flag: true` and every `response_leak`
   gets reviewed (cheap known-positive sources).
2. **Calibrate:**
   `python scripts/calibrate.py --log-dir <dir> --labels labels.jsonl \
     --out thresholds_v1.json --report calibration-report.md`
   If it refuses on mixed provenance, do NOT pass
   `--allow-mixed-provenance` blindly — find what changed mid-week
   (model digest or valve edit) and decide whether the week is valid.
3. **Read the report:** per-class thresholds, expected blocks/redactions
   per 10k, FP budget. Any class that missed its bar stays log-only in
   Phase 2 — documented, no exceptions.
4. **FP case notes:** every false positive on real traffic gets a note →
   regression corpus for Phase 2.

## If something goes wrong mid-week

- **Ollama dies:** the filter keeps serving (log-only, never blocks);
  records land as `classifier_error`. Fix Ollama; the week is still
  usable if `classified_ok/eligible >= 99%` overall.
- **Disk fills:** rotation + 30-day retention handle the steady state;
  for an emergency, lower `RAW_SAMPLE_RATE` to 0 — never delete
  `events.jsonl` mid-week.
- **Valve change needed:** any valve edit changes `classifier_config_hash`
  and splits provenance. Prefer to finish the week first; if you must
  change, note the date — calibration will gate on it.
