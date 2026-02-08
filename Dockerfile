FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    meshtastic \
    requests \
    pypubsub

# Copy all modules and packages
COPY config.py .
COPY providers/ ./providers/
COPY conversation/ ./conversation/
COPY meshtastic_handler/ ./meshtastic_handler/
COPY ai_responder.py .


# Run the application
CMD ["python", "-u", "ai_responder.py"]
