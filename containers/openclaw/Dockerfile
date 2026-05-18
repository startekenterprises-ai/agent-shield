FROM python:3.11-slim

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

# 1. Install necessary system packages and headless browser graphics
RUN apt-get update && apt-get install -y \
    git \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 2. Install the browser automation engine, playwright core, and OpenAI-compatible client libraries
RUN pip install --no-cache-dir \
    playwright \
    browser-use \
    langchain-openai \
    pydantic

# 3. Download and install safe Chromium browser drivers via the python module execution flag
RUN python -m playwright install --with-deps chromium

# 4. Inject our native Agent-Shield local security mapping asset
RUN mkdir -p /root/.openclaw
COPY openclaw.json /root/.openclaw/openclaw.json

# 5. Copy a clean script runner into the container core to act as our TUI loop
COPY agent_tui_runner.py /app/main.py

VOLUME ["/app/workspace"]

# Keep-alive entry point: keeps the container awake for your interactive sessions
CMD ["tail", "-f", "/dev/null"]

