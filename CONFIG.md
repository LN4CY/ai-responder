# Configuration Guide

Complete configuration reference for the AI Responder.

## Environment Variables

The application is configured primarily via environment variables passed to the Docker container.

### Connection Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERFACE_TYPE` | `tcp` | Connection type: `tcp` or `serial`. |
| `SERIAL_PORT` | `/dev/ttyACM0` | Serial device path (e.g., `COM3` on Windows). |
| `MESHTASTIC_HOST` | `meshmonitor` | Hostname of the Meshtastic TCP interface. |
| `MESHTASTIC_PORT` | `4404` | Port of the Meshtastic TCP interface. |

### AI Provider Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_FILE` | `/app/data/config.json` | Path to the persistent configuration file. |
| `AI_PROVIDER` | `ollama` | The default AI provider to use. Options: `ollama`, `gemini`, `openai`, `anthropic`. |
| `OLLAMA_HOST` | `ollama` | Hostname of the Ollama service (if using Local AI). |
| `OLLAMA_PORT` | `11434` | Port of the Ollama service. |
| `OLLAMA_MODEL` | `llama3.2:1b` | The specific model to use with Ollama. |
| `GEMINI_API_KEY` | - | API Key for Google Gemini (required if provider is `gemini`). |
| `OPENAI_API_KEY` | - | API Key for OpenAI (required if provider is `openai`). |
| `ANTHROPIC_API_KEY` | - | API Key for Anthropic (required if provider is `anthropic`). |

### AI Persona / System Prompt

Currently, the system prompt is hardcoded in the `ai-responder.py` file:
> "Context: Meshtastic network assistant. Concise responses."

*To customize this, you currently need to modify the source code.*

### Access Control & Channels

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_NODE_ID` | - | Comma-separated list of Node IDs authorized for admin commands (e.g., `!1234abcd`). |
| `ALLOWED_CHANNELS` | `0,3` | Comma-separated list of channel indices the bot listens on. |

## Configuration File

The application also persists runtime configuration changes (like allowed channels or provider switches) to a JSON file.

- **Path**: `/app/data/config.json`
- **Persistence**: This file is stored in the Docker volume `ai-responder-data` to survive container restarts.

**Example `config.json`:**
```json
{
  "current_provider": "ollama",
  "allowed_channels": [0, 3],
  "admin_nodes": ["!9e044360"]
}
```

> [!NOTE]
> Values in `config.json` take precedence over environment variables if the file exists.

## Docker Compose Example

```yaml
  ai-responder:
    image: ghcr.io/ln4cy/ai-responder:latest
    container_name: meshmonitor-ai-responder
    restart: unless-stopped
    environment:
      - MESHTASTIC_HOST=meshmonitor
      - MESHTASTIC_PORT=4404
      - AI_PROVIDER=ollama
      - OLLAMA_HOST=ollama
      - OLLAMA_MODEL=llama3.2:1b
      - ALLOWED_CHANNELS=0,1,2
      - ADMIN_NODE_ID=!myadminid
    volumes:
      - ai-responder-data:/app/data
    depends_on:
      - meshmonitor
      - ollama
```
