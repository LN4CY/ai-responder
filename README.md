# AI Responder for Meshtastic

A powerful, plugin-based AI assistant for Meshtastic nodes. It connects to your mesh via TCP (like MeshMonitor) and processes `!ai` commands, responding using either a local LLM (Ollama) or a cloud provider (Gemini).

## Features

-   **Dual Provider Support**: Switch between Local (Ollama) and Cloud (Google Gemini) on the fly.
-   **Admin Controls**: Restrict sensitive commands (changing providers, managing channels) to specific node IDs.
-   **Channel Management**: Configure which channels the bot listens on.
-   **Smart Rate Limiting**: Splits long responses into chunks and waits (30s) between sends to prevent mesh congestion.
-   **Reliability**: Retries connections and verifies message acknowledgments.

## Installation

### Kubernetes / Docker Compose (Recommended)

Add to your `docker-compose.yml`:

```yaml
  ai-responder:
    build: ./ai-responder
    environment:
      - MESHTASTIC_HOST=meshmonitor
      - MESHTASTIC_PORT=4404
      - AI_PROVIDER=gemini # or 'ollama'
      - GEMINI_API_KEY=your_key_here
      - ADMIN_NODE_ID=!your_admin_id
    volumes:
      - ai-data:/app/data
    depends_on:
      - meshmonitor
```

### Local Development

1.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
2.  Run the script:
    ```bash
    python ai-responder.py
    ```

## Configuration

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `MESHTASTIC_HOST` | `meshmonitor` | Hostname of TCP interface |
| `MESHTASTIC_PORT` | `4404` | Port of TCP interface |
| `AI_PROVIDER` | `ollama` | Initial provider (`ollama` or `gemini`) |
| `OLLAMA_HOST` | `ollama` | Hostname for Ollama service |
| `GEMINI_API_KEY` | - | API Key for Google Gemini |
| `ADMIN_NODE_ID` | - | Node ID authorized for admin commands (e.g. `!1234abcd`) |
| `ALLOWED_CHANNELS` | `0,3` | CSV list of channel indices to listen on |

## Commands

### User Commands
-   `!ai <prompt>`: Ask the AI a question.

### Admin Commands
-   `!ai -h`: Show help menu.
-   `!ai -p [local|online]`: Switch AI provider.
-   `!ai -c [add|rm] <index>`: Enable/Disable listening on a channel.
-   `!ai -a [add|rm] <node_id>`: Add or remove an admin.
