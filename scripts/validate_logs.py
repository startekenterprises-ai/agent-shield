#!/usr/bin/env python3
"""Validate Phase 1 logs: schema conformance + the no-raw-content invariant.

Checks every events*.jsonl(.gz) record:
  1. parses as JSON, carries schema_version == 1 and a known type;
  2. carries the required fields for its type (per schemas/log_schema_v1.json);
  3. contains NO raw-content fields (text/content/raw_text/raw_matches/raw)
     — hashes and metadata only. samples.jsonl is EXEMPT by design (0600,
     short retention; it is the only place raw content may exist).

Exit 0 when everything passes, 1 with a summary of violations otherwise.

Usage:
    python scripts/validate_logs.py --log-dir ./logs/agent-shield
"""

from __future__ import annotations

import argparse
import gzip
import glob
import json
import os
import sys

SCHEMA_VERSION = 1

KNOWN_TYPES = {
    "request", "response", "analysis", "ssrf_probe", "response_leak",
    "drop", "filter_error", "heartbeat", "label",
}

# Lightweight required-field lists (mirrors schemas/log_schema_v1.json).
REQUIRED: dict[str, list[str]] = {
    "request": ["schema_version", "type", "event_id", "ts", "direction",
                "model", "user_hash", "chat_hash", "turn_seq", "messages",
                "inlet_latency_us", "classifier"],
    "response": ["schema_version", "type", "event_id", "ts", "direction",
                 "model", "chars", "sha256", "outlet_latency_us",
                 "correlation", "classifier"],
    "analysis": ["schema_version", "type", "event_id", "ts", "direction",
                 "classifier", "classifier_latency_ms", "classifier_status",
                 "scores", "risk", "decision", "would_block", "would_redact",
                 "tev1_model", "model_digest", "systemone_contract_version",
                 "prompt_template_version", "classifier_config_hash"],
    "ssrf_probe": ["schema_version", "type", "event_id", "ts", "url_hash",
                   "host", "resolved_ips", "any_flag", "dns_status"],
    "response_leak": ["schema_version", "type", "event_id", "ts",
                      "pattern_class", "match_count", "redacted_in_log",
                      "direction"],
    "drop": ["schema_version", "type", "event_id", "ts", "reason",
             "dropped_direction", "total_dropped"],
    "filter_error": ["schema_version", "type", "event_id", "ts", "hook",
                     "error"],
    "heartbeat": ["schema_version", "type", "ts", "queue_depth",
                  "dropped_total", "classified_total", "error_total",
                  "uptime_s", "model_digest", "classifier_config_hash"],
    "label": ["schema_version", "type", "event_id", "ts", "label",
              "label_class", "source"],
}

# Fields that must NEVER appear in events files. (samples.jsonl is exempt.)
FORBIDDEN_RAW_FIELDS = {"text", "content", "raw_text", "raw_matches", "raw"}


def _has_forbidden(obj, path=""):
    """Recursively find forbidden raw-content keys. Returns list of paths."""
    hits = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in FORBIDDEN_RAW_FIELDS:
                hits.append(f"{path}.{key}" if path else key)
            hits.extend(_has_forbidden(value, f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            hits.extend(_has_forbidden(value, f"{path}[{i}]"))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log-dir", required=True)
    args = ap.parse_args()

    violations: list[str] = []
    counts: dict[str, int] = {}
    total = 0

    pattern = os.path.join(args.log_dir, "events*.jsonl*")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"no event files found in {args.log_dir}", file=sys.stderr)
        return 1

    for path in files:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                total += 1
                where = f"{os.path.basename(path)}:{lineno}"
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    violations.append(f"{where}: invalid JSON ({exc})")
                    continue
                if not isinstance(rec, dict):
                    violations.append(f"{where}: record is not an object")
                    continue
                if rec.get("schema_version") != SCHEMA_VERSION:
                    violations.append(
                        f"{where}: schema_version != {SCHEMA_VERSION}")
                rtype = rec.get("type")
                if rtype not in KNOWN_TYPES:
                    violations.append(f"{where}: unknown type {rtype!r}")
                    continue
                counts[rtype] = counts.get(rtype, 0) + 1
                missing = [f for f in REQUIRED[rtype] if f not in rec]
                if missing:
                    violations.append(
                        f"{where}: {rtype} missing fields {missing}")
                for hit in _has_forbidden(rec):
                    violations.append(
                        f"{where}: forbidden raw-content field '{hit}' "
                        f"in events log (raw text belongs in samples.jsonl)")
                # ssrf_probe must not carry a raw URL either.
                if rtype == "ssrf_probe" and "url" in rec:
                    violations.append(
                        f"{where}: ssrf_probe carries raw 'url' "
                        "(host + url_hash only)")

    print(f"validated {total} records across {len(files)} files")
    for rtype in sorted(counts):
        print(f"  {rtype}: {counts[rtype]}")
    if violations:
        print(f"\n{len(violations)} VIOLATIONS:", file=sys.stderr)
        for v in violations[:50]:
            print(f"  - {v}", file=sys.stderr)
        if len(violations) > 50:
            print(f"  ... and {len(violations) - 50} more", file=sys.stderr)
        return 1
    print("OK: schema-conformant, zero raw content in events logs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
