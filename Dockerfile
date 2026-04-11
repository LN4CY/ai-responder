# nikolaik/python-nodejs bundles Python 3.12 + Node 22 in a slim Debian image.
# Using this as the base avoids a slow apt-get install of nodejs/npm on every build.
# Ref: https://hub.docker.com/r/nikolaik/python-nodejs
FROM nikolaik/python-nodejs:python3.12-nodejs22

WORKDIR /app

# Install dependencies
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
