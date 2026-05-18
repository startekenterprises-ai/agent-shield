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
# Module 1: Agent-Shield Core Security Gateway (FastAPI Proxy)
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
    if [ "$INSTALLED_SEARXNG" = true ]; then
        read -p "● Local SearXNG Mesh is already running. Rebuild/Repair it? (y/N): " REPAIR_SEARXNG
    else
        read -p "● Do you want a fresh, isolated local SearXNG container spun up? (Y/n): " INSTALL_SEARXNG
        if [[ "$INSTALL_SEARXNG" =~ ^[Nn]$ ]]; then
            read -p "  Enter your pre-existing external SearXNG URL: " EXT_URL
            REAL_SEARXNG_URL=$EXT_URL
        else
            REAL_SEARXNG_URL="http://searxng-private-mesh:8080"
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
echo "⚙️  Generating targeted Docker Compose manifest..."

# Construct the base docker-compose text string dynamically based on selections
cat << EOF > ./docker-compose.secure.yml
version: '3.8'
services:
EOF

# Append Core Shield service block
if [[ "$INSTALL_SHIELD" != "n" || "$REPAIR_SHIELD" =~ ^[Yy]$ ]]; then
    cat << EOF >> ./docker-compose.secure.yml
  agent-shield-firewall:
    build: .
    container_name: agent-shield-gateway
    ports:
      - "8000:8000"
    environment:
      - SEARCH_PROVIDER=searxng
      - REAL_SEARXNG_URL=${REAL_SEARXNG_URL:-http://searxng-private-mesh:8080}
      - OLLAMA_HOST=http://docker.internal
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: always
EOF
    INSTALLED_SHIELD=true
fi

# Append SearXNG service block
if [[ "$INSTALL_SEARXNG" =~ ^[Yy]$ || "$INSTALL_SEARXNG" == "" || "$REPAIR_SEARXNG" =~ ^[Yy]$ ]]; then
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
    cat << 'EOF' >> ./docker-compose.secure.yml
  searxng-private-mesh:
    image: searxng/searxng:latest
    container_name: searxng-private-mesh
    expose:
      - "8080"
    volumes:
      - ./config/searxng:/etc/searxng:ro
    restart: always
EOF
    INSTALLED_SEARXNG=true
fi

# Append OpenClaw service block
if [[ "$INSTALL_OPENCLAW" =~ ^[Yy]$ || "$REPAIR_OPENCLAW" =~ ^[Yy]$ ]]; then
    cat << 'EOF' >> ./docker-compose.secure.yml
  openclaw-agent:
    build:
      context: ./containers/openclaw
    container_name: openclaw-agent-workspace
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./workspace:/app/workspace
    restart: always
EOF
    INSTALLED_OPENCLAW=true
fi

# ---------------------------------------------------------------------------
# Targeted Container Upgrades Execution
# ---------------------------------------------------------------------------
echo "🚀 Deploying and updates execution loops..."

if [ "$REPAIR_SHIELD" = "y" ]; then docker-compose -f docker-compose.secure.yml up -d --build agent-shield-firewall; fi
if [ "$REPAIR_SEARXNG" = "y" ]; then docker-compose -f docker-compose.secure.yml up -d searxng-private-mesh; fi
if [ "$REPAIR_OPENCLAW" = "y" ]; then docker-compose -f docker-compose.secure.yml up -d --build openclaw-agent; fi

# Default up catch-all for newly targeted additions
docker-compose -f docker-compose.secure.yml up -d --remove-orphans

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
echo "✅ ARCHITECTURE SYNCHRONIZED!"
echo "State mapped safely. Re-run this installer at any time to patch individual modules."
echo "======================================================="

