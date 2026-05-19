import pathlib
import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Any
from agent_shield.engine import ShieldEngine

app = FastAPI(
    title="Agent-Shield Universal Security Mesh",
    description="Anonymizing egress search proxy, injection firewall, and structural sanitizer for AI agents",
    version="0.2.0"
)

# Initialize Engine with Ollama semantic verification active
engine = ShieldEngine(use_ollama=True)

# --- Pydantic Schemas for structural payload filtering ---
class SearchResultItem(BaseModel):
    title: str
    content: str
    url: str

class SanitizeRequest(BaseModel):
    results: List[SearchResultItem]

class SimulationPayload(BaseModel):
    incoming_content: str
    outbound_intent: str

# --- 1. SEARCH MESH PROXY ENDPOINTS (For OpenClaw / Cursor Custom Search) ---
@app.get("/")
@app.get("/search")
@app.get("/v1/search")
async def secure_search_proxy(request: Request):
    """
    Acts as a drop-in replacement search router.
    Intercepts user queries, sanitizes inputs, anonymizes metadata, and protects agents.
    """
    query = request.query_params.get("q", "")
    if not query:
        return JSONResponse(status_code=400, content={"error": "Missing query parameter 'q'"})

    sanitized_results = await engine.secure_search(query)

    return {
        "query": query,
        "agent_shield_verified": True,
        "results": sanitized_results
    }

# --- 2. STRUCTURAL DATA SANITIZER (For Open WebUI Pipelines & Ingestion) ---
@app.post("/v1/sanitize")
async def sanitize_search_results(payload: SanitizeRequest):
    """
    Accepts raw structured search blocks directly from ingestion pipelines,
    runs the dual-pass filtering grid, and returns neutralized structural strings.
    """
    sanitized_items = []
    threat_intercepted = False

    for item in payload.results:
        sanitized_content = engine.sanitize(item.content)

        if "[SECURITY SANITIZATION" in sanitized_content:
            threat_intercepted = True

        sanitized_items.append({
            "title": item.title,
            "url": item.url,
            "content": sanitized_content
        })

    return {
        "status": "PROCESSED",
        "threat_intercepted": threat_intercepted,
        "results": sanitized_items
    }

# --- 3. INTERACTIVE SIMULATION PLAYGROUND ---
@app.post("/api/v1/playground/simulate")
async def simulate_exploit(payload: SimulationPayload):
    """Simulates multi-vector inbound injections and outbound data leaks."""
    sanitized_inbound = engine.sanitize(payload.incoming_content)
    inbound_compromised = "[SECURITY SANITIZATION" in sanitized_inbound
    dlp_report = engine.inspect_egress(payload.outbound_intent)

    return {
        "simulation_status": "COMPLETED",
        "inbound_threat_detected": inbound_compromised,
        "sanitized_inbound_preview": sanitized_inbound,
        "outbound_dlp_report": dlp_report,
        "agent_status": "ISOLATED" if (inbound_compromised or not dlp_report["safe"]) else "SECURE"
    }

@app.get("/health")
async def health_check():
    return {"status": "HEALTHY", "ollama_enabled": engine.use_ollama}


# --- DASHBOARD UI ---
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serves the Agent-Shield management dashboard."""
    dashboard_path = pathlib.Path(__file__).parent / "dashboard.html"
    return HTMLResponse(content=dashboard_path.read_text())
