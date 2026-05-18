#!/bin/bash
set -e

STATE_FILE="./.shield_state"
echo "🛡️  Agent-Shield: Modular AI Security Mesh Installer"
echo "======================================================="

# Load saved backend parameters if they exist
if [ -f "$STATE_FILE" ]; then
    source "$STATE_FILE"
else
    REAL_SEARXNG_URL="http://192.168.2.42:8088"
fi

# Helper function to check if a specific container is running actively
is_active() {
    docker ps --format '{{.Names}}' | grep -q "^$1$"
}

# ---------------------------------------------------------------------------
# Module 1: SearXNG Private Search Engine
# ---------------------------------------------------------------------------
echo "--- 📦 MODULE 1: UPSTREAM SEARCH ENGINE ---"
if is_active "searxng-private-mesh"; then
    echo "💡 Local SearXNG container is currently RUNNING on port 8088."
    read -p "❓ Force STOP and REINSTALL Local SearXNG? (y/N): " DEPLOY_SEARXNG
else
    read -p "❓ Deploy fresh local SearXNG container inside WSL on port 8088? (Y/n): " DEPLOY_SEARXNG
fi

# Handle configuration if they reject the local option
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
    REAL_SEARXNG_URL="http://192.168.2.42:8088"
    RUN_SEARXNG_ACTION=true
fi

echo ""
# ---------------------------------------------------------------------------
# Module 2: Agent-Shield Security Gateway Core
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
# Module 3: OpenClaw Agent Workspace Space
# ---------------------------------------------------------------------------
echo "--- 🎨 MODULE 3: HYPERCONVERGED AGENT WORKSPACE ---"
if is_active "openclaw-agent-workspace"; then
    echo "💡 OpenClaw Workspace container is currently RUNNING."
    read -p "❓ Force STOP and REINSTALL OpenClaw Workspace? (y/N): " DEPLOY_OPENCLAW
else
    read -p "❓ Bundle in a containerized OpenClaw Agent Workspace? (y/N): " DEPLOY_OPENCLAW
fi

# ===========================================================================
# Execution Block
# ===========================================================================
echo "======================================================="
echo "🚀 Executing targeted container orchestration lifecycle..."

# Execute SearXNG Block
if [ "$RUN_SEARXNG_ACTION" = true ] && [[ "$DEPLOY_SEARXNG" =~ ^[Yy]$ || "$DEPLOY_SEARXNG" == "" ]]; then
    echo "📦 Re-allocating isolated SearXNG Private Mesh container..."
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
      -v "$(pwd)/config/searxng:/etc/searxng:ro" \
      -p 8088:8080 \
      --restart always \
      searxng/searxng:latest
else
    echo "➡️  Skipping SearXNG structural changes."
fi

# Execute Agent-Shield Block
if [[ "$DEPLOY_SHIELD" =~ ^[Yy]$ || "$DEPLOY_SHIELD" == "" ]]; then
    echo "🛠️  Compiling localized Agent-Shield container image layers..."
    docker build -t agent-shield:latest .
    docker rm -f agent-shield-gateway || true
    docker run -d \
      --name agent-shield-gateway \
      -p 8000:8000 \
      -e SEARCH_PROVIDER=searxng \
      -e REAL_SEARXNG_URL=$REAL_SEARXNG_URL \
      -e OLLAMA_HOST=http://192.168.2.42:11434 \
      --restart always \
      agent-shield:latest
else
    echo "➡️  Skipping Agent-Shield Core structural changes."
fi

# Execute OpenClaw Block
if [[ "$DEPLOY_OPENCLAW" =~ ^[Yy]$ ]]; then
    echo "🎨 Compiling graphics-compliant OpenClaw environment..."
    docker build -t openclaw-agent:latest ./containers/openclaw/
    docker rm -f openclaw-agent-workspace || true
    mkdir -p ./workspace
    docker run -d \
      --name openclaw-agent-workspace \
      -v "$(pwd)/workspace:/app/workspace" \
      --restart always \
      openclaw-agent:latest
else
    echo "➡️  Skipping OpenClaw Workspace structural changes."
fi

# ---------------------------------------------------------------------------
# Save Current State Parameters Locally
# ---------------------------------------------------------------------------
cat << EOF > "$STATE_FILE"
REAL_SEARXNG_URL=$REAL_SEARXNG_URL
SAVED_EXTERNAL_URL=$SAVED_EXTERNAL_URL
EOF

echo "======================================================="
echo "✅ INFRASTRUCTURE MESH SYNCHRONIZED!"
echo "Gateway Target upstream address mapping: $REAL_SEARXNG_URL"
echo "======================================================="

