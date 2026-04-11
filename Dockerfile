# Base Python image
FROM python:3.12-slim

WORKDIR /app

# Install System Dependencies (Debian Native Node.js + Build Tools)
# Using native packages ensures full support for amd64, arm64, and armhf (arm/v7).
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    ca-certificates \
    build-essential \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
