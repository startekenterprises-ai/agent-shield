"""Phase 1 log-only guarantees for the Pipelines filter.

Hard rules under test:
  * inlet()/outlet() NEVER mutate the request/response body.
  * inlet()/outlet() NEVER raise, whatever the input shape.
  * Drop-newest (not oldest) on queue full, with explicit drop records.
  * Outlet inherits the inlet event_id; miss -> fresh id + "miss" flag.
  * No raw prompt/response text may land in events.jsonl.
  * SSRF Addendum A: candidate URLs extracted (hostnames only, hashed,
    query strings stripped); worker resolves + flags sensitive ranges.
  * SSRF Addendum B: outlet leak scan logs {pattern_class, match_count} only.
"""

import asyncio
import copy
import json
import os

import pytest

from pipelines.agent_shield_filter import Pipeline

# Port 1 on loopback: connection refused instantly, no real Ollama needed.
TEST_VALVES = {
    "OLLAMA_BASE_URL": "http://127.0.0.1:1",
    "CLASSIFIER_TIMEOUT_S": 0.5,
    "QUEUE_SIZE": 100,
    "RAW_SAMPLE_RATE": 0.0,  # deterministic: no random sampling in tests
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


@pytest.mark.asyncio
async def test_inlet_never_mutates_body(tmp_path):
    p = make_pipeline(tmp_path)
    body = {
        "model": "test-model",
        "stream": True,
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "fetch",
                            "arguments": '{"url": "https://example.com/x?a=1"}',
                        },
                    }
                ],
            },
        ],
    }
    frozen = copy.deepcopy(body)
    out = await p.inlet(body, {"id": "user-1"})
    assert out is body
    assert body == frozen


@pytest.mark.asyncio
async def test_outlet_never_mutates_body(tmp_path):
    p = make_pipeline(tmp_path)
    body = {
        "model": "test-model",
        "choices": [
            {"message": {"content": "response text"}, "finish_reason": "stop"}
        ],
    }
    frozen = copy.deepcopy(body)
    out = await p.outlet(body, {"id": "user-1"})
    assert out is body
    assert body == frozen


@pytest.mark.asyncio
async def test_malformed_bodies_never_raise(tmp_path):
    bad_bodies = [
        None,
        {},
        {"messages": "not-a-list"},
        {"messages": None},
        {"messages": [{"role": "user"}]},  # missing content
        {"messages": ["just-a-string"]},
        {"messages": [{"role": "user", "content": ["odd", 123]}]},
        "a-string",
        12345,
    ]
    for bad in bad_bodies:
        p = make_pipeline(tmp_path)
        frozen = copy.deepcopy(bad)
        out_in = await p.inlet(bad, {"id": "u"})
        assert out_in is bad
        assert bad == frozen
        out_out = await p.outlet(bad, {"id": "u"})
        assert out_out is bad
        assert bad == frozen
    # Every malformed inlet left a filter_error record, never a crash.
    errors = by_type(read_events(tmp_path), "filter_error")
    assert len(errors) >= 2  # at least the None bodies


@pytest.mark.asyncio
async def test_outlet_disabled_returns_body_untouched(tmp_path):
    p = make_pipeline(tmp_path, ENABLE_OUTLET=False)
    body = {"model": "m", "choices": []}
    out = await p.outlet(body, {"id": "u"})
    assert out is body
    assert read_events(tmp_path) == []


@pytest.mark.asyncio
async def test_correlation_hit_inherits_event_id(tmp_path):
    p = make_pipeline(tmp_path)
    body = {
        "model": "m1",
        "messages": [{"role": "user", "content": "ping"}],
    }
    await p.inlet(body, {"id": "user-9"})
    inlet_id = p.last_event_id()
    resp = {
        "model": "m1",
        "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
    }
    await p.outlet(resp, {"id": "user-9"})
    assert p.last_event_id() == inlet_id
    responses = by_type(read_events(tmp_path), "response")
    assert len(responses) == 1
    assert responses[0]["event_id"] == inlet_id
    assert responses[0]["correlation"] == "hit"


@pytest.mark.asyncio
async def test_correlation_miss_flags_fresh_id(tmp_path):
    p = make_pipeline(tmp_path)  # no inlet first
    resp = {
        "model": "m1",
        "choices": [{"message": {"content": "orphan"}, "finish_reason": "stop"}],
    }
    await p.outlet(resp, {"id": "user-9"})
    responses = by_type(read_events(tmp_path), "response")
    assert len(responses) == 1
    assert responses[0]["correlation"] == "miss"
    assert len(responses[0]["event_id"]) == 32  # fresh uuid hex


@pytest.mark.asyncio
async def test_drop_newest_preserves_fifo(tmp_path):
    p = make_pipeline(tmp_path, QUEUE_SIZE=2)
    mk = lambda i: {
        "model": "m",
        "messages": [{"role": "user", "content": f"msg-{i}"}],
    }
    await p.inlet(mk(0), {"id": "u"})
    await p.inlet(mk(1), {"id": "u"})
    assert p._queue.qsize() == 2
    await p.inlet(mk(2), {"id": "u"})  # queue full -> drop record
    assert p._queue.qsize() == 2
    drops = by_type(read_events(tmp_path), "drop")
    assert len(drops) == 1
    assert drops[0]["reason"] == "queue_full"
    assert drops[0]["dropped_direction"] == "inlet"
    # FIFO preserved: the two survivors are msg-0 and msg-1 (drop-newest).
    survivors = []
    while not p._queue.empty():
        direction, snap = p._queue.get_nowait()
        survivors.append(snap["texts"][0][1])
    assert survivors == ["msg-0", "msg-1"]
    requests = by_type(read_events(tmp_path), "request")
    assert [r["classifier"] for r in requests] == ["queued", "queued", "dropped"]


@pytest.mark.asyncio
async def test_no_raw_prompts_in_events(tmp_path):
    p = make_pipeline(tmp_path)
    secret = "supersecret-prompt-marker-987654321"
    await p.inlet(
        {"model": "m", "messages": [{"role": "user", "content": secret}]},
        {"id": "user-1"},
    )
    await p.outlet(
        {
            "model": "m",
            "choices": [
                {
                    "message": {"content": f"echo {secret}"},
                    "finish_reason": "stop",
                }
            ],
        },
        {"id": "user-1"},
    )
    path = os.path.join(str(tmp_path), "events.jsonl")
    with open(path, encoding="utf-8") as fh:
        blob = fh.read()
    assert secret not in blob
    # ...but message metadata (hashes) IS present.
    requests = by_type(read_events(tmp_path), "request")
    assert requests[0]["messages"][0]["role"] == "user"
    assert len(requests[0]["messages"][0]["sha256"]) == 64


@pytest.mark.asyncio
async def test_ssrf_candidate_url_extraction(tmp_path):
    p = make_pipeline(tmp_path)
    body = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "fetch that"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "fetch",
                            "arguments": '{"url": "http://169.254.169.254/latest/meta-data/?token=SECRET123"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "see https://internal.example:8443/admin for details",
            },
        ],
    }
    await p.inlet(body, {"id": "u"})
    direction, snap = p._queue.get_nowait()
    cands = snap["candidate_urls"]
    hosts = {c["host"]: c for c in cands}
    assert "169.254.169.254" in hosts
    assert "internal.example" in hosts
    meta = hosts["169.254.169.254"]
    assert meta["source"] == "tool_call_args"
    assert meta["host_port"] is None
    assert hosts["internal.example"]["host_port"] == 8443
    assert hosts["internal.example"]["source"] == "tool_message"
    # Query strings stripped before hashing: hash differs from the raw URL's.
    import hashlib

    raw_hash = hashlib.sha256(
        b"http://169.254.169.254/latest/meta-data/?token=SECRET123"
    ).hexdigest()
    stripped_hash = hashlib.sha256(
        b"http://169.254.169.254/latest/meta-data/"
    ).hexdigest()
    assert meta["url_hash"] == stripped_hash
    assert meta["url_hash"] != raw_hash
    # No raw secret-bearing URL anywhere in the events log.
    with open(os.path.join(str(tmp_path), "events.jsonl"), encoding="utf-8") as fh:
        assert "SECRET123" not in fh.read()


@pytest.mark.asyncio
async def test_ssrf_probe_flags_metadata_ip(tmp_path):
    """Worker resolves the literal IP (no DNS network needed) and flags it."""
    p = make_pipeline(tmp_path)
    body = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "fetch",
                            "arguments": '{"url": "http://169.254.169.254/latest/meta-data/"}',
                        },
                    }
                ],
            }
        ],
    }
    await p.inlet(body, {"id": "u"})
    direction, snap = p._queue.get_nowait()
    await p._process_snapshot(direction, snap)  # Tev1 fails fast (port 1)
    probes = by_type(read_events(tmp_path), "ssrf_probe")
    assert len(probes) == 1
    probe = probes[0]
    assert probe["event_id"] == snap["event_id"]
    assert probe["host"] == "169.254.169.254"
    assert probe["dns_status"] == "ok"
    assert probe["any_flag"] is True
    assert probe["resolved_ips"][0]["flag"] == "cloud_metadata"
    assert "tev1_model" in probe  # provenance stamped


@pytest.mark.asyncio
async def test_response_leak_detection_counts_only(tmp_path):
    p = make_pipeline(tmp_path)
    resp = {
        "model": "m",
        "choices": [
            {
                "message": {
                    "content": (
                        "ERROR: claim denied for SSN 123-45-6789. "
                        "Key AKIAIOSFODNN7EXAMPLE leaked. "
                        "Again SSN 123-45-6789."
                    )
                },
                "finish_reason": "stop",
            }
        ],
    }
    await p.outlet(resp, {"id": "u"})
    leaks = by_type(read_events(tmp_path), "response_leak")
    by_class = {r["pattern_class"]: r["match_count"] for r in leaks}
    assert by_class == {"ssn": 2, "aws_key": 1}
    for r in leaks:
        assert r["redacted_in_log"] is False
        assert r["direction"] == "outlet"
    # Raw PII never persisted.
    with open(os.path.join(str(tmp_path), "events.jsonl"), encoding="utf-8") as fh:
        blob = fh.read()
    assert "123-45-6789" not in blob
    assert "AKIAIOSFODNN7EXAMPLE" not in blob


@pytest.mark.asyncio
async def test_luhn_rejects_non_cards(tmp_path):
    p = make_pipeline(tmp_path)
    resp = {
        "model": "m",
        "choices": [
            {
                # 16 digits that fail Luhn -> not counted
                "message": {"content": "order 1234567890123456 shipped"},
                "finish_reason": "stop",
            }
        ],
    }
    await p.outlet(resp, {"id": "u"})
    leaks = by_type(read_events(tmp_path), "response_leak")
    assert all(r["pattern_class"] != "credit_card" for r in leaks)
