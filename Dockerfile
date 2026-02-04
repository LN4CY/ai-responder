FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    meshtastic \
    requests \
    pypubsub

# Copy script and system prompts
COPY ai-responder.py .
COPY system_prompt_local.txt .
COPY system_prompt_online.txt .

# Run script
CMD ["python", "-u", "ai-responder.py"]
