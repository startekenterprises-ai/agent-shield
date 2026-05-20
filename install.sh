#!/bin/bash
set -e

STATE_FILE="./.shield_state"
ENV_FILE="./.env"

echo ""
echo "🛡️  Agent-Shield: Intelligent AI Security Mesh Installer"
echo "======================================================="
echo ""

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
# Flags:
#   --build   Build agent-shield from local source instead of Docker Hub
#   --test    Run on alternate ports/names, leaving live stack untouched
# ---------------------------------------------------------------------------
BUILD_FROM_SOURCE=false
TEST_MODE=false

for arg in "$@"; do
    case $arg in
        --build) BUILD_FROM_SOURCE=true; echo "🔧 Build-from-source mode enabled." ;;
        --test)  TEST_MODE=true ;;
    esac
done

if [ "$TEST_MODE" = true ]; then
    echo "🧪 TEST MODE — alternate containers and ports, live stack untouched."
    echo ""
    SHIELD_CONTAINER="agent-shield-test"
    SEARXNG_CONTAINER="searxng-test"
    OPENCLAW_CONTAINER="openclaw-test"
    MESH_NETWORK="agent-shield-test-mesh"
    SHIELD_PORT=18000
    SEARXNG_PORT=18088
    REAL_SEARXNG_URL="http://searxng-test:8080"
else
    SHIELD_CONTAINER="agent-shield-gateway"
    SEARXNG_CONTAINER="searxng-private-mesh"
    OPENCLAW_CONTAINER="openclaw-agent-workspace"
    MESH_NETWORK="agent-shield-mesh"
    SHIELD_PORT=8000
    SEARXNG_PORT=8088
fi

# ---------------------------------------------------------------------------
# SECURITY NOTICE
# ---------------------------------------------------------------------------
echo "🔐 SECURITY NOTICE"
echo "   Your API keys are stored in .env on this machine only and are"
echo "   never transmitted to Agent-Shield servers. All images pull from"
echo "   official Docker Hub repos. This installer is fully open source:"
echo "   https://github.com/startekenterprises-ai/agent-shield"
echo ""

# ---------------------------------------------------------------------------
# Helper: load an existing key from .env
# ---------------------------------------------------------------------------
_load_existing_key() {
    grep "^${1}=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2 || echo ""
}

# Helper: check if a named container is running
is_active() {
    docker ps --format '{{.Names}}' | grep -q "^$1$"
}

# ===========================================================================
# STEP 0: OLLAMA — Local or Cloud
# ===========================================================================
echo "--- 🦙 STEP 0: OLLAMA (LOCAL AI ENGINE) ---"
echo ""
echo "Ollama lets you run AI models locally or access cloud models."
echo "No credit card required. Models run fully offline after download."
echo ""

OLLAMA_TARGET=""
HAS_OLLAMA=false

if command -v ollama &> /dev/null; then
    echo "✅ Ollama is already installed."
    HAS_OLLAMA=true

    # Check if logged in
    OLLAMA_USER=$(ollama whoami 2>/dev/null || echo "")
    if [ ! -z "$OLLAMA_USER" ]; then
        echo "✅ Logged in to Ollama as: $OLLAMA_USER"
    else
        echo "💡 You are not logged in to Ollama."
        echo "   Login unlocks private and gated cloud models at ollama.com."
        echo "   Register free at: https://ollama.com/signup"
        read -p "❓ Login to Ollama now? (y/N): " DO_OLLAMA_LOGIN
        if [[ "$DO_OLLAMA_LOGIN" =~ ^[Yy]$ ]]; then
            ollama login
        fi
    fi

    echo ""
    echo "Currently installed models:"
    ollama list
    echo ""
    read -p "❓ Pull a model for Agent-Shield to use? (y/N): " PULL_MODEL
    if [[ "$PULL_MODEL" =~ ^[Yy]$ ]]; then
        echo "  1) qwen2.5-coder:7b    (recommended — fast, great for code tasks, ~4GB)"
        echo "  2) llama3.3:70b        (most capable — needs 40GB+ VRAM)"
        echo "  3) mistral:7b          (balanced speed and quality, ~4GB)"
        echo "  4) deepseek-coder:6.7b (strong at code, lightweight, ~4GB)"
        echo "  5) Skip"
        read -p "Choice [1-5]: " MODEL_CHOICE
        case $MODEL_CHOICE in
            1) ollama pull qwen2.5-coder:7b ;;
            2) ollama pull llama3.3:70b ;;
            3) ollama pull mistral:7b ;;
            4) ollama pull deepseek-coder:6.7b ;;
        esac
    fi
    OLLAMA_TARGET="http://docker.internal"

else
    echo "💡 Ollama is not installed on this system."
    echo ""
    read -p "❓ Install Ollama? (y/N): " DO_INSTALL_OLLAMA
    if [[ "$DO_INSTALL_OLLAMA" =~ ^[Yy]$ ]]; then
        echo ""
        echo "🔐 This will run the official install script from https://ollama.com/install.sh"
        read -p "❓ Proceed? (y/N): " CONFIRM_INSTALL
        if [[ "$CONFIRM_INSTALL" =~ ^[Yy]$ ]]; then
            curl -fsSL https://ollama.com/install.sh | sh
            HAS_OLLAMA=true
            echo ""
            echo "✅ Ollama installed. No account required for local models."
            echo ""
            echo "   Want to access private/gated cloud models on Ollama.com?"
            echo "   Register free at: https://ollama.com/signup"
            read -p "❓ Login to Ollama now? (y/N): " DO_OLLAMA_LOGIN
            if [[ "$DO_OLLAMA_LOGIN" =~ ^[Yy]$ ]]; then
                ollama login
            fi
            echo ""
            echo "Select a model to pull:"
            echo "  1) qwen2.5-coder:7b    (recommended — fast, code-focused, ~4GB)"
            echo "  2) llama3.3:70b        (most capable — needs 40GB+ VRAM)"
            echo "  3) mistral:7b          (balanced, ~4GB)"
            echo "  4) deepseek-coder:6.7b (lightweight code model, ~4GB)"
            echo "  5) Skip for now"
            read -p "Choice [1-5]: " MODEL_CHOICE
            case $MODEL_CHOICE in
                1) ollama pull qwen2.5-coder:7b ;;
                2) ollama pull llama3.3:70b ;;
                3) ollama pull mistral:7b ;;
                4) ollama pull deepseek-coder:6.7b ;;
            esac
            OLLAMA_TARGET="http://docker.internal"
        fi
    fi
fi

echo ""

# ===========================================================================
# STEP 1: CLOUD LLM PROVIDERS (Optional)
# ===========================================================================
echo "--- 🔑 STEP 1: CLOUD LLM PROVIDERS (OPTIONAL) ---"
echo ""
read -p "❓ Do you want to configure cloud LLM API keys? (y/N): " CONFIGURE_CLOUD

OPENROUTER_API_KEY=""
GOOGLE_API_KEY=""
GROQ_API_KEY=""
MISTRAL_API_KEY=""

if [[ "$CONFIGURE_CLOUD" =~ ^[Yy]$ ]]; then
    echo ""
    echo "All providers below are free with no credit card required."
    echo "Agent-Shield cycles through them automatically as rate limits are hit."
    echo ""
    echo "  1) OpenRouter  — 30+ free models via one key  → https://openrouter.ai/sign-up"
    echo "  2) Google      — 1,500 req/day Gemini Flash   → https://aistudio.google.com/apikey"
    echo "  3) Groq        — Fastest free inference       → https://console.groq.com"
    echo "  4) Mistral     — 1B tokens/month free         → https://console.mistral.ai"
    echo "  5) Cerebras    — 1M tokens/day free           → https://cloud.cerebras.ai"
    echo ""
    echo "Which providers do you have keys for? (select all that apply)"
    echo "Enter numbers separated by spaces, or Enter to skip all:"
    read -p "Providers [e.g. 1 3]: " PROVIDER_CHOICES

    for choice in $PROVIDER_CHOICES; do
        case $choice in
            1)
                existing=$(_load_existing_key "OPENROUTER_API_KEY")
                if [ ! -z "$existing" ]; then
                    echo "💡 OpenRouter key already saved."
                    read -p "   Replace it? (y/N): " R
                    if [[ "$R" =~ ^[Yy]$ ]]; then
                        read -p "🔑 OpenRouter API Key: " OPENROUTER_API_KEY
                    else
                        OPENROUTER_API_KEY=$existing
                    fi
                else
                    read -p "🔑 OpenRouter API Key: " OPENROUTER_API_KEY
                fi
                ;;
            2)
                existing=$(_load_existing_key "GOOGLE_API_KEY")
                if [ ! -z "$existing" ]; then
                    echo "💡 Google key already saved."
                    read -p "   Replace it? (y/N): " R
                    if [[ "$R" =~ ^[Yy]$ ]]; then
                        read -p "🔑 Google Gemini API Key: " GOOGLE_API_KEY
                    else
                        GOOGLE_API_KEY=$existing
                    fi
                else
                    read -p "🔑 Google Gemini API Key: " GOOGLE_API_KEY
                fi
                ;;
            3)
                existing=$(_load_existing_key "GROQ_API_KEY")
                if [ ! -z "$existing" ]; then
                    echo "💡 Groq key already saved."
                    read -p "   Replace it? (y/N): " R
                    if [[ "$R" =~ ^[Yy]$ ]]; then
                        read -p "🔑 Groq API Key: " GROQ_API_KEY
                    else
                        GROQ_API_KEY=$existing
                    fi
                else
                    read -p "🔑 Groq API Key: " GROQ_API_KEY
                fi
                ;;
            4)
                existing=$(_load_existing_key "MISTRAL_API_KEY")
                if [ ! -z "$existing" ]; then
                    echo "💡 Mistral key already saved."
                    read -p "   Replace it? (y/N): " R
                    if [[ "$R" =~ ^[Yy]$ ]]; then
                        read -p "🔑 Mistral API Key: " MISTRAL_API_KEY
                    else
                        MISTRAL_API_KEY=$existing
                    fi
                else
                    read -p "🔑 Mistral API Key: " MISTRAL_API_KEY
                fi
                ;;
            5)
                echo "💡 Cerebras support coming in v1.1 — sign up at https://cloud.cerebras.ai"
                ;;
        esac
    done
else
    # Load any previously saved keys silently
    OPENROUTER_API_KEY=$(_load_existing_key "OPENROUTER_API_KEY")
    GOOGLE_API_KEY=$(_load_existing_key "GOOGLE_API_KEY")
    GROQ_API_KEY=$(_load_existing_key "GROQ_API_KEY")
    MISTRAL_API_KEY=$(_load_existing_key "MISTRAL_API_KEY")
    if [ ! -z "$OPENROUTER_API_KEY" ] || [ ! -z "$GOOGLE_API_KEY" ] || [ ! -z "$GROQ_API_KEY" ] || [ ! -z "$MISTRAL_API_KEY" ]; then
        echo "💡 Using previously saved cloud API keys."
    fi
fi

# Persist keys
for key in OPENROUTER_API_KEY GOOGLE_API_KEY GROQ_API_KEY MISTRAL_API_KEY; do
    sed -i "/^${key}=/d" "$ENV_FILE" 2>/dev/null || true
done
[ ! -z "$OPENROUTER_API_KEY" ] && echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" >> "$ENV_FILE"
[ ! -z "$GOOGLE_API_KEY" ]     && echo "GOOGLE_API_KEY=$GOOGLE_API_KEY"         >> "$ENV_FILE"
[ ! -z "$GROQ_API_KEY" ]       && echo "GROQ_API_KEY=$GROQ_API_KEY"             >> "$ENV_FILE"
[ ! -z "$MISTRAL_API_KEY" ]    && echo "MISTRAL_API_KEY=$MISTRAL_API_KEY"       >> "$ENV_FILE"

# Determine primary provider (priority: OpenRouter → Google → Groq → Mistral → Ollama)
if [ ! -z "$OPENROUTER_API_KEY" ]; then
    DEFAULT_MODEL="anthropic/claude-3.5-sonnet"
    DEFAULT_BASE="https://openrouter.ai/api/v1"
    DEFAULT_KEY="$OPENROUTER_API_KEY"
    echo "🚀 Primary LLM: OpenRouter → claude-3.5-sonnet"
elif [ ! -z "$GOOGLE_API_KEY" ]; then
    DEFAULT_MODEL="gemini-2.0-flash"
    DEFAULT_BASE="https://generativelanguage.googleapis.com/v1beta/openai"
    DEFAULT_KEY="$GOOGLE_API_KEY"
    echo "🚀 Primary LLM: Google → gemini-2.0-flash"
elif [ ! -z "$GROQ_API_KEY" ]; then
    DEFAULT_MODEL="llama-3.3-70b-versatile"
    DEFAULT_BASE="https://api.groq.com/openai/v1"
    DEFAULT_KEY="$GROQ_API_KEY"
    echo "🚀 Primary LLM: Groq → llama-3.3-70b-versatile"
elif [ ! -z "$MISTRAL_API_KEY" ]; then
    DEFAULT_MODEL="mistral-small-latest"
    DEFAULT_BASE="https://api.mistral.ai/v1"
    DEFAULT_KEY="$MISTRAL_API_KEY"
    echo "🚀 Primary LLM: Mistral → mistral-small-latest"
elif [ "$HAS_OLLAMA" = true ] && [ ! -z "$OLLAMA_TARGET" ]; then
    DEFAULT_MODEL="qwen2.5-coder:7b"
    DEFAULT_BASE="$OLLAMA_TARGET/v1"
    DEFAULT_KEY="ollama"
    echo "🚀 Primary LLM: Local Ollama → qwen2.5-coder:7b"
else
    DEFAULT_MODEL=""
    DEFAULT_BASE=""
    DEFAULT_KEY=""
    echo "⚠️  No LLM provider configured. OpenClaw will run in search-only mode."
fi

echo ""

# ===========================================================================
# MODULE 1: SearXNG
# ===========================================================================
echo "--- 📦 MODULE 1: PRIVATE SEARCH ENGINE ---"
if is_active "$SEARXNG_CONTAINER"; then
    echo "💡 SearXNG ($SEARXNG_CONTAINER) is RUNNING on port $SEARXNG_PORT."
    read -p "❓ Force STOP and REINSTALL? (y/N): " DEPLOY_SEARXNG
else
    read -p "❓ Deploy private SearXNG on port $SEARXNG_PORT? (Y/n): " DEPLOY_SEARXNG
fi

if [[ "$DEPLOY_SEARXNG" =~ ^[Nn]$ ]]; then
    if [ -z "$SAVED_EXTERNAL_URL" ]; then
        read -p "  👉 Enter existing SearXNG URL (e.g. http://192.168.2.42:8080): " REAL_SEARXNG_URL
    else
        read -p "  👉 SearXNG URL [Default: $SAVED_EXTERNAL_URL]: " NEW_EXT_URL
        REAL_SEARXNG_URL=${NEW_EXT_URL:-$SAVED_EXTERNAL_URL}
    fi
    SAVED_EXTERNAL_URL=$REAL_SEARXNG_URL
    RUN_SEARXNG_ACTION=false
else
    if [ "$TEST_MODE" = false ]; then
        REAL_SEARXNG_URL="http://searxng-private-mesh:8080"
    fi
    RUN_SEARXNG_ACTION=true
fi

echo ""

# ===========================================================================
# MODULE 2: Agent-Shield
# ===========================================================================
echo "--- 🛡️  MODULE 2: AGENT-SHIELD FIREWALL CORE ---"
if is_active "$SHIELD_CONTAINER"; then
    echo "💡 Agent-Shield ($SHIELD_CONTAINER) is RUNNING on port $SHIELD_PORT."
    read -p "❓ Force STOP and REINSTALL? (y/N): " DEPLOY_SHIELD
else
    read -p "❓ Deploy Agent-Shield on port $SHIELD_PORT? (Y/n): " DEPLOY_SHIELD
fi

echo ""

# ===========================================================================
# MODULE 3: OpenClaw
# ===========================================================================
echo "--- 🎨 MODULE 3: OPENCLAW AGENT WORKSPACE ---"
echo "   OpenClaw is a full browser-use AI coding agent, sandboxed inside"
echo "   the Agent-Shield security mesh. All web traffic is sanitized."
echo ""
if is_active "$OPENCLAW_CONTAINER"; then
    echo "💡 OpenClaw ($OPENCLAW_CONTAINER) is RUNNING."
    read -p "❓ Force STOP and REINSTALL? (y/N): " DEPLOY_OPENCLAW
else
    read -p "❓ Deploy OpenClaw Agent Workspace? (y/N): " DEPLOY_OPENCLAW
fi

echo ""

# ===========================================================================
# MODULE 4: Community Threat Mesh
# ===========================================================================
echo "--- 🤝 MODULE 4: COMMUNITY THREAT MESH (OPTIONAL) ---"
read -p "❓ Contribute anonymized threat patterns to improve Agent-Shield? (y/N): " CONTRIBUTE
if [[ "$CONTRIBUTE" =~ ^[Yy]$ ]]; then
    echo "  What would you like your agent to work on?"
    echo "  1) Hunt for new prompt injection patterns"
    echo "  2) Test DLP evasion signatures"
    echo "  3) Expand regex detection coverage"
    echo "  4) Other (skip for now)"
    read -p "  Choice [1-4]: " CONTRIBUTION_TYPE
    sed -i "/^CONTRIBUTION_TYPE=/d" "$ENV_FILE" 2>/dev/null || true
    echo "CONTRIBUTION_TYPE=$CONTRIBUTION_TYPE" >> "$ENV_FILE"
    echo "✅ Contribution preference saved."
fi

# ===========================================================================
# ORCHESTRATION
# ===========================================================================
echo ""
echo "======================================================="
[ "$TEST_MODE" = true ] && echo "🧪 DEPLOYING TEST STACK..." || echo "🚀 Deploying Agent-Shield mesh..."
echo ""

docker network create $MESH_NETWORK 2>/dev/null || true

# ---------------------------------------------------------------------------
# Module 1: SearXNG
# ---------------------------------------------------------------------------
if [ "$RUN_SEARXNG_ACTION" = true ] && [[ "$DEPLOY_SEARXNG" =~ ^[Yy]$ || "$DEPLOY_SEARXNG" == "" ]]; then
    echo "📦 Pulling SearXNG (searxng/searxng:latest)..."
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
    docker rm -f $SEARXNG_CONTAINER 2>/dev/null || true
    docker run -d \
      --name $SEARXNG_CONTAINER \
      --network $MESH_NETWORK \
      --network-alias $SEARXNG_CONTAINER \
      -v "$(pwd)/config/searxng:/etc/searxng:ro" \
      -p ${SEARXNG_PORT}:8080 \
      --restart always \
      searxng/searxng:latest
    echo "✅ SearXNG running on port $SEARXNG_PORT"
else
    echo "➡️  Skipping SearXNG."
fi

# ---------------------------------------------------------------------------
# Module 2: Agent-Shield
# ---------------------------------------------------------------------------
if [[ "$DEPLOY_SHIELD" =~ ^[Yy]$ || "$DEPLOY_SHIELD" == "" ]]; then
    docker rm -f $SHIELD_CONTAINER 2>/dev/null || true

    if [ "$BUILD_FROM_SOURCE" = true ]; then
        echo "🔧 Building Agent-Shield from local source..."
        docker buildx build -t agent-shield:latest . 2>/dev/null || docker build -t agent-shield:latest .
        SHIELD_IMAGE="agent-shield:latest"
    else
        echo "📦 Pulling Agent-Shield (startekenterprises/agent-shield:latest)..."
        docker pull startekenterprises/agent-shield:latest
        SHIELD_IMAGE="startekenterprises/agent-shield:latest"
    fi

    docker run -d \
      --name $SHIELD_CONTAINER \
      --network $MESH_NETWORK \
      --network-alias $SHIELD_CONTAINER \
      -p ${SHIELD_PORT}:8000 \
      -e SEARCH_PROVIDER=searxng \
      -e REAL_SEARXNG_URL="$REAL_SEARXNG_URL" \
      -e OLLAMA_HOST="${OLLAMA_TARGET:-""}" \
      --add-host "host.docker.internal:host-gateway" \
      --restart always \
      $SHIELD_IMAGE

    echo "✅ Agent-Shield running on port $SHIELD_PORT"
    echo "🖥️  Dashboard: http://localhost:${SHIELD_PORT}/dashboard"
else
    echo "➡️  Skipping Agent-Shield."
fi

# ---------------------------------------------------------------------------
# Module 3: OpenClaw
# Built from local source. Uses --no-sandbox and virtual display flags
# so browser-use works correctly in a headless container environment.
# ---------------------------------------------------------------------------
if [[ "$DEPLOY_OPENCLAW" =~ ^[Yy]$ ]]; then
    echo "🎨 Building OpenClaw from local source..."

    # Build failover provider list
    PROVIDERS_JSON="[]"
    build_providers() {
        local j="$1"
        [ ! -z "$OPENROUTER_API_KEY" ] && j=$(echo "$j" | python3 -c "
import sys,json; p=json.load(sys.stdin)
p.append({'name':'openrouter','api_base':'https://openrouter.ai/api/v1','api_key':'$OPENROUTER_API_KEY','model':'anthropic/claude-3.5-sonnet'})
print(json.dumps(p))")
        [ ! -z "$GOOGLE_API_KEY" ] && j=$(echo "$j" | python3 -c "
import sys,json; p=json.load(sys.stdin)
p.append({'name':'google','api_base':'https://generativelanguage.googleapis.com/v1beta/openai','api_key':'$GOOGLE_API_KEY','model':'gemini-2.0-flash'})
print(json.dumps(p))")
        [ ! -z "$GROQ_API_KEY" ] && j=$(echo "$j" | python3 -c "
import sys,json; p=json.load(sys.stdin)
p.append({'name':'groq','api_base':'https://api.groq.com/openai/v1','api_key':'$GROQ_API_KEY','model':'llama-3.3-70b-versatile'})
print(json.dumps(p))")
        [ ! -z "$MISTRAL_API_KEY" ] && j=$(echo "$j" | python3 -c "
import sys,json; p=json.load(sys.stdin)
p.append({'name':'mistral','api_base':'https://api.mistral.ai/v1','api_key':'$MISTRAL_API_KEY','model':'mistral-small-latest'})
print(json.dumps(p))")
        [ ! -z "$OLLAMA_TARGET" ] && j=$(echo "$j" | python3 -c "
import sys,json; p=json.load(sys.stdin)
p.append({'name':'ollama','api_base':'$OLLAMA_TARGET/v1','api_key':'ollama','model':'qwen2.5-coder:7b'})
print(json.dumps(p))")
        echo "$j"
    }
    PROVIDERS_JSON=$(build_providers "$PROVIDERS_JSON")
    PROVIDER_COUNT=$(echo "$PROVIDERS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    OPENCLAW_SEARCH_BASE="http://${SHIELD_CONTAINER}:8000/search"

    cat << EOF > ./containers/openclaw/openclaw.json
{
  "agent": {
    "workspace_dir": "/app/workspace",
    "verbose": true
  },
  "browser": {
    "headless": true,
    "timeout": 30000,
    "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
  },
  "search": {
    "provider": "searxng",
    "api_base": "$OPENCLAW_SEARCH_BASE"
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

    docker buildx build -t openclaw-agent:latest ./containers/openclaw/ 2>/dev/null || \
    docker build -t openclaw-agent:latest ./containers/openclaw/

    docker rm -f $OPENCLAW_CONTAINER 2>/dev/null || true
    mkdir -p ./workspace

    docker run -d \
      --name $OPENCLAW_CONTAINER \
      --network $MESH_NETWORK \
      -v "$(pwd)/workspace:/app/workspace" \
      -e OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-""}" \
      -e GOOGLE_API_KEY="${GOOGLE_API_KEY:-""}" \
      -e GROQ_API_KEY="${GROQ_API_KEY:-""}" \
      -e MISTRAL_API_KEY="${MISTRAL_API_KEY:-""}" \
      --shm-size=2g \
      --add-host "host.docker.internal:host-gateway" \
      --restart always \
      openclaw-agent:latest

    echo "✅ OpenClaw running — $PROVIDER_COUNT LLM provider(s) configured"
else
    echo "➡️  Skipping OpenClaw."
fi

# ---------------------------------------------------------------------------
# Save State
# ---------------------------------------------------------------------------
cat << EOF > "$STATE_FILE"
REAL_SEARXNG_URL=$REAL_SEARXNG_URL
SAVED_EXTERNAL_URL=${SAVED_EXTERNAL_URL:-""}
INSTALLED_SHIELD=true
INSTALLED_SEARXNG=true
INSTALLED_OPENCLAW=true
EOF

echo ""
echo "======================================================="
if [ "$TEST_MODE" = true ]; then
    echo "🧪 TEST STACK RUNNING"
    echo ""
    echo "  🌐 SearXNG (test):      http://localhost:$SEARXNG_PORT"
    echo "  🛡️  Agent-Shield (test): http://localhost:$SHIELD_PORT"
    echo "  🖥️  Dashboard (test):    http://localhost:$SHIELD_PORT/dashboard"
    echo ""
    echo "  Live stack on 8000/8088 is untouched."
    echo ""
    echo "  Tear down test stack when done:"
    echo "  docker rm -f $SHIELD_CONTAINER $SEARXNG_CONTAINER $OPENCLAW_CONTAINER"
else
    echo "✅ AGENT-SHIELD MESH RUNNING"
    echo ""
    echo "  🌐 SearXNG:       http://localhost:$SEARXNG_PORT"
    echo "  🛡️  Agent-Shield:  http://localhost:$SHIELD_PORT"
    echo "  🖥️  Dashboard:     http://localhost:$SHIELD_PORT/dashboard"
fi
echo ""
echo "  Gateway upstream: $REAL_SEARXNG_URL"
echo "======================================================="
