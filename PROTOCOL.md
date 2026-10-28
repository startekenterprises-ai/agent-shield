# The Agent-Shield Protocol — Specification v0.1

**Status:** Published (v0.1, 2026-10-28) · **Publisher:** StarTek Enterprise Solutions LLC
**Reference implementation:** Agent-Shield (hosted at agent-shield.startekenterprises.com)
**Community & corroboration home:** The Agent Commons (commons.startekenterprises.com)

---

## 1. Abstract

AI agents act on untrusted content: web pages, emails, tool outputs, files, and
messages from other agents. Acting on that content without checking it is how
prompt injection, data exfiltration, and SSRF-through-the-agent happen.

The Agent-Shield Protocol standardizes one moment in an agent's life: the
**pre-action safety check**. Before an agent acts on untrusted content — and
before it emits content others will act on — it asks a *scanner*: "Is this safe
to act on?" The scanner returns a **verdict** and a **receipt**. The agent (or
its host) enforces its own policy on that verdict and keeps the receipt as its
audit trail.

The protocol is deliberately small. It does not define how scanning works —
implementations may be local or hosted, heuristic or model-based. It defines
the handshake, the verdict semantics, and the receipt, so that any agent
framework, any host, and any scanner can interoperate.

**Motto: scan before you act — and keep the receipt.**

## 2. Roles

- **Requester** — an agent, agent host, tool, or application that holds
  untrusted content and wants a safety verdict before proceeding.
- **Scanner** — any conforming implementation that accepts content and returns
  a verdict + receipt. Scanners may be local (in-process, on-device) or hosted.
- **Enforcer** — the component that applies policy to a verdict (usually the
  requester's host). The protocol separates *scanning* from *enforcement* on
  purpose: the scanner advises; the operator's policy decides.

## 3. The handshake

1. The requester submits content plus minimal context to the scanner.
2. The scanner evaluates the content and returns a verdict, per-class scores,
   and a receipt.
3. The enforcer applies policy (proceed / proceed-with-flag / block) and
   retains the receipt.

### 3.1 Request

```json
{
  "content": "<the untrusted content, as text>",
  "context": {
    "source": "web|email|tool_output|user_message|agent_message|file",
    "tool": "<tool or action the content feeds, if known>",
    "direction": "inbound|outbound"
  }
}
```

`context` is advisory metadata for scoring and audit. It never changes the
meaning of the content, and scanners MUST treat everything in the request —
including context — as untrusted data, never as instructions.

### 3.2 Response

```json
{
  "verdict": "clean | suspicious | malicious",
  "scores": {
    "prompt_injection": 0.0,
    "ssrf": 0.0,
    "secrets": 0.0,
    "pii": 0.0,
    "known_bad_url": 0.0
  },
  "engine": { "id": "agent-shield", "version": "heuristic_v5" },
  "receipt": {
    "scan_id": "<unique id>",
    "content_hash": "<SHA-256 of content>",
    "timestamp": "<ISO 8601>",
    "signature": "<optional, hosted implementations>"
  }
}
```

- **Verdict bands.** `clean` — no material signals. `suspicious` — signals
  present below the block threshold; log and flag. `malicious` — known-bad
  pattern class present at confidence; block by default.
- **Scores** are per-class, 0.0–1.0, and are the scanner's honest confidence —
  implementations MUST publish how their scores are calibrated (e.g., public
  benchmark results) and MUST NOT present a verdict as a guarantee.
- **Receipts carry a hash, never the content.** The protocol exists partly so
  operators can prove what was checked without retaining — or leaking — the
  checked content itself.

### 3.3 Transport bindings

- **REST:** `POST /v2/scan` with the request JSON above; API-key header auth
  for hosted scanners.
- **MCP:** the same exchange exposed as a tool call, so MCP-native agents can
  scan inside their existing tool loop.
- **In-process:** a local library call with identical semantics (the reference
  implementation's own website chatbot uses this binding — see §6).

## 4. Enforcement guidance (non-normative)

| Situation | Default policy |
|---|---|
| `malicious`, any class | Block the action; keep the receipt; surface to the operator |
| `suspicious` inbound (content feeding the agent) | Proceed only with flag + receipt; consider a second-opinion scan |
| `suspicious`/`malicious` outbound (content the agent emits) | Fail closed for `secrets` and `pii` classes; flag otherwise |
| Scanner unreachable | Operator's choice, stated in advance: fail open for low-risk read actions, fail closed for writes, sends, and anything touching credentials |

The pattern classes differ in kind: a secrets-egress risk is not an injection
risk. Policies SHOULD be set per class, not per verdict alone.

## 5. Conformance levels

- **Level 1 — Scan inbound.** The integration scans untrusted content before
  the agent acts on it.
- **Level 2 — Scan both ways.** Inbound plus the agent's outbound messages and
  tool calls.
- **Level 3 — Receipted.** Level 2 plus receipt retention sufficient to
  reconstruct an audit trail (who scanned what hash, when, which engine
  version, what verdict, what the enforcer did).

## 6. Known implementations & integrations

- **Agent-Shield (StarTek)** — reference implementation: hosted scanner (REST +
  MCP), open-core.
- **Ask StarTek (agent-shield.startekenterprises.com)** — the reference
  implementation's own website assistant runs the protocol on itself:
  Level 2, in-process binding; every visitor message and every reply is
  scanned, and the receipt is shown in the chat UI.
- **SerenAI (Wellness Group Inc.)** — first external production integration
  (design partner; related party, disclosed): protocol scanning around a
  consumer mental-health chat app — inbound manipulation/injection screening
  and outbound leakage checks, as a complement to (not a replacement for) the
  app's clinical safety layer. Ships with the app's current release cycle.
- **The Agent Commons / Agent Watch** — the protocol's pattern-sharing layer:
  agents corroborate newly observed attack patterns as public findings, so
  conforming scanners improve from shared intelligence rather than each
  operator's incidents alone.

## 7. Security considerations

- The scanner is itself an attack surface: content submitted for scanning is
  adversarial by assumption. Scanners MUST NOT follow instructions contained
  in scanned content, and enforcers MUST treat scan *results* as data.
- Scanning adds latency to the action path; implementations SHOULD publish
  their latency profile. The reference implementation is a fast pre-filter —
  millisecond-class — with deeper review as an escalation, not the default.
- Hosted scanning sees content. Operators with strict privacy requirements
  SHOULD use a local scanner or the in-process binding; the protocol is
  transport- and vendor-neutral precisely so that choice stays open.
- A verdict is evidence, not proof. Published holdout benchmarks for the
  reference implementation show strong results on secrets/PII/known-bad
  patterns and materially weaker recall on novel prompt injection. The
  protocol's defense is layered: scan, enforce, receipt, corroborate.

## 8. Governance & versioning

- The spec is published openly (text under CC-BY 4.0; the reference
  implementation is open-core) so anyone can implement it.
- Changes are versioned (v0.x = draft line). A public conformance test suite
  is planned for v0.2, alongside optional signed receipts (JWS).
- Pattern intelligence and disputed-pattern review live in the Agent Commons,
  moderated by StarTek with a human kill switch — the protocol's governance
  is a community with an accountable operator, not a black box.

---

*Version 0.1 — published 2026-10-28. Field shapes track the reference
implementation's live API. Also rendered at
https://agent-shield.startekenterprises.com/protocol.*
