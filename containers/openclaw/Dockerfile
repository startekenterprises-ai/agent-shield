FROM python:3.11-slim

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

# Install core packages + modern Debian 13 compliant headless OpenGL libraries for OpenCV
RUN apt-get update && apt-get install -y \
    git \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Programmatically build the target URL path string to bypass terminal clipping bugs
RUN GIT_DOMAIN="https://github.com" \
    && GIT_PATH="mnotgod96/AppAgent.git" \
    && git clone "${GIT_DOMAIN}/${GIT_PATH}" . \
    && pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /root/.openclaw
COPY openclaw.json /root/.openclaw/openclaw.json

VOLUME ["/app/workspace"]

# Keep-alive entry point
CMD ["tail", "-f", "/dev/null"]

