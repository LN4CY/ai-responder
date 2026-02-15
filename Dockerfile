FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all modules and packages
COPY config.py .
COPY ai_responder.py .
COPY providers/ ./providers/
COPY conversation/ ./conversation/
COPY meshtastic_handler/ ./meshtastic_handler/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/

# Create data directory for persistence
RUN mkdir -p /app/data

# Exposure for Web Dashboard
EXPOSE 8000

# Run the Manager Dashboard Orchestrator
CMD ["python", "-u", "dashboard/backend/manager.py"]
