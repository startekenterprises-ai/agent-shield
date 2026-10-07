"""Worker unit tests: queue bounds, Ollama-down fallback, circuit breaker,
provenance stamping, and provisional would_block/would_redact labels.

The worker never raises into the request path: every failure mode ends as
an analysis record with a non-ok classifier_status.
"""

import asyncio
import json
import os

import httpx
import pytest

from pipelines.agent_shield_filter import Pipeline

TEST_VALVES = {
    "OLLAMA_BASE_URL": "http://127.0.0.1:1",  # always refused, fast
    "CLASSIFIER_TIMEOUT_S": 0.5,
    "QUEUE_SIZE": 100,
    "RAW_SAMPLE_RATE": 0.0,
}


def make_pipeline(tmp_path, **overrides):
    valves = dict(TEST_VALVES, LOG_DIR=str(tmp_path))
    valves.update(overrides)
    return Pipeline(valves=valves)


def read_events(tmp_path):
    path = os.path.join(str(tmp_path), "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def by_type(records, rtype):
    return [r for r in records if r.get("type") == rtype]


def inlet_snapshot(event_id="evt-1"):
    return {
        "event_id": event_id,
        "ts": "2026-10-07T00:00:00.000+00:00",
        "direction": "inlet",
        "model": "m",
        "user_hash": "u",
        "chat_hash": "c",
        "turn_seq": 1,
        "stream": False,
        "texts": [("user", "hello world")],
        "candidate_urls": [],
        "total_chars": 11,
    }


@pytest.mark.asyncio
async def test_ollama_down_writes_error_analysis_never_raises(tmp_path):
    p = make_pipeline(tmp_path)
    await p._process_snapshot("inlet", inlet_snapshot())
    analyses = by_type(read_events(tmp_path), "analysis")
    assert len(analyses) == 1
    a = analyses[0]
    assert a["classifier_status"] in ("error", "timeout", "unavailable")
    assert a["decision"] == "log"  # log-only even on failure
    assert a["would_block"] is False
    assert a["would_redact"] is False


@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_sustained_failures(tmp_path):
    p = make_pipeline(tmp_path)
    # Simulate 10 consecutive failures without network.
    for _ in range(10):
        p._register_failure()
    assert p._consec_failures == 10
    # Next classification short-circuits: no network, status unavailable.
    analysis = await p._classify(
        inlet_snapshot(), "[user]\nhello", False, 11, "inlet"
    )
    assert analysis["classifier_status"] == "unavailable"
    assert analysis["scores"] == {c: 0.0 for c in
        ("prompt_injection", "indirect_injection", "secrets_exposure",
         "pii_disclosure", "poisoned_content")}
    # A success resets the breaker.
    p._consec_failures = 0
    p._cooldown_until = 0.0


@pytest.mark.asyncio
async def test_provenance_stamped_on_every_analysis(tmp_path):
    p = make_pipeline(tmp_path)
    await p._process_snapshot("inlet", inlet_snapshot())
    (a,) = by_type(read_events(tmp_path), "analysis")
    assert a["tev1_model"] == "tev1:0.8b"
    assert a["systemone_contract_version"] == "systemone/v1"
    assert a["prompt_template_version"] == "prompt/v1"
    assert len(a["classifier_config_hash"]) == 64
    assert "model_digest" in a  # "unknown" when Ollama is down


@pytest.mark.asyncio
async def test_would_block_labels_from_canned_scores(tmp_path, monkeypatch):
    p = make_pipeline(tmp_path)

    async def fake_post(payload):
        return {
            "prompt_injection": 0.95,   # >= 0.9 -> would_block
            "indirect_injection": 0.75,  # >= 0.7 -> would_redact only
            "secrets_exposure": 0.1,
            "pii_disclosure": 0.2,
            "poisoned_content": 0.05,
        }

    monkeypatch.setattr(p, "_post_systemone", fake_post)
    await p._process_snapshot("inlet", inlet_snapshot())
    (a,) = by_type(read_events(tmp_path), "analysis")
    assert a["classifier_status"] == "ok"
    assert a["risk"] == pytest.approx(0.95)
    assert a["would_block"] is True
    assert a["would_redact"] is True
    assert a["score_raw"]["prompt_injection"] == 0.95
    assert a["normalization_version"] == "bands/v1"


@pytest.mark.asyncio
async def test_band_labels_normalized(tmp_path, monkeypatch):
    p = make_pipeline(tmp_path)

    async def fake_post(payload):
        return {"prompt_injection": "high", "secrets_exposure": "low"}

    monkeypatch.setattr(p, "_post_systemone", fake_post)
    await p._process_snapshot("inlet", inlet_snapshot())
    (a,) = by_type(read_events(tmp_path), "analysis")
    assert a["scores"]["prompt_injection"] == pytest.approx(0.75)
    assert a["scores"]["secrets_exposure"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_worker_loop_drains_queue_then_cancelled(tmp_path):
    p = make_pipeline(tmp_path)
    for i in range(3):
        p._queue.put_nowait(("inlet", inlet_snapshot(f"evt-{i}")))
    task = asyncio.create_task(p._worker_loop())
    await asyncio.sleep(1.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    analyses = by_type(read_events(tmp_path), "analysis")
    assert len(analyses) == 3
    assert p._queue.empty()


@pytest.mark.asyncio
async def test_queue_full_accounted_as_classifier_error_bucket(tmp_path):
    """Dropped snapshots never vanish silently: a drop record exists and the
    request record says classifier=dropped (feeds the queue_dropped bucket)."""
    p = make_pipeline(tmp_path, QUEUE_SIZE=1)
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    await p.inlet(body, {"id": "u"})
    await p.inlet(body, {"id": "u"})
    records = read_events(tmp_path)
    assert len(by_type(records, "drop")) == 1
    assert by_type(records, "request")[-1]["classifier"] == "dropped"


@pytest.mark.asyncio
async def test_outlet_snapshot_classified_with_direction(tmp_path, monkeypatch):
    p = make_pipeline(tmp_path)

    async def fake_post(payload):
        assert "[assistant]" in payload
        return {"pii_disclosure": 0.8}

    monkeypatch.setattr(p, "_post_systemone", fake_post)
    snap = {
        "event_id": "evt-9",
        "ts": "2026-10-07T00:00:00.000+00:00",
        "direction": "outlet",
        "model": "m",
        "user_hash": "u",
        "chat_hash": "c",
        "text": "some response",
        "chars": 13,
    }
    await p._process_snapshot("outlet", snap)
    (a,) = by_type(read_events(tmp_path), "analysis")
    assert a["direction"] == "outlet"
    assert a["scores"]["pii_disclosure"] == pytest.approx(0.8)
    assert a["would_redact"] is True
    assert a["would_block"] is False
