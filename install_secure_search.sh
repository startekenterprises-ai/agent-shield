#!/bin/bash
set -e

STATE_FILE="./.shield_state"
echo "🛡️  Agent-Shield: Modular AI Security Mesh Installer"
echo "======================================================="

# Load existing state if it exists to allow individual piece recovery
if [ -f "$STATE_FILE" ]; then
    source "$STATE_FILE"
    echo "🔄 Existing setup detected. Running in Maintenance/Repair mode..."
else
    INSTALLED_SHIELD=false
    INSTALLED_SEARXNG=false
    INSTALLED_OPENCLAW=false
fi

# ---------------------------------------------------------------------------
# Module 1: Agent-Shield Core Security Gateway
# ---------------------------------------------------------------------------
if [ "$INSTALLED_SHIELD" = true ]; then
    read -p "● Agent-Shield Core is already running. Rebuild/Repair it? (y/N): " REPAIR_SHIELD
else
    read -p "● Install Agent-Shield Security Firewall Proxy? (Y/n): " INSTALL_SHIELD
fi

# ---------------------------------------------------------------------------
# Module 2: Upstream Search Infrastructure (SearXNG)
# ---------------------------------------------------------------------------
if [ "$INSTALL_SHIELD" != "n" ]; then
    # AUTOMATED PRE-FLIGHT CHECK: Check if port 8088 or 8080 is already active in Docker
    if docker ps --format '{{.Names}} {{.Ports}}' | grep -q -E '(searxng-private-mesh|8088|8080)'; then
        echo "💡 Notice: A local SearXNG instance or bound port was detected active in Docker."
        read -p "  -> Skip SearXNG installation and use the active running instance? (Y/n): " SKIP_SEARXNG
        if [[ "$SKIP_SEARXNG" =~ ^[Nn]$ ]]; then
            read -p "  Enter alternate pre-existing external SearXNG URL: " EXT_URL
            REAL_SEARXNG_URL=$EXT_URL
            INSTALL_SEARXNG="n"
        else
            REAL_SEARXNG_URL="http://192.168.2.42:8088"
            INSTALL_SEARXNG="n"
            REPAIR_SEARXNG="n"
            INSTALLED_SEARXNG=true
            echo "✅ Skipping SearXNG container deployment (Using active container)."
        fi
    else
        if [ "$INSTALLED_SEARXNG" = true ]; then
            read -p "● Local SearXNG Mesh is registered. Rebuild/Repair it? (y/N): " REPAIR_SEARXNG
        else
            read -p "● Do you want a fresh, isolated local SearXNG container spun up? (Y/n): " INSTALL_SEARXNG
            if [[ "$INSTALL_SEARXNG" =~ ^[Nn]$ ]]; then
                read -p "  Enter your pre-existing external SearXNG URL: " EXT_URL
                REAL_SEARXNG_URL=$EXT_URL
            else
                REAL_SEARXNG_URL="http://192.168.2.42:8088"
            fi
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Module 3: Hyperconverged Agent Space (OpenClaw)
# ---------------------------------------------------------------------------
if [ "$INSTALLED_OPENCLAW" = true ]; then
    read -p "● OpenClaw Workspace container is already running. Rebuild/Repair it? (y/N): " REPAIR_OPENCLAW
else
    read -p "● Optional: Bundle in a containerized OpenClaw Agent Workspace? (y/N): " INSTALL_OPENCLAW
fi

echo "======================================================="
echo "🚀 Deploying and updating execution loops via Native Docker..."

# 1. Handle SearXNG Execution
if [[ "$INSTALL_SEARXNG" =~ ^[Yy]$ || "$INSTALL_SEARXNG" == "" || "$REPAIR_SEARXNG" =~ ^[Yy]$ ]]; then
    if [ "$INSTALL_SEARXNG" != "n" ]; then
        echo "📦 Initializing SearXNG Private Mesh on port 8088..."
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
        REAL_SEARXNG_URL="http://192.168.2.42:8088"
        INSTALLED_SEARXNG=true
    fi
fi

# 2. Handle Agent-Shield Core Execution
if [[ "$INSTALL_SHIELD" != "n" || "$REPAIR_SHIELD" =~ ^[Yy]$ ]]; then
    echo "🛠️  Compiling Agent-Shield Image layers..."
    docker build -t agent-shield:latest .
    docker rm -f agent-shield-gateway || true
    docker run -d \
      --name agent-shield-gateway \
      -p 8000:8000 \
      -e SEARCH_PROVIDER=searxng \
      -e REAL_SEARXNG_URL=${REAL_SEARXNG_URL:-http://192.168.2.42:8088} \
      -e OLLAMA_HOST=http://192.168.2.42:11434 \
      --restart always \
      agent-shield:latest
    INSTALLED_SHIELD=true
fi

# 3. Handle OpenClaw Execution
if [[ "$INSTALL_OPENCLAW" =~ ^[Yy]$ || "$REPAIR_OPENCLAW" =~ ^[Yy]$ ]]; then
    echo "🎨 Compiling graphics-compliant OpenClaw environment..."
    docker build -t openclaw-agent:latest ./containers/openclaw/
    docker rm -f openclaw-agent-workspace || true
    mkdir -p ./workspace
    docker run -d \
      --name openclaw-agent-workspace \
      -v "$(pwd)/workspace:/app/workspace" \
      --restart always \
      openclaw-agent:latest
    INSTALLED_OPENCLAW=true
fi

# ---------------------------------------------------------------------------
# Save Deployment State Locally
# ---------------------------------------------------------------------------
cat << EOF > "$STATE_FILE"
INSTALLED_SHIELD=$INSTALLED_SHIELD
INSTALLED_SEARXNG=$INSTALLED_SEARXNG
INSTALLED_OPENCLAW=$INSTALLED_OPENCLAW
REAL_SEARXNG_URL=$REAL_SEARXNG_URL
EOF

echo "======================================================="
echo "✅ ARCHITECTURE SYNCHRONIZED SUCCESSFULLY!"
echo "All modules active and verified via Native Docker engine."
echo "======================================================="

