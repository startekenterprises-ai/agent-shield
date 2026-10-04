import os
import re
import httpx
from typing import Dict, Any, List

MALICIOUS_PATTERNS = [
    r"(?i)ignore\s+(?:all\s+)?previous\s+instructions",
    r"(?i)system\s+override",
    r"(?i)delete\s+(?:all\s+)?files",
    r"(?i)you\s+are\s+now\s+a\s+(?:malicious|harmful)\s+agent",
    r"(?i)respond\s+only\s+with",
    r"sudo\s+rm\s+-rf"
]

class DLPFilter:
    def __init__(self):
        # Targets exfiltration vectors across webhooks, tracking pixels, and subdomains
        self.patterns = {
            "api_key": re.compile(r'(?:sk|pk|secret|key|token|passwd)[-_a-zA-Z0-9]{12,}', re.IGNORECASE),
            "env_variable": re.compile(r'(?:AWS_|AZURE_|STRIPE_|GITHUB_)[A-Z_]+=\S+'),
            "dns_exfiltration": re.compile(r'[a-zA-Z0-9.-]+\.(?:canarytokens\.com|burpcollaborator\.net|interactsh\.com)'),
            "markdown_pixel": re.compile(r'\!\[.*?\]\((https?://[^\s)]+?\.(?:png|jpg|gif)\?.*?)\)', re.IGNORECASE)
        }

    def inspect_outbound(self, payload: str) -> Dict[str, Any]:
        """Scans outgoing payloads for systemic credentials and leak vectors."""
        violations = []
        for name, pattern in self.patterns.items():
            matches = pattern.findall(payload)
            if matches:
                violations.append({"type": name, "matches": len(matches)})
        return {
            "safe": len(violations) == 0,
            "violations": violations,
            "action": "BLOCK" if violations else "ALLOW"
        }

class MultiEngineRouter:
    def __init__(self):
        # Read configurations from environment variables or default to local setups
        self.provider = os.getenv("SEARCH_PROVIDER", "searxng").lower()
        self.searxng_url = os.getenv("REAL_SEARXNG_URL", "http://localhost:8080")
        # NOTE (Phase 0): BRAVE_API_KEY removed with the dead Brave provider path.

    async def fetch_results(self, query: str) -> List[Dict[str, Any]]:
        """Routes search requests across providers while implementing privacy padding and masking."""
        normalized_results = []

        # 1. ANONYMIZATION & SALTING LAYER [EXPERIMENTAL — unverified efficacy]:
        # Strip path indicators, local directories, and config markers to mask target architecture.
        # NOTE (Phase 0): the ".json"/".env" stripping can break legitimate searches
        # for config docs, and the "documentation context" salting is a heuristic
        # with no measured anti-fingerprinting effect. Kept as-is pending review;
        # do not present this as a real privacy guarantee.
        clean_query = re.sub(r'(/home/[^\s]+|~\/[^\s]+|\b\w+\.json\b|\b\w+\.env\b)', '', query)

        # EXPERIMENTAL (see note above): appends a fixed padding phrase intended to
        # pollute telemetry profiles. Effect unmeasured — heuristic only.
        secured_query = f"{clean_query.strip()} documentation context"

        if self.provider == "searxng":
            async with httpx.AsyncClient() as client:
                try:
                    resp = await client.get(
                        f"{self.searxng_url}/search",
                        params={"q": secured_query, "format": "json"},
                        timeout=8.0
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for item in data.get("results", []):
                            normalized_results.append({
                                "title": item.get("title", ""),
                                "content": item.get("content", ""),
                                "url": item.get("url", "")
                            })
                except Exception as e:
                    print(f"DEBUG [MultiEngineRouter]: Local SearXNG lookup error: {e}")

        return normalized_results

    # NOTE (Phase 0): the "brave" provider path was removed. It was dead code —
    # it called https://brave.com instead of the real Brave Search API endpoint.
    # Search-provider routing is out of scope for v2 (the interception point
    # moves to model traffic via Open WebUI Pipelines). Re-add here only if a
    # working Brave Search API integration is needed.

class ShieldEngine:
    def __init__(self, custom_patterns: list = None, use_ollama: bool = False, ollama_model: str = "qwen2.5-coder-7b:128k"):
        self.patterns = custom_patterns if custom_patterns else MALICIOUS_PATTERNS
        self.use_ollama = use_ollama
        self.ollama_model = ollama_model
        # DORMANT (Phase 0): retained for API compatibility; no HTTP is made.
        # Phase 2 repoints this at /v1/systemone for Tev1 decision models.
        self.ollama_url = "http://localhost:11434/api/generate"
        self.dlp = DLPFilter()
        self.router = MultiEngineRouter()

    def sanitize(self, text: str) -> str:
        """First pass high-speed regex sanitization loop."""
        if not text:
            return ""
        sanitized = text
        for pattern in self.patterns:
            sanitized = re.sub(pattern, "[SECURITY SANITIZATION TRIGGERED]", sanitized)

        # Second pass: deferred to Phase 2 (see semantic_check_stub). v1 fired a
        # blocking ~5s Ollama generation call on every CLEAN input — a latency
        # disaster in any hot path. The stub below is a no-op by design.
        if self.use_ollama and "[SECURITY SANITIZATION TRIGGERED]" not in sanitized:
            sanitized = self.semantic_check_stub(sanitized)
        return sanitized

    def inspect_egress(self, text: str) -> Dict[str, Any]:
        """Exposes DLP inspection directly to the engine core."""
        return self.dlp.inspect_outbound(text)

    async def secure_search(self, query: str) -> List[Dict[str, Any]]:
        """Unified entry point to run privacy-protected web queries and sanitize returns."""
        raw_results = await self.router.fetch_results(query)
        for item in raw_results:
            if item["content"]:
                item["content"] = self.sanitize(item["content"])
        return raw_results

    def semantic_check_stub(self, text: str) -> str:
        """STUB (Phase 0 demolition).

        Replaces the v1 blocking Ollama generation call, which fired a ~5s
        synchronous inference on every clean input in the hot path.

        Phase 2 replaces this with Ollama Tev1 decision models via
        POST http://localhost:11434/v1/systemone — a single forward pass that
        returns typed choices with per-option probabilities in milliseconds,
        instead of a seconds-long text-generation call.

        Implementation notes for Phase 2 (do not reintroduce the v1 bugs):
        - Exception handling: v1 caught only (httpx.ConnectError,
          httpx.TimeoutException), letting every other httpx failure
          (ReadError, RemoteProtocolError, PoolTimeout, HTTPStatusError, …)
          propagate uncaught. Phase 2 MUST catch the full set — i.e. catch
          httpx.HTTPError (the base class) — and degrade to pass-through.
        - Never block the hot path: the decision call must have a tight
          timeout and an async/non-blocking design.
        - Labels must be frozen and calibrated (decision models are sensitive
          to option labeling).
        """
        print("DEBUG [ShieldEngine]: semantic check deferred to Phase 2 (Tev1); passing through.")
        return text

