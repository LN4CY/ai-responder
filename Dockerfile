# Base Python image
FROM python:3.12-slim

WORKDIR /app

# Install System Dependencies (Node.js + Build Tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    build-essential \
    libffi-dev \
    python3-dev \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
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
