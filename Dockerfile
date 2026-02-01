FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    meshtastic \
    requests \
    pypubsub

# Copy script
COPY ai-responder.py .

# Run script
CMD ["python", "-u", "ai-responder.py"]
