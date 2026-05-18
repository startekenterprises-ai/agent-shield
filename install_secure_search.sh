#!/bin/bash
set -e

echo "🛡️  Agent-Shield: Automated Secure Search Installer"
echo "======================================================="

# 1. Environment Verification Gates
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker engine could not be detected. Please install Docker first."
    exit 1
fi

# 2. Pre-deployment Cleanup: Prevent the "orphan containers" warning
# Clears the legacy manual docker run container if it is lingering on port 8000
if docker ps -a --format '{{.Names}}' | grep -q '^agent-shield-gateway$'; then
    echo "🧹 Cleaning up existing standalone agent-shield-gateway instance..."
    docker rm -f agent-shield-gateway > /dev/null || true
fi

# 3. Interactive Network Routing Configuration
echo "Configuring Search Backend Routing..."
read -p "Do you want to use an EXISTING external SearXNG instance? (y/N): " USE_EXTERNAL

if [[ "$USE_EXTERNAL" =~ ^[Yy]$ ]]; then
    read -p "Enter your external SearXNG URL (e.g., http://192.168.2.42:8080): " EXTERNAL_URL
    REAL_SEARXNG_URL=$EXTERNAL_URL
    LAUNCH_LOCAL_SEARXNG=false
    echo "🔗 Agent-Shield will route requests upstream to: $REAL_SEARXNG_URL"
else
    REAL_SEARXNG_URL="http://searxng-private-mesh:8080"
    LAUNCH_LOCAL_SEARXNG=true
    echo "📦 Agent-Shield will spin up a fresh local SearXNG container inside WSL."
fi

# 4. Create Local Configs if needed
if [ "$LAUNCH_LOCAL_SEARXNG" = true ]; then
    mkdir -p ./config/searxng
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
fi

# 5. Determine if we build from local source context or pull pre-built imagery
# If running inside your cloned workspace with a Dockerfile, build it locally.
if [ -f "./Dockerfile" ]; then
    echo "🛠️  Local source repository detected. Building image from source context..."
    IMAGE_STR="build: ."
else
    echo "📦 Standalone execution context. Targetting public GitHub distribution image..."
    IMAGE_STR="image: ghcr.io/startekenterprises-ai/agent-shield:latest"
fi

# 6. Generate the Blueprint
cat << EOF > ./docker-compose.secure.yml
version: '3.8'

services:
  agent-shield-firewall:
    $IMAGE_STR
    container_name: agent-shield-gateway
    ports:
      - "8000:8000"
    environment:
      - SEARCH_PROVIDER=searxng
      - REAL_SEARXNG_URL=$REAL_SEARXNG_URL
      - OLLAMA_HOST=http://docker.internal
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: always
EOF

# Append SearXNG service to the compose file only if local was selected
if [ "$LAUNCH_LOCAL_SEARXNG" = true ]; then
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
fi

# 7. Run the Stack and force-clear old orphans dynamically
echo "🚀 Booting safe-routing security containers..."
docker-compose -f docker-compose.secure.yml up -d --remove-orphans

echo "======================================================="
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "🛡️  Agent-Shield Gateway listening securely on: http://localhost:8000"
echo ""
echo "🔌 Configure OpenClaw or OpenClaw-Sandbox to use this proxy:"
echo "   openclaw configure --section web --url http://localhost:8000/"
echo "======================================================="

