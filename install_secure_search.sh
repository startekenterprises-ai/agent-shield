#!/bin/bash
set -e

echo "🛡️  Agent-Shield: Automated Secure Search Installer"
echo "======================================================="

# 1. Environment Verification Gates
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker engine could not be detected. Please install Docker first."
    exit 1
fi

# 2. Workspace Initialization
echo "📂 Constructing isolated security volume directory structure..."
mkdir -p ./config/searxng

# 3. Create a production-hardened SearXNG configuration on the fly
cat << 'EOF' > ./config/searxng/settings.yml
use_default_settings: true
server:
  port: 8080
  bind_address: "0.0.0.0"
  secret_key: "agent_shield_super_secure_entropy_salt_key"
search:
  safe_search: 0
  autocomplete: ""
EOF

# 4. Generate the Unified Multi-Container Production Docker Compose Blueprint
cat << 'EOF' > ./docker-compose.secure.yml
version: '3.8'

services:
  agent-shield-firewall:
    image: ghcr.io/startekenterprises-ai/agent-shield:latest
    container_name: agent-shield-gateway
    ports:
      - "8000:8000"
    environment:
      - SEARCH_PROVIDER=searxng
      - REAL_SEARXNG_URL=http://searxng-private:8080
      - OLLAMA_HOST=http://docker.internal
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      - searxng-private
    restart: always

  searxng-private:
    image: searxng/searxng:latest
    container_name: searxng-private-mesh
    # Keeps ports unbound to host system so OpenClaw is FORCED to go through Agent-Shield
    expose:
      - "8080"
    volumes:
      - ./config/searxng:/etc/searxng:ro
    restart: always
EOF

# 5. Execute Container Deployment Loops
echo "🚀 Booting safe-routing security containers..."
docker compose -f docker-compose.secure.yml up -d --pull always

echo "======================================================="
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "🛡️  Agent-Shield Gateway listening securely on: http://localhost:8000"
echo ""
echo "🔌 To connect your OpenClaw or OpenClaw-Sandbox agent, execute:"
echo "   openclaw configure --section web --url http://localhost:8000/"
echo "======================================================="

