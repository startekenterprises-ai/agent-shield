#!/bin/bash
set -e

STATE_FILE="./.shield_state"
ENV_FILE="./.env"
echo "🛡️  Agent-Shield: Intelligent AI Security Mesh Installer"
echo "======================================================="

# ---------------------------------------------------------------------------
# Pre-Flight: Environment Setup
# ---------------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    touch "$ENV_FILE"
fi

if [ -f "$STATE_FILE" ]; then
    source "$STATE_FILE"
else
    REAL_SEARXNG_URL="http://searxng-private-mesh:8080"
fi

# ---------------------------------------------------------------------------
# Build mode flag — pass --build to skip Docker Hub pull and build locally
# ---------------------------------------------------------------------------
BUILD_FROM_SOURCE=false
if [[ "$1" == "--build" ]]; then
    BUILD_FROM_SOURCE=true
    echo "🔧 Build-from-source mode enabled."
fi

# ---------------------------------------------------------------------------
# INTERACTIVE KEY INTERCEPT ENGINE (Auto-configures LLM Failovers)
# ---------------------------------------------------------------------------
echo "--- 🔑 LLM BACKEND PROVIDER REGISTRATION MATRIX ---"

# Step A: Local Ollama Inspection
read -p "❓ Do you run a local Ollama instance on this host system? (y/N): " HAS_OLLAMA
if [[ "$HAS_OLLAMA" =~ ^[Yy]$ ]]; then
    OLLAMA_TARGET="http://docker.internal"
    echo "✅ Local Ollama route mapped to host gateway link."
else
    OLLAMA_TARGET=""
fi

# Step B: OpenRouter Cloud Fallback Loop
if grep -q "OPENROUTER_API_KEY=" "$ENV_FILE" && [ ! -z "$(grep "OPENROUTER_API_KEY=" "$ENV_FILE" | cut -d'=' -f2)" ]; then
    CURRENT_KEY=$(grep "OPENROUTER_API_KEY=" "$ENV_FILE" | cut -d'=' -f2)
    echo "💡 Active OpenRouter API Token detected in local environment configuration."
    read -p "❓ Do you want to update or replace this OpenRouter token? (y/N): " CHANGE_KEY
    if [[ "$CHANGE_KEY" =~ ^[Yy]$ ]]; then
        echo "🌐 Navigate to your browser context and generate a free API token:"
        echo "   👉 https://openrouter.ai/workspaces/default/keys"
        read -p "🔑 Paste your OpenRouter API Key: " OPENROUTER_API_KEY
    else
        OPENROUTER_API_KEY=$CURRENT_KEY
    fi
else
    echo "🌐 To run high-end reasoning without a GPU, generate a free API token:"
    echo "   👉 https://openrouter.ai/workspaces/default/keys"
    read -p "🔑 Paste your OpenRouter API Key (or hit Enter to skip): " OPENROUTER_API_KEY
fi

sed -i '/OPENROUTER_API_KEY=/d' "$ENV_FILE" 2>/dev/null || true
echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" >> "$ENV_FILE"

if [ ! -z "$OPENROUTER_API_KEY" ]; then
    DEFAULT_MODEL="anthropic/claude-3.5-sonnet"
    DEFAULT_BASE="https://openrouter.ai"
    echo "🚀 Configuration targeted to: OpenRouter Cloud Premium Tier ($DEFAULT_MODEL)"
else
    DEFAULT_MODEL="qwen2.5-coder-7b:128k"
    DEFAULT_BASE="http://docker.internal/v1"
    echo "🚀 Configuration targeted to: Local Ollama Engine Pipeline ($DEFAULT_MODEL)"
fi

echo ""

# Helper: check if a container is running
is_active() {
    docker ps --format '{{.Names}}' | grep -q "^$1$"
}

# ---------------------------------------------------------------------------
# Module 1: SearXNG Private Search Engine
# Pulls from Docker Hub: searxng/searxng:latest
# ---------------------------------------------------------------------------
echo "--- 📦 MODULE 1: UPSTREAM SEARCH ENGINE ---"
if is_active "searxng-private-mesh"; then
    echo "💡 Local SearXNG container is currently RUNNING on port 8088."
    read -p "❓ Force STOP and REINSTALL Local SearXNG? (y/N): " DEPLOY_SEARXNG
else
    read -p "❓ Deploy fresh local SearXNG container on port 8088? (Y/n): " DEPLOY_SEARXNG
fi

if [[ "$DEPLOY_SEARXNG" =~ ^[Nn]$ ]]; then
    if [ -z "$SAVED_EXTERNAL_URL" ]; then
        read -p "  👉 Enter your pre-existing external SearXNG URL (e.g. http://192.168.2.42:8080): " REAL_SEARXNG_URL
    else
        read -p "  👉 Enter external SearXNG URL [Default: $SAVED_EXTERNAL_URL]: " NEW_EXT_URL
        REAL_SEARXNG_URL=${NEW_EXT_URL:-$SAVED_EXTERNAL_URL}
    fi
    SAVED_EXTERNAL_URL=$REAL_SEARXNG_URL
    RUN_SEARXNG_ACTION=false
else
    REAL_SEARXNG_URL="http://searxng-private-mesh:8080"
    RUN_SEARXNG_ACTION=true
fi

echo ""

# ---------------------------------------------------------------------------
# Module 2: Agent-Shield Security Gateway
# Option A (default): Pulls from Docker Hub: startekenterprises/agent-shield:latest
# Option B (--build): Builds from local source
# ---------------------------------------------------------------------------
echo "--- 🛡️  MODULE 2: AGENT-SHIELD FIREWALL CORE ---"
if is_active "agent-shield-gateway"; then
    echo "💡 Agent-Shield Core container is currently RUNNING on port 8000."
    read -p "❓ Force STOP and REINSTALL Agent-Shield Firewall? (y/N): " DEPLOY_SHIELD
else
    read -p "❓ Deploy Agent-Shield Security Firewall Proxy on port 8000? (Y/n): " DEPLOY_SHIELD
fi

echo ""

# ---------------------------------------------------------------------------
# Module 3: OpenClaw Agent Workspace
# Always built from local source (./containers/openclaw/) using latest
# browser-use. This ensures OpenClaw is always wired correctly to your
# Agent-Shield version. Pin to n-1 in the Dockerfile if a breaking
# OpenClaw release ever causes issues.
# ---------------------------------------------------------------------------
echo "--- 🎨 MODULE 3: HYPERCONVERGED AGENT WORKSPACE ---"
if is_active "openclaw-agent-workspace"; then
    echo "💡 OpenClaw Workspace container is currently RUNNING."
    read -p "❓ Force STOP and REINSTALL OpenClaw Workspace? (y/N): " DEPLOY_OPENCLAW
else
    read -p "❓ Bundle in a containerized OpenClaw Agent Workspace? (y/N): " DEPLOY_OPENCLAW
fi

# ---------------------------------------------------------------------------
# Optional: Community Threat Contribution
# ---------------------------------------------------------------------------
echo ""
echo "--- 🤝 OPTIONAL: COMMUNITY THREAT MESH ---"
read -p "❓ Help improve Agent-Shield by contributing anonymized threat patterns? (y/N): " CONTRIBUTE
if [[ "$CONTRIBUTE" =~ ^[Yy]$ ]]; then
    echo "What would you like your agent to work on to improve Agent-Shield?"
    echo "  1) Hunt for new prompt injection patterns"
    echo "  2) Test DLP evasion signatures"
    echo "  3) Expand regex detection coverage"
    echo "  4) Other (skip for now)"
    read -p "Choice [1-4]: " CONTRIBUTION_TYPE
    echo "✅ Contribution preference saved: option $CONTRIBUTION_TYPE"
    echo "CONTRIBUTION_TYPE=$CONTRIBUTION_TYPE" >> "$ENV_FILE"
fi

# ===========================================================================
# Orchestration Pipeline Execution
# ===========================================================================
echo "======================================================="
echo "🚀 Executing targeted container orchestration lifecycle..."

docker network create agent-shield-mesh 2>/dev/null || true

# ---------------------------------------------------------------------------
# Execute Module 1: SearXNG
# ---------------------------------------------------------------------------
if [ "$RUN_SEARXNG_ACTION" = true ] && [[ "$DEPLOY_SEARXNG" =~ ^[Yy]$ || "$DEPLOY_SEARXNG" == "" ]]; then
    echo "📦 Pulling SearXNG from Docker Hub (searxng/searxng:latest)..."
    mkdir -p ./config/searxng
    if [ ! -f ./config/searxng/settings.yml ]; then
        cat << 'EOF' > ./config/searxng/settings.yml
use_default_settings: true
server:
  port: 8080
  bind_address: "0.0.0.0"
  secret_key: "agent_shield_super_secure_entropy_salt_key"
EOF
    fi
    docker rm -f searxng-private-mesh || true
    docker run -d \
      --name searxng-private-mesh \
      --network agent-shield-mesh \
      --network-alias searxng-private-mesh \
      -v "$(pwd)/config/searxng:/etc/searxng:ro" \
      -p 8088:8080 \
      --restart always \
      searxng/searxng:latest
    echo "✅ SearXNG running on port 8088"
else
    echo "➡️  Skipping SearXNG deployment."
fi

# ---------------------------------------------------------------------------
# Execute Module 2: Agent-Shield Core
# ---------------------------------------------------------------------------
if [[ "$DEPLOY_SHIELD" =~ ^[Yy]$ || "$DEPLOY_SHIELD" == "" ]]; then
    docker rm -f agent-shield-gateway || true

    if [ "$BUILD_FROM_SOURCE" = true ]; then
        echo "🔧 Building Agent-Shield from local source..."
        docker build -t agent-shield:latest .
        SHIELD_IMAGE="agent-shield:latest"
    else
        echo "📦 Pulling Agent-Shield from Docker Hub (startekenterprises/agent-shield:latest)..."
        docker pull startekenterprises/agent-shield:latest
        SHIELD_IMAGE="startekenterprises/agent-shield:latest"
    fi

    docker run -d \
      --name agent-shield-gateway \
      --network agent-shield-mesh \
      --network-alias agent-shield-gateway \
      -p 8000:8000 \
      -e SEARCH_PROVIDER=searxng \
      -e REAL_SEARXNG_URL=$REAL_SEARXNG_URL \
      -e OLLAMA_HOST=http://docker.internal \
      --add-host "host.docker.internal:host-gateway" \
      --restart always \
      $SHIELD_IMAGE

    echo "✅ Agent-Shield gateway running on port 8000"
    echo "🖥️  Dashboard available at: http://localhost:8000/dashboard"
else
    echo "➡️  Skipping Agent-Shield Core deployment."
fi

# ---------------------------------------------------------------------------
# Execute Module 3: OpenClaw
# Built from local source — always uses latest browser-use unless
# pinned in ./containers/openclaw/Dockerfile for compatibility.
# ---------------------------------------------------------------------------
if [[ "$DEPLOY_OPENCLAW" =~ ^[Yy]$ ]]; then
    echo "🎨 Building OpenClaw from local source (latest browser-use)..."

    cat << EOF > ./containers/openclaw/openclaw.json
{
  "agent": {
    "workspace_dir": "/app/workspace",
    "verbose": true
  },
  "browser": {
    "headless": true,
    "timeout": 30000
  },
  "search": {
    "provider": "searxng",
    "api_base": "http://agent-shield-gateway:8000/search"
  },
  "llm": {
    "provider": "openai_compatible",
    "api_base": "$DEFAULT_BASE",
    "model": "$DEFAULT_MODEL",
    "temperature": 0.0,
    "fallback_ollama_base": "$OLLAMA_TARGET"
  }
}
EOF

    docker build -t openclaw-agent:latest ./containers/openclaw/
    docker rm -f openclaw-agent-workspace || true
    mkdir -p ./workspace

    docker run -d \
      --name openclaw-agent-workspace \
      --network agent-shield-mesh \
      -v "$(pwd)/workspace:/app/workspace" \
      -e OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
      --add-host "host.docker.internal:host-gateway" \
      --restart always \
      openclaw-agent:latest

    echo "✅ OpenClaw workspace running"
else
    echo "➡️  Skipping OpenClaw Workspace deployment."
fi

# ---------------------------------------------------------------------------
# Save State
# ---------------------------------------------------------------------------
cat << EOF > "$STATE_FILE"
REAL_SEARXNG_URL=$REAL_SEARXNG_URL
SAVED_EXTERNAL_URL=$SAVED_EXTERNAL_URL
INSTALLED_SHIELD=true
INSTALLED_SEARXNG=true
INSTALLED_OPENCLAW=true
EOF

echo "======================================================="
echo "✅ INFRASTRUCTURE MESH SYNCHRONIZED!"
echo ""
echo "  🌐 SearXNG Search:     http://localhost:8088"
echo "  🛡️  Agent-Shield API:   http://localhost:8000"
echo "  🖥️  Dashboard:          http://localhost:8000/dashboard"
echo ""
echo "  Gateway upstream: $REAL_SEARXNG_URL"
echo "======================================================="
