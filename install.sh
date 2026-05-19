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
# LLM BACKEND REGISTRATION
# Supports: Local Ollama + OpenRouter + Google Gemini + Groq + Mistral
# OpenClaw will cycle through available keys in order, falling back as
# rate limits are hit. Ollama always acts as final local fallback.
# ---------------------------------------------------------------------------
echo ""
echo "--- 🔑 LLM BACKEND PROVIDER REGISTRATION ---"
echo ""
echo "Agent-Shield supports multiple free LLM providers and cycles between"
echo "them automatically as rate limits are reached. Register as many or as"
echo "few as you like. Local Ollama (if available) is always the final fallback."
echo ""
echo "Free API key signup links (no credit card required):"
echo "  🌐 OpenRouter  → https://openrouter.ai/sign-up          (30+ free models)"
echo "  🌐 Google      → https://aistudio.google.com/apikey     (1,500 req/day, Gemini Flash)"
echo "  🌐 Groq        → https://console.groq.com               (fastest free inference)"
echo "  🌐 Mistral     → https://console.mistral.ai             (1B tokens/month free)"
echo "  🌐 Cerebras    → https://cloud.cerebras.ai              (1M tokens/day free)"
echo ""

# Step A: Local Ollama
read -p "❓ Do you run a local Ollama instance on this host system? (y/N): " HAS_OLLAMA
if [[ "$HAS_OLLAMA" =~ ^[Yy]$ ]]; then
    OLLAMA_TARGET="http://docker.internal"
    echo "✅ Local Ollama mapped — will be used as final fallback."
else
    OLLAMA_TARGET=""
    echo "➡️  Skipping Ollama."
fi
echo ""

# Step B: OpenRouter
_load_existing_key() {
    local key_name="$1"
    if grep -q "^${key_name}=" "$ENV_FILE" 2>/dev/null; then
        grep "^${key_name}=" "$ENV_FILE" | cut -d'=' -f2
    fi
}

OPENROUTER_API_KEY=$(_load_existing_key "OPENROUTER_API_KEY")
if [ ! -z "$OPENROUTER_API_KEY" ]; then
    echo "💡 OpenRouter key already configured."
    read -p "❓ Replace OpenRouter API key? (y/N): " CHANGE_KEY
    if [[ "$CHANGE_KEY" =~ ^[Yy]$ ]]; then
        read -p "🔑 Paste new OpenRouter API Key: " OPENROUTER_API_KEY
    fi
else
    echo "OpenRouter gives you 30+ free models through a single key."
    echo "Sign up free at: https://openrouter.ai/sign-up"
    read -p "🔑 Paste your OpenRouter API Key (or Enter to skip): " OPENROUTER_API_KEY
fi
echo ""

# Step C: Google Gemini (AI Studio)
GOOGLE_API_KEY=$(_load_existing_key "GOOGLE_API_KEY")
if [ ! -z "$GOOGLE_API_KEY" ]; then
    echo "💡 Google Gemini key already configured."
    read -p "❓ Replace Google Gemini API key? (y/N): " CHANGE_GKEY
    if [[ "$CHANGE_GKEY" =~ ^[Yy]$ ]]; then
        read -p "🔑 Paste new Google Gemini API Key: " GOOGLE_API_KEY
    fi
else
    echo "Google AI Studio gives you 1,500 free requests/day on Gemini Flash."
    echo "Sign up free at: https://aistudio.google.com/apikey"
    read -p "🔑 Paste your Google Gemini API Key (or Enter to skip): " GOOGLE_API_KEY
fi
echo ""

# Step D: Groq
GROQ_API_KEY=$(_load_existing_key "GROQ_API_KEY")
if [ ! -z "$GROQ_API_KEY" ]; then
    echo "💡 Groq key already configured."
    read -p "❓ Replace Groq API key? (y/N): " CHANGE_GROQKEY
    if [[ "$CHANGE_GROQKEY" =~ ^[Yy]$ ]]; then
        read -p "🔑 Paste new Groq API Key: " GROQ_API_KEY
    fi
else
    echo "Groq is the fastest free LLM API — 300+ tokens/sec on Llama 70B."
    echo "Sign up free at: https://console.groq.com"
    read -p "🔑 Paste your Groq API Key (or Enter to skip): " GROQ_API_KEY
fi
echo ""

# Step E: Mistral
MISTRAL_API_KEY=$(_load_existing_key "MISTRAL_API_KEY")
if [ ! -z "$MISTRAL_API_KEY" ]; then
    echo "💡 Mistral key already configured."
    read -p "❓ Replace Mistral API key? (y/N): " CHANGE_MKEY
    if [[ "$CHANGE_MKEY" =~ ^[Yy]$ ]]; then
        read -p "🔑 Paste new Mistral API Key: " MISTRAL_API_KEY
    fi
else
    echo "Mistral gives you 1B free tokens/month across all Mistral models."
    echo "Sign up free at: https://console.mistral.ai"
    read -p "🔑 Paste your Mistral API Key (or Enter to skip): " MISTRAL_API_KEY
fi
echo ""

# Persist all keys to .env
for key in OPENROUTER_API_KEY GOOGLE_API_KEY GROQ_API_KEY MISTRAL_API_KEY; do
    sed -i "/^${key}=/d" "$ENV_FILE" 2>/dev/null || true
done
[ ! -z "$OPENROUTER_API_KEY" ] && echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" >> "$ENV_FILE"
[ ! -z "$GOOGLE_API_KEY" ]     && echo "GOOGLE_API_KEY=$GOOGLE_API_KEY"         >> "$ENV_FILE"
[ ! -z "$GROQ_API_KEY" ]       && echo "GROQ_API_KEY=$GROQ_API_KEY"             >> "$ENV_FILE"
[ ! -z "$MISTRAL_API_KEY" ]    && echo "MISTRAL_API_KEY=$MISTRAL_API_KEY"       >> "$ENV_FILE"

# ---------------------------------------------------------------------------
# Determine primary model and base URL for OpenClaw config
# Priority: OpenRouter → Google → Groq → Mistral → Ollama
# ---------------------------------------------------------------------------
if [ ! -z "$OPENROUTER_API_KEY" ]; then
    DEFAULT_MODEL="anthropic/claude-3.5-sonnet"
    DEFAULT_BASE="https://openrouter.ai/api/v1"
    DEFAULT_KEY="$OPENROUTER_API_KEY"
    echo "🚀 Primary: OpenRouter → claude-3.5-sonnet"
elif [ ! -z "$GOOGLE_API_KEY" ]; then
    DEFAULT_MODEL="gemini-2.0-flash"
    DEFAULT_BASE="https://generativelanguage.googleapis.com/v1beta/openai"
    DEFAULT_KEY="$GOOGLE_API_KEY"
    echo "🚀 Primary: Google AI Studio → gemini-2.0-flash"
elif [ ! -z "$GROQ_API_KEY" ]; then
    DEFAULT_MODEL="llama-3.3-70b-versatile"
    DEFAULT_BASE="https://api.groq.com/openai/v1"
    DEFAULT_KEY="$GROQ_API_KEY"
    echo "🚀 Primary: Groq → llama-3.3-70b-versatile"
elif [ ! -z "$MISTRAL_API_KEY" ]; then
    DEFAULT_MODEL="mistral-small-latest"
    DEFAULT_BASE="https://api.mistral.ai/v1"
    DEFAULT_KEY="$MISTRAL_API_KEY"
    echo "🚀 Primary: Mistral → mistral-small-latest"
elif [ ! -z "$OLLAMA_TARGET" ]; then
    DEFAULT_MODEL="qwen2.5-coder-7b:128k"
    DEFAULT_BASE="$OLLAMA_TARGET/v1"
    DEFAULT_KEY="ollama"
    echo "🚀 Primary: Local Ollama → qwen2.5-coder-7b:128k"
else
    echo "⚠️  No LLM provider configured. OpenClaw will run in search-only mode."
    DEFAULT_MODEL=""
    DEFAULT_BASE=""
    DEFAULT_KEY=""
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
    echo "💡 SearXNG is currently RUNNING on port 8088."
    read -p "❓ Force STOP and REINSTALL SearXNG? (y/N): " DEPLOY_SEARXNG
else
    read -p "❓ Deploy local SearXNG private search container on port 8088? (Y/n): " DEPLOY_SEARXNG
fi

if [[ "$DEPLOY_SEARXNG" =~ ^[Nn]$ ]]; then
    if [ -z "$SAVED_EXTERNAL_URL" ]; then
        read -p "  👉 Enter your existing SearXNG URL (e.g. http://192.168.2.42:8080): " REAL_SEARXNG_URL
    else
        read -p "  👉 SearXNG URL [Default: $SAVED_EXTERNAL_URL]: " NEW_EXT_URL
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
# Default: Pull from Docker Hub startekenterprises/agent-shield:latest
# --build flag: Build from local source
# ---------------------------------------------------------------------------
echo "--- 🛡️  MODULE 2: AGENT-SHIELD FIREWALL CORE ---"
if is_active "agent-shield-gateway"; then
    echo "💡 Agent-Shield is currently RUNNING on port 8000."
    read -p "❓ Force STOP and REINSTALL Agent-Shield? (y/N): " DEPLOY_SHIELD
else
    read -p "❓ Deploy Agent-Shield Security Firewall on port 8000? (Y/n): " DEPLOY_SHIELD
fi

echo ""

# ---------------------------------------------------------------------------
# Module 3: OpenClaw Agent Workspace
# Built from local source (./containers/openclaw/) using latest browser-use.
# Wired to cycle through all configured LLM providers on rate limit hits.
# ---------------------------------------------------------------------------
echo "--- 🎨 MODULE 3: HYPERCONVERGED AGENT WORKSPACE ---"
if is_active "openclaw-agent-workspace"; then
    echo "💡 OpenClaw Workspace is currently RUNNING."
    read -p "❓ Force STOP and REINSTALL OpenClaw? (y/N): " DEPLOY_OPENCLAW
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
    echo "What would you like your agent to work on?"
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
echo ""
echo "======================================================="
echo "🚀 Executing container orchestration..."

docker network create agent-shield-mesh 2>/dev/null || true

# ---------------------------------------------------------------------------
# Execute Module 1: SearXNG
# ---------------------------------------------------------------------------
if [ "$RUN_SEARXNG_ACTION" = true ] && [[ "$DEPLOY_SEARXNG" =~ ^[Yy]$ || "$DEPLOY_SEARXNG" == "" ]]; then
    echo "📦 Pulling SearXNG from Docker Hub..."
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
        echo "📦 Pulling Agent-Shield from Docker Hub..."
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
      -e OLLAMA_HOST=${OLLAMA_TARGET:-""} \
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
# Generates a multi-provider failover config so OpenClaw cycles through
# all registered API keys before falling back to local Ollama.
# ---------------------------------------------------------------------------
if [[ "$DEPLOY_OPENCLAW" =~ ^[Yy]$ ]]; then
    echo "🎨 Building OpenClaw from local source..."

    # Build provider list for failover cycling
    PROVIDERS_JSON="[]"

    if [ ! -z "$OPENROUTER_API_KEY" ]; then
        PROVIDERS_JSON=$(echo "$PROVIDERS_JSON" | python3 -c "
import sys, json
providers = json.load(sys.stdin)
providers.append({
    'name': 'openrouter',
    'api_base': 'https://openrouter.ai/api/v1',
    'api_key': '$OPENROUTER_API_KEY',
    'model': 'anthropic/claude-3.5-sonnet'
})
print(json.dumps(providers))
")
    fi

    if [ ! -z "$GOOGLE_API_KEY" ]; then
        PROVIDERS_JSON=$(echo "$PROVIDERS_JSON" | python3 -c "
import sys, json
providers = json.load(sys.stdin)
providers.append({
    'name': 'google',
    'api_base': 'https://generativelanguage.googleapis.com/v1beta/openai',
    'api_key': '$GOOGLE_API_KEY',
    'model': 'gemini-2.0-flash'
})
print(json.dumps(providers))
")
    fi

    if [ ! -z "$GROQ_API_KEY" ]; then
        PROVIDERS_JSON=$(echo "$PROVIDERS_JSON" | python3 -c "
import sys, json
providers = json.load(sys.stdin)
providers.append({
    'name': 'groq',
    'api_base': 'https://api.groq.com/openai/v1',
    'api_key': '$GROQ_API_KEY',
    'model': 'llama-3.3-70b-versatile'
})
print(json.dumps(providers))
")
    fi

    if [ ! -z "$MISTRAL_API_KEY" ]; then
        PROVIDERS_JSON=$(echo "$PROVIDERS_JSON" | python3 -c "
import sys, json
providers = json.load(sys.stdin)
providers.append({
    'name': 'mistral',
    'api_base': 'https://api.mistral.ai/v1',
    'api_key': '$MISTRAL_API_KEY',
    'model': 'mistral-small-latest'
})
print(json.dumps(providers))
")
    fi

    if [ ! -z "$OLLAMA_TARGET" ]; then
        PROVIDERS_JSON=$(echo "$PROVIDERS_JSON" | python3 -c "
import sys, json
providers = json.load(sys.stdin)
providers.append({
    'name': 'ollama',
    'api_base': '$OLLAMA_TARGET/v1',
    'api_key': 'ollama',
    'model': 'qwen2.5-coder-7b:128k'
})
print(json.dumps(providers))
")
    fi

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
    "api_key": "$DEFAULT_KEY",
    "model": "$DEFAULT_MODEL",
    "temperature": 0.0,
    "failover_providers": $PROVIDERS_JSON
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
      -e GOOGLE_API_KEY="$GOOGLE_API_KEY" \
      -e GROQ_API_KEY="$GROQ_API_KEY" \
      -e MISTRAL_API_KEY="$MISTRAL_API_KEY" \
      --add-host "host.docker.internal:host-gateway" \
      --restart always \
      openclaw-agent:latest

    echo "✅ OpenClaw workspace running with $(echo $PROVIDERS_JSON | python3 -c "import sys,json; print(len(json.load(sys.stdin)))") provider(s) configured"
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
