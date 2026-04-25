# Base Python image
FROM python:3.12-slim

WORKDIR /app

# All direct and transitive Python dependencies ship as pure-Python or
# pre-built binary wheels for amd64/arm64/armv7, so no compiler toolchain
# is needed. The image stays lean for Pi deployment.

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
