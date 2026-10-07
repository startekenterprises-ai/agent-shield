"""
Agent-Shield Phase 1 — logging-only Open WebUI Pipelines inlet filter.

Scope (frozen per panel verdict 2026-10-05): observe, log, calibrate.
NO blocking, NO redaction enforcement, NO fine-tuning.

What this filter does:
  * inlet()  — snapshots request metadata + content hashes (never raw prompts),
               extracts SSRF candidate URLs (Addendum A), enqueues for the
               background worker, returns the body UNMODIFIED.
  * outlet() — snapshots response metadata, scans the in-memory response for
               PII/secret leak patterns (Addendum B), enqueues, returns the
               body UNMODIFIED.
  * background worker — calls the local Tev1 decision model (log-only),
               resolves SSRF candidate hostnames, writes analysis / ssrf_probe
               records. Never raises into the request path.

Hard rules:
  * inlet/outlet NEVER mutate the body and NEVER raise.
  * Classification happens in the background worker, never synchronously.
  * Queue-full policy is drop-NEWEST with an explicit drop record (FIFO kept).
  * events.jsonl carries hashes only; raw text lives solely in samples.jsonl
    (1% sample, mode 0600) — the only place raw content may exist.

Implements:
  * panel plan  : workspace/goals/monday-morning-operator-brief/
                  panel-chatgpt-agentshield-phase1-2026-10-05.md
  * SSRF addenda: workspace/agent-shield-ssrf-addenda-2026-10-06.md
    (approved by Glenn 2026-10-07 — both addenda)
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import os
import random
import re
import shutil
import socket
import time
import urllib.parse
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
SYSTEMONE_CONTRACT_VERSION = "systemone/v1"
PROMPT_TEMPLATE_VERSION = "prompt/v1"
NORMALIZATION_VERSION = "bands/v1"

HEARTBEAT_INTERVAL_S = 60
CORRELATION_CACHE_SIZE = 10_000
CIRCUIT_BREAKER_THRESHOLD = 10
CIRCUIT_BREAKER_COOLDOWN_S = 60
DNS_TIMEOUT_S = 2.0
LEAK_SCAN_MAX_CHARS = 65_536
MAX_URLS_PER_EVENT = 50
MAX_URL_LEN = 2_048

THREAT_CLASSES = (
    "prompt_injection",
    "indirect_injection",
    "secrets_exposure",
    "pii_disclosure",
    "poisoned_content",
)

# Provisional score-band map for normalizing non-0..1 classifier outputs.
# Versioned in schemas/score_bands_v1.json; kept in sync here as fallback.
SCORE_BANDS = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}

_URL_RE = re.compile(r"https?://[^\s\"'<>`]+", re.IGNORECASE)

# Addendum B — leak patterns. Bounded regexes; raw matches never leave memory.
_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b\d{13,19}\b")),
    # AWS access-key IDs are AKIA + 16 chars (the 40-char value is the secret,
    # which we deliberately do NOT try to match — too FP-prone).
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp_|gho_)[A-Za-z0-9]{36}\b")),
    ("pem_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


# ---------------------------------------------------------------------------
# Small helpers (pure, easily unit-tested)
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _extract_text(content) -> str:
    """Pull plain text out of an OpenAI-style message content field."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
                elif isinstance(t, dict) and isinstance(t.get("value"), str):
                    parts.append(t["value"])
        return "\n".join(parts)
    return str(content)


def _extract_response_text(body: dict) -> tuple[str, str | None, bool]:
    """Return (text, finish_reason, stream_truncated) from a chat response body."""
    text = ""
    finish_reason = None
    stream_truncated = False
    try:
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            message = choice.get("message") or {}
            delta = choice.get("delta") or {}
            text = _extract_text(message.get("content") or delta.get("content"))
            if delta and not message.get("content"):
                stream_truncated = True
    except Exception:
        pass
    return text, finish_reason, stream_truncated


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _scan_leaks(text: str) -> dict[str, int]:
    """Count PII/secret pattern hits. Returns {pattern_class: match_count}."""
    counts: dict[str, int] = {}
    for name, pattern in _LEAK_PATTERNS:
        if name == "credit_card":
            n = sum(1 for m in pattern.finditer(text) if _luhn_ok(m.group(0)))
        else:
            n = sum(1 for _ in pattern.finditer(text))
        if n:
            counts[name] = n
    return counts


def _strip_query(url: str) -> str:
    """Drop query string and fragment (that's where secrets live)."""
    try:
        parts = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, "", "")
        )
    except Exception:
        return url.split("?", 1)[0].split("#", 1)[0]


def _extract_candidate_urls(messages: list) -> list[dict]:
    """Addendum A (inlet side): hostnames only, query strings stripped,
    URLs hashed. Bounded: MAX_URLS_PER_EVENT per event."""
    found: list[dict] = []
    seen: set[str] = set()

    def _add(text: str, source: str) -> None:
        for match in _URL_RE.finditer(text[: MAX_URL_LEN * 4]):
            if len(found) >= MAX_URLS_PER_EVENT:
                return
            raw = match.group(0)[:MAX_URL_LEN]
            stripped = _strip_query(raw)
            url_hash = _sha256(stripped)
            if url_hash in seen:
                continue
            seen.add(url_hash)
            try:
                host = urllib.parse.urlsplit(stripped).hostname
                port = urllib.parse.urlsplit(stripped).port
            except Exception:
                host, port = None, None
            if not host:
                continue
            found.append(
                {
                    "url_hash": url_hash,
                    "host": host,
                    "host_port": port,
                    "source": source,
                }
            )

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, dict):
                args = json.dumps(args)
            if isinstance(args, str) and args:
                _add(args, "tool_call_args")
        if msg.get("role") == "tool":
            _add(_extract_text(msg.get("content")), "tool_message")
    return found


def _classify_ip(ip_str: str) -> str:
    """Flag an IP per the SSRF addendum taxonomy (order matters)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return "invalid"
    if ip.version == 4 and ip in ipaddress.ip_network("169.254.0.0/16"):
        return "cloud_metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "private"
    if ip.is_link_local:
        return "link_local"
    if ip.is_reserved:
        return "reserved"
    if ip.is_multicast:
        return "multicast"
    return "public"

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

_ENV_PREFIX = "AGENTSHIELD_"


def _coerce_env(raw: str, annotation):
    if annotation is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if annotation is int:
        return int(raw)
    if annotation is float:
        return float(raw)
    return raw


class Pipeline:
    """
    Open WebUI Pipelines filter pipeline (type = "filter").

    Attach per-model in Open WebUI (Admin → Models → Filters) for scoped
    rollout. Phase 1 is LOG-ONLY: inlet()/outlet() never mutate the body
    and never raise into the request path.
    """

    type = "filter"

    class Valves(BaseModel):
        LOG_DIR: str = "./logs/agent-shield"
        OLLAMA_BASE_URL: str = "http://localhost:11434"
        TEV1_MODEL: str = "tev1:0.8b"
        SYSTEMONE_PATH: str = "/v1/systemone"
        QUEUE_SIZE: int = 2000
        WORKER_CONCURRENCY: int = 4
        CLASSIFIER_TIMEOUT_S: float = 5.0
        MAX_CLASSIFY_CHARS: int = 8000
        RAW_SAMPLE_RATE: float = 0.01
        ENABLE_OUTLET: bool = True
        PROVISIONAL_BLOCK_THRESHOLD: float = 0.9
        PROVISIONAL_REDACT_THRESHOLD: float = 0.7
        LOG_RETENTION_DAYS: int = 30

    def __init__(self, valves: dict | None = None):
        self.valves = self._load_valves(valves)
        self._queue: asyncio.Queue = asyncio.Queue(
            maxsize=self.valves.QUEUE_SIZE
        )
        # (chat_hash, turn_seq) -> event_id, bounded LRU.
        self._correlation: OrderedDict[tuple[str, int], str] = OrderedDict()
        self._tasks: list[asyncio.Task] = []
        self._started_at = time.monotonic()
        self._log_date: str | None = None
        self._last_event_id: str | None = None
        # Counters for heartbeat / coverage accounting.
        self._dropped_total = 0
        self._classified_total = 0
        self._error_total = 0
        # Circuit breaker state.
        self._consec_failures = 0
        self._cooldown_until = 0.0
        # Provenance (resolved lazily, cached).
        self._model_digest: str | None = None
        self._config_hash = _sha256(
            json.dumps(self.valves.model_dump(), sort_keys=True)
        )

    @classmethod
    def _load_valves(cls, overrides: dict | None = None) -> "Pipeline.Valves":
        data: dict = {}
        for field, finfo in cls.Valves.model_fields.items():
            env_key = _ENV_PREFIX + field
            if env_key in os.environ:
                data[field] = _coerce_env(os.environ[env_key], finfo.annotation)
        if overrides:
            data.update(overrides)
        return cls.Valves(**data)

    # -- lifecycle ------------------------------------------------------

    async def on_startup(self):
        """Spawn the background worker and heartbeat. Never blocks requests."""
        self._resolve_provenance()  # best-effort pre-warm; lazy fallback exists
        self._tasks.append(asyncio.create_task(self._worker_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

    async def on_shutdown(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    def last_event_id(self) -> str | None:
        """Most recent event_id (used by scripts/redteam_probe.py for labels)."""
        return self._last_event_id

    # -- inlet ----------------------------------------------------------

    async def inlet(self, body: dict, user: dict | None = None) -> dict:
        t0 = time.perf_counter()
        event_id = uuid.uuid4().hex
        self._last_event_id = event_id
        try:
            if not isinstance(body, dict):
                raise TypeError(f"inlet body must be dict, got {type(body).__name__}")
            user_id = str((user or {}).get("id", "anonymous"))
            user_hash = _sha256(user_id)
            model = str(body.get("model", "unknown"))
            messages = body.get("messages", [])
            malformed = not isinstance(messages, list)
            if malformed:
                messages = []
            turn_seq = len(messages)
            chat_hash = _sha256(f"{user_id}:{model}")

            msg_metas: list[dict] = []
            texts: list[tuple[str, str]] = []
            total_chars = 0
            for i, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role", "unknown"))
                text = _extract_text(msg.get("content"))
                chars = len(text)
                total_chars += chars
                msg_metas.append(
                    {"i": i, "role": role, "chars": chars, "sha256": _sha256(text)}
                )
                texts.append((role, text))

            # SSRF Addendum A — candidate URLs (hostnames only, hashed).
            candidate_urls = _extract_candidate_urls(messages)

            snapshot = {
                "event_id": event_id,
                "ts": _utcnow(),
                "direction": "inlet",
                "model": model,
                "user_hash": user_hash,
                "chat_hash": chat_hash,
                "turn_seq": turn_seq,
                "stream": bool(body.get("stream", False)),
                "texts": texts,
                "candidate_urls": candidate_urls,
                "total_chars": total_chars,
            }

            # Correlation entry for the outlet hook (bounded LRU).
            key = (chat_hash, turn_seq)
            if key in self._correlation:
                del self._correlation[key]
            self._correlation[key] = event_id
            while len(self._correlation) > CORRELATION_CACHE_SIZE:
                self._correlation.popitem(last=False)

            # Enqueue BEFORE writing the record so the record reflects reality.
            # Drop-newest: on QueueFull the incoming snapshot gets the drop
            # record; FIFO order of queued work is preserved.
            queue_status = "queued"
            try:
                self._queue.put_nowait(("inlet", snapshot))
            except asyncio.QueueFull:
                queue_status = "dropped"
                self._dropped_total += 1
                self._write(
                    "events",
                    {
                        "type": "drop",
                        "event_id": event_id,
                        "ts": _utcnow(),
                        "reason": "queue_full",
                        "dropped_direction": "inlet",
                        "total_dropped": self._dropped_total,
                    },
                )

            inlet_latency_us = int((time.perf_counter() - t0) * 1e6)
            self._write(
                "events",
                {
                    "type": "request",
                    "event_id": event_id,
                    "ts": snapshot["ts"],
                    "direction": "inlet",
                    "model": model,
                    "user_hash": user_hash,
                    "chat_hash": chat_hash,
                    "turn_seq": turn_seq,
                    "stream": snapshot["stream"],
                    "messages": msg_metas,
                    "total_chars": total_chars,
                    "inlet_latency_us": inlet_latency_us,
                    "classifier": queue_status,
                    "malformed": malformed,
                },
            )

            # 1% raw sample → samples.jsonl (0600). The ONLY place raw text
            # may be persisted.
            if (
                not malformed
                and random.random() < self.valves.RAW_SAMPLE_RATE
            ):
                self._write(
                    "samples",
                    {
                        "event_id": event_id,
                        "ts": snapshot["ts"],
                        "raw_text": [t for _, t in texts],
                    },
                )
        except Exception as exc:  # NEVER break the request path
            self._write(
                "events",
                {
                    "type": "filter_error",
                    "event_id": event_id,
                    "ts": _utcnow(),
                    "hook": "inlet",
                    "error": type(exc).__name__,
                },
            )
        return body  # ALWAYS unmodified in Phase 1

    # -- outlet ---------------------------------------------------------

    def _resolve_correlation(self, chat_hash: str) -> tuple[str, str]:
        """Find the latest inlet event_id for this chat.

        The map is keyed (chat_hash, turn_seq); the outlet hook cannot know
        the turn sequence from the response body alone, so it resolves the
        highest turn_seq seen for the chat. Miss -> fresh id + "miss" flag.
        """
        best_seq = -1
        best_id: str | None = None
        for (ch, seq), eid in self._correlation.items():
            if ch == chat_hash and seq > best_seq:
                best_seq, best_id = seq, eid
        if best_id is not None:
            return best_id, "hit"
        return uuid.uuid4().hex, "miss"

    async def outlet(self, body: dict, user: dict | None = None) -> dict:
        if not self.valves.ENABLE_OUTLET:
            return body
        t0 = time.perf_counter()
        provisional_id = uuid.uuid4().hex
        try:
            if not isinstance(body, dict):
                raise TypeError(
                    f"outlet body must be dict, got {type(body).__name__}"
                )
            user_id = str((user or {}).get("id", "anonymous"))
            user_hash = _sha256(user_id)
            model = str(body.get("model", "unknown"))
            chat_hash = _sha256(f"{user_id}:{model}")
            event_id, correlation = self._resolve_correlation(chat_hash)
            self._last_event_id = event_id

            text, finish_reason, stream_truncated = _extract_response_text(body)
            chars = len(text)

            # SSRF Addendum B — leak scan on the in-memory body (µs scale).
            # Logs {pattern_class, match_count} only; raw matches never persist.
            for pattern_class, count in _scan_leaks(
                text[:LEAK_SCAN_MAX_CHARS]
            ).items():
                self._write(
                    "events",
                    {
                        "type": "response_leak",
                        "event_id": event_id,
                        "ts": _utcnow(),
                        "pattern_class": pattern_class,
                        "match_count": count,
                        "redacted_in_log": False,
                        "direction": "outlet",
                    },
                )

            outlet_latency_us = int((time.perf_counter() - t0) * 1e6)
            snapshot = {
                "event_id": event_id,
                "ts": _utcnow(),
                "direction": "outlet",
                "model": model,
                "user_hash": user_hash,
                "chat_hash": chat_hash,
                "text": text,
                "chars": chars,
            }
            try:
                self._queue.put_nowait(("outlet", snapshot))
                queue_status = "queued"
            except asyncio.QueueFull:
                queue_status = "dropped"
                self._dropped_total += 1
                self._write(
                    "events",
                    {
                        "type": "drop",
                        "event_id": event_id,
                        "ts": _utcnow(),
                        "reason": "queue_full",
                        "dropped_direction": "outlet",
                        "total_dropped": self._dropped_total,
                    },
                )
            self._write(
                "events",
                {
                    "type": "response",
                    "event_id": event_id,
                    "ts": snapshot["ts"],
                    "direction": "outlet",
                    "model": model,
                    "chars": chars,
                    "sha256": _sha256(text),
                    "finish_reason": finish_reason,
                    "stream_truncated": stream_truncated,
                    "outlet_latency_us": outlet_latency_us,
                    "correlation": correlation,
                    "classifier": queue_status,
                },
            )
        except Exception as exc:  # NEVER break the request path
            self._write(
                "events",
                {
                    "type": "filter_error",
                    "event_id": provisional_id,
                    "ts": _utcnow(),
                    "hook": "outlet",
                    "error": type(exc).__name__,
                },
            )
        return body  # ALWAYS unmodified in Phase 1

    # -- log writing + rotation -----------------------------------------

    def _log_paths(self, kind: str) -> str:
        name = "events.jsonl" if kind == "events" else "samples.jsonl"
        return os.path.join(self.valves.LOG_DIR, name)

    def _write(self, kind: str, record: dict) -> None:
        """Append one JSONL record. Synchronous (no awaits) so concurrent
        async hooks cannot interleave lines."""
        record = {"schema_version": SCHEMA_VERSION, **record}
        if "ts" not in record:
            record["ts"] = _utcnow()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._log_date != today:
            self._rotate_logs(self._log_date, today)
            self._log_date = today
        os.makedirs(self.valves.LOG_DIR, exist_ok=True)
        path = self._log_paths(kind)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        if kind == "samples":
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        else:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)

    def _rotate_logs(self, old_date: str | None, today: str) -> None:
        """Gzip-rotate events.jsonl on date rollover; enforce retention."""
        if old_date:
            src = self._log_paths("events")
            if os.path.exists(src) and os.path.getsize(src) > 0:
                dst = os.path.join(
                    self.valves.LOG_DIR, f"events-{old_date}.jsonl.gz"
                )
                try:
                    with open(src, "rb") as fh_in, gzip.open(
                        dst, "wb"
                    ) as fh_out:
                        shutil.copyfileobj(fh_in, fh_out)
                    os.remove(src)
                except OSError:
                    pass
        # Retention sweep: drop rotated files older than LOG_RETENTION_DAYS.
        cutoff = time.time() - self.valves.LOG_RETENTION_DAYS * 86400
        try:
            for name in os.listdir(self.valves.LOG_DIR):
                if name.startswith("events-") and name.endswith(".jsonl.gz"):
                    full = os.path.join(self.valves.LOG_DIR, name)
                    try:
                        if os.path.getmtime(full) < cutoff:
                            os.remove(full)
                    except OSError:
                        pass
        except OSError:
            pass

    # -- provenance -----------------------------------------------------

    def _provenance(self) -> dict:
        return {
            "tev1_model": self.valves.TEV1_MODEL,
            "model_digest": self._model_digest or "unknown",
            "systemone_contract_version": SYSTEMONE_CONTRACT_VERSION,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "classifier_config_hash": self._config_hash,
        }

    def _resolve_provenance(self) -> None:
        """Best-effort Ollama model digest lookup (cached). Never raises."""
        if self._model_digest is not None:
            return
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    f"{self.valves.OLLAMA_BASE_URL}/api/show",
                    json={"name": self.valves.TEV1_MODEL},
                )
                data = resp.json() if resp.status_code == 200 else {}
            digest = data.get("digest") or (
                data.get("details") or {}
            ).get("digest")
            self._model_digest = str(digest) if digest else "unknown"
        except Exception:
            self._model_digest = "unknown"

    # -- background worker ----------------------------------------------

    async def _worker_loop(self) -> None:
        sem = asyncio.Semaphore(self.valves.WORKER_CONCURRENCY)
        while True:
            direction, snapshot = await self._queue.get()
            try:
                async with sem:
                    await self._process_snapshot(direction, snapshot)
            except Exception:
                self._error_total += 1
            finally:
                self._queue.task_done()

    async def _process_snapshot(self, direction: str, snapshot: dict) -> None:
        """Classify one snapshot; write analysis (+ ssrf_probe) records.

        Failures are recorded as analysis rows with a non-ok
        classifier_status — the worker never raises into the request path.
        """
        if direction == "inlet":
            texts = snapshot.get("texts", [])
            payload, truncated = self._build_classify_input(texts)
            chars = sum(len(t) for _, t in texts)
            analysis = await self._classify(
                snapshot, payload, truncated, chars, "inlet"
            )
            self._write("events", analysis)
            # SSRF Addendum A — resolve candidate hostnames (worker-side,
            # never in the hot path).
            for cand in snapshot.get("candidate_urls", []):
                probe = await self._ssrf_probe(snapshot, cand)
                self._write("events", probe)
        elif direction == "outlet":
            text = snapshot.get("text", "")
            payload, truncated = self._build_classify_input(
                [("assistant", text)]
            )
            analysis = await self._classify(
                snapshot, payload, truncated, len(text), "outlet"
            )
            self._write("events", analysis)

    def _build_classify_input(
        self, texts: list[tuple[str, str]]
    ) -> tuple[str, bool]:
        """Role-tagged payload, truncated oldest-first at MAX_CLASSIFY_CHARS."""
        chunks = [f"[{role}]\n{text}" for role, text in texts if text]
        max_chars = self.valves.MAX_CLASSIFY_CHARS
        truncated = False
        while sum(len(c) for c in chunks) > max_chars and len(chunks) > 1:
            chunks.pop(0)
            truncated = True
        if chunks and len(chunks[0]) > max_chars:
            chunks[0] = chunks[0][:max_chars]
            truncated = True
        return "\n".join(chunks), truncated

    async def _classify(
        self,
        snapshot: dict,
        payload: str,
        truncated: bool,
        chars_classified: int,
        direction: str,
    ) -> dict:
        t0 = time.perf_counter()
        prov = self._provenance()
        base = {
            "type": "analysis",
            "event_id": snapshot["event_id"],
            "ts": _utcnow(),
            "direction": direction,
            "classifier": "tev1-0.8b",
            "truncated": truncated,
            "chars_classified": chars_classified,
            "decision": "log",  # ALWAYS "log" in Phase 1
        }

        # Circuit breaker: cool down after sustained failures.
        if time.monotonic() < self._cooldown_until:
            base.update(
                {
                    "classifier_latency_ms": 0,
                    "classifier_status": "unavailable",
                    "scores": {c: 0.0 for c in THREAT_CLASSES},
                    "score_raw": None,
                    "normalization_version": NORMALIZATION_VERSION,
                    "risk": 0.0,
                    "would_block": False,
                    "would_redact": False,
                    **{k: v for k, v in prov.items()},
                }
            )
            self._error_total += 1
            return base

        status = "ok"
        scores: dict[str, float] = {c: 0.0 for c in THREAT_CLASSES}
        score_raw = None
        try:
            score_raw = await self._post_systemone(payload)
            scores = self._normalize_scores(score_raw)
            self._consec_failures = 0
            self._classified_total += 1
        except httpx.TimeoutException:
            status = "timeout"
            self._register_failure()
        except httpx.HTTPError:
            status = "error"
            self._register_failure()
        except Exception:
            status = "error"
            self._register_failure()

        risk = max(scores.values()) if scores else 0.0
        latency_ms = int((time.perf_counter() - t0) * 1000)
        base.update(
            {
                "classifier_latency_ms": latency_ms,
                "classifier_status": status,
                "scores": scores,
                "score_raw": score_raw,
                "normalization_version": NORMALIZATION_VERSION,
                "risk": risk,
                # Provisional thresholds as calibration labels ONLY — never
                # acted on in Phase 1.
                "would_block": risk >= self.valves.PROVISIONAL_BLOCK_THRESHOLD,
                "would_redact": risk >= self.valves.PROVISIONAL_REDACT_THRESHOLD,
                **prov,
            }
        )
        return base

    def _register_failure(self) -> None:
        self._consec_failures += 1
        self._error_total += 1
        if self._consec_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self._cooldown_until = (
                time.monotonic() + CIRCUIT_BREAKER_COOLDOWN_S
            )

    async def _post_systemone(self, payload: str):
        """POST to the Tev1 decision endpoint. 5s timeout, 1 retry on timeout."""
        url = f"{self.valves.OLLAMA_BASE_URL}{self.valves.SYSTEMONE_PATH}"
        body = {"model": self.valves.TEV1_MODEL, "input": payload}
        timeout = self.valves.CLASSIFIER_TIMEOUT_S
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body)
        except httpx.TimeoutException:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body)  # single retry
        resp.raise_for_status()
        data = resp.json()
        return data.get("scores", data)

    def _normalize_scores(self, raw) -> dict[str, float]:
        """Map raw classifier output to 0.0–1.0 per threat class.

        Identity passthrough when the endpoint already returns 0–1 floats;
        band-label mapping (low/medium/high/critical) otherwise.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"unexpected score shape: {type(raw).__name__}")
        scores: dict[str, float] = {}
        for cls in THREAT_CLASSES:
            value = raw.get(cls, 0.0)
            if isinstance(value, str):
                value = SCORE_BANDS.get(value.strip().lower(), 0.0)
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = 0.0
            scores[cls] = max(0.0, min(1.0, value))
        return scores

    # -- SSRF Addendum A (worker side) ----------------------------------

    async def _ssrf_probe(self, snapshot: dict, cand: dict) -> dict:
        """Resolve one candidate hostname; flag sensitive IP ranges."""
        host = cand.get("host")
        port = cand.get("host_port") or 80
        dns_status = "ok"
        resolved: list[dict] = []
        try:
            infos = await asyncio.wait_for(
                asyncio.to_thread(
                    socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM
                ),
                timeout=DNS_TIMEOUT_S,
            )
            for info in infos:
                ip = info[4][0]
                if any(entry["ip"] == ip for entry in resolved):
                    continue
                resolved.append({"ip": ip, "flag": _classify_ip(ip)})
        except Exception:
            dns_status = "error"
        any_flag = any(entry["flag"] != "public" for entry in resolved)
        return {
            "type": "ssrf_probe",
            "event_id": snapshot["event_id"],
            "ts": _utcnow(),
            "url_hash": cand.get("url_hash"),
            "host": host,
            "resolved_ips": resolved,
            "any_flag": any_flag,
            "dns_status": dns_status,
            **self._provenance(),
        }

    # -- heartbeat ------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            degraded = time.monotonic() < self._cooldown_until
            try:
                self._write(
                    "events",
                    {
                        "type": "heartbeat",
                        "ts": _utcnow(),
                        "queue_depth": self._queue.qsize(),
                        "dropped_total": self._dropped_total,
                        "classified_total": self._classified_total,
                        "error_total": self._error_total,
                        "uptime_s": int(time.monotonic() - self._started_at),
                        "model_digest": self._model_digest or "unknown",
                        "classifier_config_hash": self._config_hash,
                        "degraded": degraded,
                    },
                )
            except Exception:
                pass
