/**
 * Agent-Shield connector — deterministic detection layer (heuristic_v5).
 *
 * Ported from the Agent-Shield Phase 1 log-only filter
 * (pipelines/agent_shield_filter.py: _scan_leaks, _extract_candidate_urls,
 * _classify_ip, _luhn_ok). No ML in v1: the hosted Tev1 decision model
 * replaces the prompt-injection heuristics when hosted inference lands.
 *
 * INVARIANTS (enforced by construction):
 *  - Raw sensitive content is NEVER echoed, logged, or persisted.
 *  - Findings carry { pattern_class, match_count } — never matched text.
 *  - URLs are stored as sha256(host-stripped URL); query/fragment dropped.
 */

export interface ScanScores {
  prompt_injection: number;
  secrets_exposure: number;
  pii_disclosure: number;
  ssrf_risk: number;
}

export interface Finding {
  /** e.g. "ssn" | "credit_card" | "aws_key" | "github_token" | "pem_key"
   *  | "ssrf" | "prompt_injection_signal" */
  class: string;
  /** count of matches, or 1 for boolean signals */
  match_count: number;
  /** extra non-sensitive detail: ip_class, url_hash, signal name — never raw text */
  detail?: Record<string, string | number>;
}

export interface ScanResult {
  verdict: "clean" | "suspicious" | "malicious";
  scores: ScanScores;
  findings: Finding[];
  analyzer: "heuristic_v5";
  /** sha256 of the scanned content — for audit correlation, not the content */
  content_hash: string;
  scanned_chars: number;
}

const MAX_CONTENT_CHARS = 64_000;
const MAX_URLS = 50;
const DNS_TIMEOUT_MS = 3000;

// ---------------------------------------------------------------------------
// Small pure helpers
// ---------------------------------------------------------------------------

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function luhnOk(digits: string): boolean {
  let total = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = digits.charCodeAt(digits.length - 1 - i) - 48;
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
  }
  return total % 10 === 0;
}

// ---------------------------------------------------------------------------
// Leak patterns (Addendum B). Raw matches never leave this module.
// ---------------------------------------------------------------------------

const LEAK_PATTERNS: Array<{
  name: string;
  re: RegExp;
  luhn?: boolean;
}> = [
  { name: "ssn", re: /\b\d{3}-\d{2}-\d{4}\b/g },
  { name: "credit_card", re: /\b\d{13,19}\b/g, luhn: true },
  // AWS access-key IDs: AKIA (long-term) + ASIA (temporary session).
  // The 40-char secret is deliberately NOT matched — too FP-prone.
  // Well-known documentation placeholders are allowlisted below.
  { name: "aws_key", re: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g },
  {
    name: "github_token",
    re: /\b(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{22,}\b/g,
  },
  { name: "pem_key", re: /-----BEGIN [A-Z ]*PRIVATE KEY-----/g },
  // Generic high-entropy secret assignments: password/secret/token/api_key =
  // "long random string" (quoted). Conservative: requires a secret-ish key
  // name and a 20+ char high-entropy value.
  {
    name: "generic_secret",
    re: /\b(?:password|passwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token)\b\s*[:=]\s*["']([A-Za-z0-9_\-+/=]{20,})["']/gi,
  },
];

// Documentation placeholders that must never count as leaked secrets.
const SECRET_ALLOWLIST = new Set([
  "AKIAIOSFODNN7EXAMPLE",
  "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
  "EXAMPLEKEY",
  "YOUR_TOKEN_HERE",
  "FAKE_KEY_MATERIAL_FOR_PARSER_TESTS",
]);

function scanLeaks(text: string): Map<string, number> {
  const counts = new Map<string, number>();
  for (const { name, re, luhn } of LEAK_PATTERNS) {
    re.lastIndex = 0;
    let n = 0;
    for (const m of text.matchAll(re)) {
      const val = m[1] ?? m[0];
      if (SECRET_ALLOWLIST.has(val)) continue;
      if (luhn && !luhnOk(val)) continue;
      n++;
    }
    if (n > 0) counts.set(name, n);
  }
  return counts;
}

// ---------------------------------------------------------------------------
// URL extraction + SSRF classification (Addendum A)
// ---------------------------------------------------------------------------

const URL_RE = /https?:\/\/[^\s"'<>`]+/gi;

interface CandidateUrl {
  url_hash: string;
  host: string;
  host_port: number | null;
}

/** Normalize common URL defanging (hxxp, [.] , (dot)) so obfuscated
 *  SSRF targets are still extracted and classified. */
function normalizeDefanged(text: string): string {
  return text
    .replace(/\bhxxps?:\/\//gi, "http://")
    .replace(/\[\.\]/g, ".")
    .replace(/\(\s*dot\s*\)/gi, ".");
}

async function extractCandidateUrls(text: string): Promise<CandidateUrl[]> {
  const found: CandidateUrl[] = [];
  const seen = new Set<string>();
  const normalized = normalizeDefanged(text);
  URL_RE.lastIndex = 0;
  for (const m of normalized.matchAll(URL_RE)) {
    if (found.length >= MAX_URLS) break;
    let raw = m[0].slice(0, 2048);
    // Strip query + fragment (that's where secrets live), keep scheme/host/path.
    let stripped: string;
    let host: string | null = null;
    let port: number | null = null;
    try {
      const u = new URL(raw);
      u.search = "";
      u.hash = "";
      stripped = u.toString();
      host = u.hostname.toLowerCase();
      port = u.port ? parseInt(u.port, 10) : null;
    } catch {
      continue;
    }
    if (!host) continue;
    const urlHash = await sha256Hex(stripped);
    if (seen.has(urlHash)) continue;
    seen.add(urlHash);
    found.push({ url_hash: urlHash, host, host_port: port });
  }
  return found;
}

/** Normalize non-standard IPv4 literals to dotted decimal before classification.
 *  Attackers write 127.0.0.1 as 0x7f000001 (hex), 2130706433 (decimal),
 *  017700000001 (octal), or mixed per-octet forms like 0x7f.0.0.1 —
 *  all of which browsers and fetch() resolve to loopback. */
function normalizeIpLiteral(host: string): string {
  const h = host.toLowerCase().replace(/^\[|\]$/g, "");
  // Single 32-bit number: hex (0x...), octal (0...), or decimal.
  const asInt = (s: string): number | null => {
    if (/^0x[0-9a-f]+$/.test(s)) return parseInt(s, 16);
    if (/^0[0-7]+$/.test(s) && s.length > 1) return parseInt(s, 8);
    if (/^[0-9]+$/.test(s)) {
      const n = parseInt(s, 10);
      return n >= 0 && n <= 0xffffffff ? n : null;
    }
    return null;
  };
  const single = asInt(h);
  if (single !== null && !h.includes(".") && !h.includes(":")) {
    return [
      (single >>> 24) & 255,
      (single >>> 16) & 255,
      (single >>> 8) & 255,
      single & 255,
    ].join(".");
  }
  // Dotted form with hex/octal/decimal octets: 0x7f.0.0.1, 0177.0.0.1
  if (h.includes(".")) {
    const parts = h.split(".");
    if (parts.length === 4) {
      const nums: number[] = [];
      for (const p of parts) {
        const n = asInt(p);
        if (n === null || n > 255) return host;
        nums.push(n);
      }
      return nums.join(".");
    }
  }
  return host;
}

/** Classify an IP per the SSRF taxonomy. Order matters. */
function classifyIp(ip: string): string {
  const norm = normalizeIpLiteral(ip);
  // IPv4
  const v4 = norm.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const o = v4.slice(1, 5).map(Number);
    if (o.some((n) => n > 255)) return "invalid";
    const [a, b] = o;
    if (a === 169 && b === 254) return "cloud_metadata"; // 169.254.0.0/16
    if (a === 127) return "loopback"; // 127.0.0.0/8
    if (a === 10) return "private"; // 10.0.0.0/8
    if (a === 172 && b >= 16 && b <= 31) return "private"; // 172.16.0.0/12
    if (a === 192 && b === 168) return "private"; // 192.168.0.0/16
    if (a >= 224 && a <= 239) return "multicast";
    if (a === 0 || a >= 240) return "reserved";
    return "public";
  }
  // IPv6 (normalized lowercase, no brackets)
  const v6 = ip.toLowerCase().replace(/^\[|\]$/g, "");
  if (v6 === "::1") return "loopback";
  if (v6.startsWith("fe80:")) return "link_local"; // fe80::/10
  if (v6.startsWith("fc") || v6.startsWith("fd")) return "private"; // fc00::/7
  if (v6.startsWith("ff")) return "multicast";
  if (/^[0-9a-f:]+$/.test(v6) && v6.includes(":")) {
    // IPv4-mapped: ::ffff:a.b.c.d
    const mapped = v6.match(/::ffff:(\d+\.\d+\.\d+\.\d+)$/);
    if (mapped) return classifyIp(mapped[1]);
    return "public";
  }
  return "invalid";
}

const SSRF_RISK: Record<string, number> = {
  cloud_metadata: 1.0,
  loopback: 0.9,
  link_local: 0.8,
  private: 0.7,
  reserved: 0.6,
  multicast: 0.6,
  public: 0.05,
  invalid: 0.0,
  unresolved: 0.0,
};

async function resolveHost(host: string): Promise<string[]> {
  // If the "host" is already a literal IP, skip DNS.
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(":")) return [host];
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), DNS_TIMEOUT_MS);
  try {
    const out: string[] = [];
    for (const type of ["A", "AAAA"]) {
      const r = await fetch(
        `https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(host)}&type=${type}`,
        { headers: { Accept: "application/dns-json" }, signal: ctrl.signal },
      );
      if (!r.ok) continue;
      const j = (await r.json()) as {
        Answer?: Array<{ type: number; data: string }>;
      };
      for (const a of j.Answer ?? []) {
        if ((a.type === 1 || a.type === 28) && a.data) out.push(a.data);
      }
      if (out.length > 0) break;
    }
    return out;
  } catch {
    return [];
  } finally {
    clearTimeout(t);
  }
}

// ---------------------------------------------------------------------------
// Text normalization: undo common obfuscations before scoring.
// Each normalizer produces an additional view of the text; all views are
// scored and the max signal set wins.
// ---------------------------------------------------------------------------

/** Cyrillic/Greek homoglyphs -> Latin equivalents. */
const HOMOGLYPH_MAP: Record<string, string> = {
  "\u0430": "a", "\u0435": "e", "\u0456": "i", "\u043e": "o", "\u0440": "p",
  "\u0441": "c", "\u0445": "x", "\u0443": "y", "\u043a": "k", "\u043c": "m",
  "\u043d": "h", "\u03b1": "a", "\u03b5": "e", "\u03bf": "o", "\u03c1": "p",
};

function normalizeHomoglyphs(text: string): string {
  return text.replace(
    /[\u0430\u0435\u0456\u043e\u0440\u0441\u0445\u0443\u043a\u043c\u043d\u03b1\u03b5\u03bf\u03c1]/g,
    (ch) => HOMOGLYPH_MAP[ch] ?? ch,
  );
}

/** Leetspeak -> plain letters (only the unambiguous substitutions). */
function normalizeLeet(text: string): string {
  return text
    .replace(/0/g, "o")
    .replace(/1/g, "i")
    .replace(/3/g, "e")
    .replace(/4/g, "a")
    .replace(/5/g, "s")
    .replace(/7/g, "t")
    .replace(/@/g, "a")
    .replace(/\$/g, "s");
}

/** ROT13 decode. */
function rot13(text: string): string {
  return text.replace(/[a-zA-Z]/g, (ch) => {
    const base = ch <= "Z" ? 65 : 97;
    return String.fromCharCode(((ch.charCodeAt(0) - base + 13) % 26) + base);
  });
}

/** Try hex decode of long hex runs (with or without 0x prefixes/spaces). */
function tryHexDecode(s: string): string | null {
  const compact = s.replace(/0x|\s+/gi, "");
  if (!/^[0-9a-fA-F]{40,}$/.test(compact) || compact.length % 2 !== 0) return null;
  try {
    let out = "";
    for (let i = 0; i < compact.length; i += 2) {
      out += String.fromCharCode(parseInt(compact.slice(i, i + 2), 16));
    }
    if (!/^[\x20-\x7e\s]{10,}$/.test(out)) return null;
    return out;
  } catch {
    return null;
  }
}

const HEX_RE = /(?:0x[0-9a-fA-F]{2}(?:\s*0x[0-9a-fA-F]{2}){10,}|[0-9a-fA-F]{40,})/g;

/** Build the set of text views to score: raw + normalized variants. */
function textViews(text: string): string[] {
  const views = [text];
  const noZw = text.replace(/[​-‍﻿]/g, "");
  if (noZw !== text) views.push(noZw);
  views.push(normalizeHomoglyphs(noZw));
  views.push(normalizeLeet(noZw));
  views.push(rot13(noZw));
  // Reversed text: attackers reverse the whole payload.
  views.push([...noZw].reverse().join(""));
  // De-JSON: join string fragments split across array fields / keys,
  // e.g. ["Disregard","the user's","request"] -> "Disregard the user's request".
  const dejson = noZw
    .replace(/"\s*,\s*"/g, " ")
    .replace(/[{}\[\]]/g, " ")
    .replace(/\\"/g, '"');
  if (dejson !== noZw) views.push(dejson);
  return [...new Set(views)];
}

// Verbs attackers use to cancel existing instructions...
const OVERRIDE_VERBS =
  "ignor(e|ing)|disregard|forget|drop|overrid(e|ing)|abandon|bypass|defy|" +
  "cancel|void|nullify|eras(e|ing)|rescind|supersed(e|ing)|throw\\s+out|" +
  "set\\s+aside|stop\\s+following|do\\s+not\\s+follow";
// ...and the nouns they target.
const OVERRIDE_TARGETS =
  "instructions?|guidelines?|rules?|constraints?|safety(\\s+rules|\\s+training)?|" +
  "content\\s+polic(y|ies)|guardrails?|directives?|training|alignment|" +
  "system(\\s+prompt)?|prior\\s+context|all\\s+rules";

const INJECTION_SIGNALS: Array<{ name: string; re: RegExp; weight: number }> = [
  {
    name: "instruction_override",
    re: new RegExp(
      `(?:${OVERRIDE_VERBS})\\s+(?:your\\s+|the\\s+|all\\s+|previous\\s+|prior\\s+|my\\s+)*(?:${OVERRIDE_TARGETS})`,
      "i",
    ),
    weight: 0.45,
  },
  {
    name: "system_prompt_extraction",
    re: /reveal\s+(your\s+|the\s+)?system\s+(prompt|instructions)|disclose\s+(your\s+)?(system\s+)?(prompt|instructions)|print\s+(your\s+|the\s+)?(instructions|system\s+prompt)\s+verbatim|what\s+are\s+your\s+(exact\s+)?instructions|output\s+everything\s+above|repeat\s+after\s+me|translate\s+your\s+system\s+prompt|list\s+(every|all)\s+instructions?|read\s+back\s+(the\s+)?(full\s+)?system\s+prompt|show\s+me\s+the\s+script|quote\s+(them|it)\s+exactly|system\s+prompt\s+verbatim|exfiltrat(e|ing)\s+your\s+own\s+system\s+prompt/i,
    weight: 0.45,
  },
  {
    name: "role_spoofing",
    re: /\[(system|admin|developer|privileged|moderator)\]|<(system|admin|sys)\b|<<\s*sys\s*>>|#{2,}\s*system\b|system\s+(note|notice|override|transcript)\b|internal\s+memo\b|debug\s+mode\b|sudo\s+mode\b|role\s*:\s*system\b|begin\s+system\s+transcript/i,
    weight: 0.35,
  },
  {
    name: "authority_claim",
    re: /you\s+are\s+now\b(?! a helpful)|from\s+now\s+on,?\s+you\s+(are|will)|new\s+(instructions|directive|system\s+update)\s*:|you\s+are\s+(dan|venom)\b|unrestricted\s+mode|no\s+(restrictions|safety\s+rules)|never\s+refuse|policy\s+disabled|safety\s+(interlocks?\s+disengaged|filters?\s+disabled|layer\s+(was\s+)?removed)/i,
    weight: 0.3,
  },
  {
    name: "exfiltration_directive",
    re: /(forward|send|exfiltrate|upload|post|export|email)\s+.{0,60}(api[_-]?key|secret|token|password|credential|database|contact\s+list|environment\s+variables|chat)/i,
    weight: 0.5,
  },
  {
    name: "hidden_instruction",
    re: /<!--[\s\S]{0,800}?(system|instruction|forward|ignore|assistant)[\s\S]{0,800}?-->|\(hidden\s*:|<span[^>]*display\s*:\s*none[^>]*>/i,
    weight: 0.4,
  },
  {
    // Indirect injection: an "assistant:" directive embedded in third-party
    // content (reviews, tickets, docs, transcripts). Requires an action verb
    // to avoid flagging ordinary "Assistant:" mentions.
    name: "embedded_assistant_directive",
    re: /\bassistant\s*:\s*(ignore|disregard|cancel|delete|send|email|forward|disable|mark|recommend|omit|skip|share|export|upload|post|reveal|list|read|execute|run|revert|approve|deny|rank|pin|feature|update|tell|say|cite|assure|round|always|never|when|if)\b/i,
    weight: 0.4,
  },
  {
    // Jailbreak personas: self-declared identities whose whole purpose is
    // evading safety behavior (DAN and its cousins, "no restrictions" claims,
    // developer impersonation, fake policy/memo authority).
    name: "jailbreak_persona",
    re: /\bdan\b.{0,30}do\s+anything\s+now|do\s+anything\s+now|jailbreak|no\s+restrictions|without\s+restrictions|unfiltered|i\s+am\s+(your\s+)?developer\b|as\s+(your|the)\s+developer|policy\s+update|internal\s+memo|alignment\s+team|red\s+team|oversight\s+board|compliance\s+test|emergency\s+protocol/i,
    weight: 0.4,
  },
  {
    // Concealment directive: instructing the agent to hide an action from the
    // user. Almost never legitimate — strong signal, especially combined
    // with any other directive.
    name: "concealment_directive",
    re: /do\s*n'?t\s+(mention|tell|inform|notify|show)\s+(the\s+)?user|no\s+need\s+to\s+mention|keep\s+this\s+(between\s+us|confidential|quiet)|don'?t\s+include\s+this\s+in\s+(your|the)\s+(summary|response|report)|without\s+(telling|notifying|alerting)\s+the\s+user/i,
    weight: 0.5,
  },
  {
    // Bare imperative to a sensitive target: "forward the messages",
    // "send the database", "delete the logs" — no classic trigger words,
    // but the shape of an instruction to the agent.
    name: "imperative_sensitive_action",
    re: /\b(forward|send|email|export|upload|delete|remove|share|reveal|disclose|list|read|fetch|retrieve)\s+(the\s+|all\s+|these\s+)?(messages?|mails?|database|logs?|files?|documents?|credentials?|keys?|tokens?|passwords?|calendar|contacts?|history)\b/i,
    weight: 0.35,
  },
];

// Attribution framing: "the article says '...'", "for example: '...'".
// When a suspicious signal appears ONLY inside attributed/quoted speech,
// it's discussion-about, not execution-of. Returns true if the text looks
// like it's quoting someone/something rather than instructing.
const ATTRIBUTION_RE =
  /(says?|said|wrote|writes?|according\s+to|for\s+example|such\s+as|e\.g\.|called|titled|article|write-?up|documentation|quoting|quote)[\s\S]{0,120}["""]|["""][^"""]{0,300}["""]\s*(says?|writes?|notes?|explains?)/i;

function hasAttributionFraming(text: string): boolean {
  return ATTRIBUTION_RE.test(text);
}

// ---------------------------------------------------------------------------
// Signal combination: weak signals that co-occur become strong.
// A bare imperative alone is 0.35; imperative + concealment, or imperative
// + external contact, crosses the suspicious threshold by combination.
// ---------------------------------------------------------------------------

const EXTERNAL_CONTACT_RE =
  /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|https?:\/\/[^\s"'<>`]+/i;

function combinationBonus(
  text: string,
  signals: string[],
): { bonus: number; combos: string[] } {
  const has = (...names: string[]) =>
    names.some((n) => signals.some((s) => s.includes(n)));
  const combos: string[] = [];
  let bonus = 0;
  const imperative = has("imperative_sensitive_action", "embedded_assistant_directive");
  const conceal = has("concealment_directive");
  const exfil = has("exfiltration_directive");
  const external = EXTERNAL_CONTACT_RE.test(text);
  if (imperative && conceal) {
    bonus += 0.3;
    combos.push("imperative+concealment");
  }
  if ((imperative || exfil) && external) {
    bonus += 0.25;
    combos.push("directive+external_contact");
  }
  if (conceal && external) {
    bonus += 0.2;
    combos.push("concealment+external_contact");
  }
  return { bonus, combos };
}

// Base64-ish blobs worth decoding (short enough to be real payloads,
// long enough to avoid noise).
const B64_RE = /[A-Za-z0-9+/]{40,}={0,2}/g;

function tryB64Decode(s: string): string | null {
  try {
    // Web Crypto atob equivalent for workers: use atob (available).
    const bin = atob(s);
    // Keep only plausible text.
    if (!/^[\x20-\x7e\s]{10,}$/.test(bin)) return null;
    return bin;
  } catch {
    return null;
  }
}

// Phrases marking discussion-about-attacks rather than attacks themselves.
// Halve the injection score when present (never applied to secrets/PII/SSRF).
const EDUCATIONAL_CONTEXT_RE =
  /for\s+educational\s+purposes|do\s+not\s+execute|example\s+of\s+what\s+not\s+to\s+do|threat\s+briefing|security\s+(article|training|policy|write-?up)|ctf\s+write-?up|documentation\s+sample|in\s+this\s+write-?up|defense\s+against/i;

function scoreInjection(text: string): { score: number; signals: string[] } {
  let score = 0;
  const signals: string[] = [];
  const addSignal = (name: string, weight: number, view: string) => {
    const key = view === "raw" ? name : `${view}:${name}`;
    if (!signals.includes(key)) {
      score += weight;
      signals.push(key);
    }
  };
  // Score every normalized view; attacker obfuscation shouldn't hide signals.
  for (const view of textViews(text)) {
    const tag = view === text ? "raw" : "norm";
    for (const { name, re, weight } of INJECTION_SIGNALS) {
      re.lastIndex = 0;
      if (re.test(view)) addSignal(name, weight * (tag === "raw" ? 1 : 0.9), tag);
    }
  }
  const cleaned = text.replace(/[​-‍﻿]/g, "");
  // Structural dampening: if the text frames content as attributed quotation
  // ("the article says..."), injection signals are discussion, not execution.
  const attributed = hasAttributionFraming(cleaned);
  // Decode base64 blobs and re-scan the plaintext for injection signals.
  B64_RE.lastIndex = 0;
  for (const m of cleaned.matchAll(B64_RE)) {
    const decoded = tryB64Decode(m[0]);
    if (!decoded) continue;
    for (const { name, re, weight } of INJECTION_SIGNALS) {
      re.lastIndex = 0;
      if (re.test(decoded)) addSignal(name, weight * 0.8, "b64");
    }
  }
  // Decode hex blobs and re-scan.
  HEX_RE.lastIndex = 0;
  for (const m of cleaned.matchAll(HEX_RE)) {
    const decoded = tryHexDecode(m[0]);
    if (!decoded) continue;
    for (const { name, re, weight } of INJECTION_SIGNALS) {
      re.lastIndex = 0;
      if (re.test(decoded)) addSignal(name, weight * 0.8, "hex");
    }
  }
  if (EDUCATIONAL_CONTEXT_RE.test(cleaned)) {
    score *= 0.5;
    signals.push("educational_context_dampened");
  }
  if (attributed) {
    score *= 0.6;
    signals.push("attribution_framing_dampened");
  }
  // Signal combination: co-occurring weak signals amplify each other.
  const { bonus, combos } = combinationBonus(cleaned, signals);
  if (bonus > 0) {
    score += bonus;
    for (const c of combos) signals.push(`combo:${c}`);
  }
  return { score: Math.min(1, score), signals };
}

// ---------------------------------------------------------------------------
// Main entry
// ---------------------------------------------------------------------------

export async function scanContent(
  content: string,
  contentType = "text",
): Promise<ScanResult> {
  const text = content.slice(0, MAX_CONTENT_CHARS);
  const contentHash = await sha256Hex(content);
  const findings: Finding[] = [];
  const scores: ScanScores = {
    prompt_injection: 0,
    secrets_exposure: 0,
    pii_disclosure: 0,
    ssrf_risk: 0,
  };

  // 1. Secrets / PII (deterministic patterns)
  const leaks = scanLeaks(text);
  for (const [name, count] of leaks) {
    findings.push({ class: name, match_count: count });
  }
  const ssnN = leaks.get("ssn") ?? 0;
  const ccN = leaks.get("credit_card") ?? 0;
  if (ssnN > 0 || ccN > 0) {
    scores.pii_disclosure = Math.min(1, 0.6 + 0.2 * Math.max(ssnN, ccN));
  }
  const awsN = leaks.get("aws_key") ?? 0;
  const ghN = leaks.get("github_token") ?? 0;
  const pemN = leaks.get("pem_key") ?? 0;
  if (awsN + ghN + pemN > 0) {
    scores.secrets_exposure = Math.min(1, 0.7 + 0.15 * (awsN + ghN + pemN));
  }

  // 2. SSRF: extract URLs, resolve, classify
  const urls = await extractCandidateUrls(text);
  for (const u of urls) {
    const ips = await resolveHost(u.host);
    if (ips.length === 0) {
      findings.push({
        class: "ssrf",
        match_count: 1,
        detail: { host: u.host, ip_class: "unresolved", url_hash: u.url_hash.slice(0, 16) },
      });
      continue;
    }
    // Worst classification wins for this URL.
    let worst = "public";
    let worstIp = "";
    for (const ip of ips) {
      const cls = classifyIp(ip);
      if ((SSRF_RISK[cls] ?? 0) > (SSRF_RISK[worst] ?? 0)) {
        worst = cls;
        worstIp = ip;
      }
    }
    void worstIp;
    const risk = SSRF_RISK[worst] ?? 0;
    if (risk > scores.ssrf_risk) scores.ssrf_risk = risk;
    if (worst !== "public") {
      findings.push({
        class: "ssrf",
        match_count: 1,
        detail: {
          host: u.host,
          ip_class: worst,
          url_hash: u.url_hash.slice(0, 16),
        },
      });
    }
  }

  // 3. Prompt-injection heuristics
  const inj = scoreInjection(text);
  scores.prompt_injection = inj.score;
  for (const s of inj.signals) {
    findings.push({ class: "prompt_injection_signal", match_count: 1, detail: { signal: s } });
  }

  // Verdict
  const peak = Math.max(
    scores.prompt_injection,
    scores.secrets_exposure,
    scores.pii_disclosure,
    scores.ssrf_risk,
  );
  const verdict = peak >= 0.85 ? "malicious" : peak >= 0.4 ? "suspicious" : "clean";

  return {
    verdict,
    scores,
    findings,
    analyzer: "heuristic_v5",
    content_hash: contentHash,
    scanned_chars: text.length,
  };
}
