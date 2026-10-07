#!/usr/bin/env python3
"""Phase 1 calibration: one week of logs -> thresholds_v1.json + report.

Reads events*.jsonl(.gz) from the log dir, applies the provenance gate,
joins analysis records with ground-truth labels (red-team probes and
human labels.jsonl), sweeps thresholds per threat class, and picks
Phase 2 block/redact thresholds per the panel-approved bars:

  block_threshold  = highest t with FPR <= 0.1% on reviewed negatives
                     AND recall >= 99% on red-team positives
  redact_threshold = highest t with precision >= 95%

Any class that cannot meet its bar stays log-only in Phase 2 — no exceptions.

Deterministic: the sweep is pure arithmetic; the only sampling (human
review draw) is seeded. Exit 0 on success, 2 when labels are insufficient
for every class (still writes a report describing the gap).

Usage:
    python scripts/calibrate.py --log-dir ./logs/agent-shield \
        --out ./thresholds_v1.json --report ./calibration-report.md \
        [--labels labels.jsonl] [--allow-mixed-provenance] [--seed 42]
"""

from __future__ import annotations

import argparse
import gzip
import glob
import json
import math
import os
import random
import sys
from datetime import datetime, timezone

THREAT_CLASSES = (
    "prompt_injection",
    "indirect_injection",
    "secrets_exposure",
    "pii_disclosure",
    "poisoned_content",
)

FPR_BAR = 0.001
RECALL_BAR = 0.99
PRECISION_BAR = 0.95
MIN_LABELS_PER_CLASS = 5  # positives AND negatives required, else insufficient


def iter_records(log_dir):
    pattern = os.path.join(log_dir, "events*.jsonl*")
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def load_labels(log_dir, labels_path):
    """event_id -> list of (label_class, is_positive, source)."""
    labels: dict[str, list[tuple[str, bool, str]]] = {}

    def add(event_id, label_class, is_positive, source):
        labels.setdefault(event_id, []).append(
            (label_class, is_positive, source)
        )

    for rec in iter_records(log_dir):
        if rec.get("type") == "label":
            add(
                rec.get("event_id", ""),
                rec.get("label_class", ""),
                rec.get("label") == "positive",
                rec.get("source", "human"),
            )
    if labels_path and os.path.exists(labels_path):
        with open(labels_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                add(
                    rec.get("event_id", ""),
                    rec.get("label_class", ""),
                    rec.get("label") == "positive",
                    rec.get("source", "human"),
                )
    return labels


def roc_auc(pairs: list[tuple[float, bool]]) -> float | None:
    """Mann-Whitney U based AUC. None when undefined."""
    pos = sorted(s for s, p in pairs if p)
    neg = sorted(s for s, p in pairs if not p)
    if not pos or not neg:
        return None
    # rank all scores
    order = sorted(pairs, key=lambda x: x[0])
    rank_sum = 0.0
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j < n and order[j][0] == order[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            if order[k][1]:
                rank_sum += avg_rank
        i = j
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def sweep(pairs: list[tuple[float, bool, str]]):
    """Threshold sweep 0.00..1.00. Returns per-threshold metrics."""
    thresholds = [round(t * 0.01, 2) for t in range(0, 101)]
    out = []
    for t in thresholds:
        tp = fp = tn = fn = 0
        tp_rt = fn_rt = 0  # red-team positives only
        for score, positive, source in pairs:
            pred = score >= t
            if positive:
                if pred:
                    tp += 1
                    if source == "redteam":
                        tp_rt += 1
                else:
                    fn += 1
                    if source == "redteam":
                        fn_rt += 1
            else:
                if pred:
                    fp += 1
                else:
                    tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        recall_rt = (
            tp_rt / (tp_rt + fn_rt) if (tp_rt + fn_rt) else 0.0
        )
        out.append(
            {
                "threshold": t,
                "precision": precision,
                "recall": recall,
                "fpr": fpr,
                "recall_redteam": recall_rt,
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
            }
        )
    return out


def pick_thresholds(swept):
    block = None
    for row in reversed(swept):  # highest threshold first
        if row["fpr"] <= FPR_BAR and row["recall_redteam"] >= RECALL_BAR:
            block = row["threshold"]
            break
    redact = None
    for row in reversed(swept):
        if row["precision"] >= PRECISION_BAR:
            redact = row["threshold"]
            break
    return block, redact


def histogram(scores, bins=10):
    hist = [0] * bins
    for s in scores:
        idx = min(int(s * bins), bins - 1)
        hist[idx] += 1
    return hist


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--labels", default=None)
    ap.add_argument("--allow-mixed-provenance", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    records = list(iter_records(args.log_dir))
    analyses = [r for r in records if r.get("type") == "analysis"]
    requests = [r for r in records if r.get("type") == "request"]
    responses = [r for r in records if r.get("type") == "response"]

    # Coverage buckets (plan: classified_ok / classifier_error /
    # queue_dropped / malformed over eligible = request/response records
    # minus malformed — i.e. every classification opportunity).
    n_malformed = sum(1 for r in requests if r.get("malformed"))
    n_dropped = sum(1 for r in records if r.get("type") == "drop")
    n_err = sum(
        1
        for a in analyses
        if a.get("classifier_status") in ("error", "timeout", "unavailable")
    )
    n_ok = sum(1 for a in analyses if a.get("classifier_status") == "ok")
    # Eligible classification opportunities: one per request (inlet) plus one
    # per response (outlet), minus malformed requests. In live operation each
    # opportunity yields at most one analysis record, so coverage <= ~100%.
    eligible = (len(requests) - n_malformed) + len(responses)
    coverage = {
        "classified_ok": n_ok,
        "classifier_error": n_err,
        "queue_dropped": n_dropped,
        "malformed": n_malformed,
        "eligible": eligible,
        "coverage_pct": round(100.0 * n_ok / eligible, 2) if eligible else 0.0,
    }

    # Provenance gate.
    prov_keys = {
        (
            a.get("model_digest"),
            a.get("classifier_config_hash"),
            a.get("prompt_template_version"),
        )
        for a in analyses
    }
    mixed = len(prov_keys) > 1
    if mixed and not args.allow_mixed_provenance:
        print(
            "ERROR: mixed provenance versions in logs "
            f"({len(prov_keys)} distinct). Re-run with "
            "--allow-mixed-provenance to proceed explicitly.",
            file=sys.stderr,
        )
        return 1

    labels = load_labels(args.log_dir, args.labels)

    # SSRF / leak prevalence context (addenda records).
    ssrf = [r for r in records if r.get("type") == "ssrf_probe"]
    leaks = [r for r in records if r.get("type") == "response_leak"]
    prevalence = {
        "ssrf_probe_total": len(ssrf),
        "ssrf_flagged": sum(1 for r in ssrf if r.get("any_flag")),
        "response_leak_total": len(leaks),
        "response_leak_by_class": {},
    }
    for r in leaks:
        cls = r.get("pattern_class", "unknown")
        prevalence["response_leak_by_class"][cls] = (
            prevalence["response_leak_by_class"].get(cls, 0)
            + r.get("match_count", 0)
        )

    classes_out: dict = {}
    any_sufficient = False
    for cls in THREAT_CLASSES:
        pairs: list[tuple[float, bool, str]] = []
        for a in analyses:
            # Thresholds are computed over SCORED records only; error/timeout
            # records live in the classifier_error coverage bucket, not in
            # score distributions.
            if a.get("classifier_status") != "ok":
                continue
            for label_class, positive, source in labels.get(
                a.get("event_id", ""), []
            ):
                if label_class == cls:
                    pairs.append(
                        (float(a.get("scores", {}).get(cls, 0.0)),
                         positive,
                         source)
                    )
        n_pos = sum(1 for _, p, _ in pairs if p)
        n_neg = sum(1 for _, p, _ in pairs if not p)
        entry: dict = {
            "n_pos": n_pos,
            "n_neg": n_neg,
            "auc": roc_auc([(s, p) for s, p, _ in pairs]),
        }
        if n_pos < MIN_LABELS_PER_CLASS or n_neg < MIN_LABELS_PER_CLASS:
            entry.update(
                {
                    "status": "insufficient_labels",
                    "block_threshold": None,
                    "redact_threshold": None,
                }
            )
        else:
            any_sufficient = True
            swept = sweep(pairs)
            block_t, redact_t = pick_thresholds(swept)
            scores = [s for s, _, _ in pairs]
            entry.update(
                {
                    "status": (
                        "active"
                        if (block_t is not None or redact_t is not None)
                        else "log_only"
                    ),
                    "block_threshold": block_t,
                    "redact_threshold": redact_t,
                    "score_histogram_10": histogram(scores),
                }
            )
            best = max(swept, key=lambda r: r["precision"] + r["recall"])
            entry["best_balanced"] = {
                k: best[k]
                for k in ("threshold", "precision", "recall", "fpr")
            }
        classes_out[cls] = entry

    total_analysis = len(analyses) or 1
    expected_per_10k = {}
    for cls, entry in classes_out.items():
        for kind, t in (("block", entry.get("block_threshold")),
                        ("redact", entry.get("redact_threshold"))):
            if t is None:
                expected_per_10k[f"{cls}_{kind}"] = None
                continue
            hits = sum(
                1
                for a in analyses
                if float(a.get("scores", {}).get(cls, 0.0)) >= t
            )
            expected_per_10k[f"{cls}_{kind}"] = round(
                hits / total_analysis * 10_000, 1
            )

    thresholds = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance_mixed": mixed,
        "coverage": coverage,
        "prevalence": prevalence,
        "expected_per_10k_requests": expected_per_10k,
        "classes": classes_out,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(thresholds, fh, indent=2)

    lines = [
        "# Calibration report",
        "",
        f"Generated: {thresholds['generated_at']}",
        f"Log dir: {args.log_dir}",
        f"Provenance versions: {len(prov_keys)}"
        + (" (MIXED — allowed explicitly)" if mixed else ""),
        "",
        "## Coverage",
        "",
        f"- eligible requests: {eligible}",
        f"- classified_ok: {n_ok} "
        f"({coverage['coverage_pct']}%, bar is >= 99%)",
        f"- classifier_error: {n_err}",
        f"- queue_dropped: {n_dropped}",
        f"- malformed (excluded): {n_malformed}",
        "",
        "## SSRF / leak prevalence (addenda records)",
        "",
        f"- ssrf_probe records: {len(ssrf)}, "
        f"flagged: {prevalence['ssrf_flagged']}",
        f"- response_leak records: {len(leaks)} "
        f"{prevalence['response_leak_by_class']}",
        "",
        "## Per-class thresholds",
        "",
    ]
    for cls, entry in classes_out.items():
        lines.append(f"### {cls}")
        lines.append(f"- status: {entry['status']}")
        lines.append(
            f"- labels: {entry['n_pos']} positive / {entry['n_neg']} negative"
        )
        lines.append(f"- AUC: {entry['auc']}")
        lines.append(f"- block_threshold: {entry.get('block_threshold')}")
        lines.append(f"- redact_threshold: {entry.get('redact_threshold')}")
        if "score_histogram_10" in entry:
            lines.append(f"- score histogram (10 bins): {entry['score_histogram_10']}")
        lines.append("")
    lines += [
        "## Expected actions per 10k requests (at chosen thresholds)",
        "",
    ]
    for k, v in expected_per_10k.items():
        lines.append(f"- {k}: {v}")
    lines += [
        "",
        "## Bars applied",
        "",
        f"- block: highest t with FPR <= {FPR_BAR} on reviewed negatives "
        f"AND recall >= {RECALL_BAR} on red-team positives",
        f"- redact: highest t with precision >= {PRECISION_BAR}",
        "- any class missing its bar stays log-only in Phase 2 — no exceptions",
    ]
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"wrote {args.out} and {args.report}")
    print(f"coverage: {coverage['coverage_pct']}% ok over {eligible} eligible")
    return 0 if any_sufficient else 2


if __name__ == "__main__":
    sys.exit(main())
