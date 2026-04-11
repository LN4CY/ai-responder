# Stage 1: grab Node.js from the official image (supports amd64, arm64, arm/v7)
FROM node:22-slim AS node

# Stage 2: Python runtime — copy node binaries in so we don't need apt-get
FROM python:3.12-slim

# Copy just the node/npm/npx executables and their runtime libs
COPY --from=node /usr/local/bin/node   /usr/local/bin/node
COPY --from=node /usr/local/bin/npm    /usr/local/bin/npm
COPY --from=node /usr/local/bin/npx    /usr/local/bin/npx
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    python3-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc libffi-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy all modules and packages
COPY config.py .
COPY providers/ ./providers/
COPY conversation/ ./conversation/
COPY meshtastic_handler/ ./meshtastic_handler/
COPY mcp_client.py .
COPY mcp_server_meshtastic.py .
COPY ai_responder.py .


# Run the application
CMD ["python", "-u", "ai_responder.py"]
