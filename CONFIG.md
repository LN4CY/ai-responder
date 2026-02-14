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
| `CONNECTION_RETRY_INTERVAL` | `10` | Seconds to wait between reconnection attempts. |
| `CONNECTION_MAX_RETRIES` | `3` | Number of initial connection attempts before switching to watchdog loop. |
| `MESHTASTIC_AWARENESS` | `true` | Enable/Disable all mesh context injection (metadata & tools). |
| `HEALTH_CHECK_ACTIVITY_TIMEOUT` | `300` | Seconds of silence before sending a probe (Radio Watchdog). |
| `HEALTH_CHECK_PROBE_INTERVAL` | `150` | Seconds between active probes when silent. |

### AI Provider Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_FILE` | `/app/data/config.json` | Path to the persistent configuration file. |
| `AI_PROVIDER` | `ollama` | The default AI provider to use. Options: `ollama`, `gemini`, `openai`, `anthropic`. |
| `OLLAMA_HOST` | `ollama` | Hostname of the Ollama service (if using Local AI). |
| `OLLAMA_PORT` | `11434` | Port of the Ollama service. |
| `OLLAMA_MODEL` | `llama3.2:1b` | The specific model to use with Ollama. |
| `GEMINI_API_KEY` | - | API Key for Google Gemini (required if provider is `gemini`). |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | The specific Gemini model version to use. |
| `GEMINI_SEARCH_GROUNDING` | `false` | Enable Google Search grounding for real-time info. Set to `true` to enable (**Gemini Only**). |
| `GEMINI_MAPS_GROUNDING` | `false` | Enable Google Maps grounding for location-based info. Set to `true` to enable (**Gemini Only**). |
| `OPENAI_API_KEY` | - | API Key for OpenAI (required if provider is `openai`). |
| `OPENAI_MODEL` | `gpt-3.5-turbo` | The specific OpenAI model to use. |
| `ANTHROPIC_API_KEY` | - | API Key for Anthropic (required if provider is `anthropic`). |
| `ANTHROPIC_MODEL` | `claude-3-haiku-20240307` | The specific Anthropic model to use. |

### AI Persona / System Prompt

System prompts are loaded from external text files, allowing easy customization without code changes.

- **Local Provider (Ollama)**: Loads from `system_prompt_local.txt`
  - Default: "You are a helpful AI assistant. Keep responses concise (under 200 chars when possible)."
  
- **Online Providers**: Loads from `system_prompt_online.txt`
  - Default: "You are a helpful AI assistant communicating via Meshtastic mesh network..."
  - **Context Isolation**: The prompt supports a `{context_id}` placeholder. The system automatically injects the current conversation ID (e.g., `Channel:0:!1234abcd`) into this placeholder to ground the AI in the specific user context.

### Situational Awareness (AI Tool Use)

The responder uses **AI Function Calling** (Adaptive Tools) to proactively query the network. This eliminates noisy metadata injection and allows the AI to only fetch what it needs.

**Provider Implementation:**
- **Gemini**: Native function calling with multi-turn orchestration and **Dynamic Grounding Switch** (simulated mixed mode).
- **OpenAI / Anthropic**: Multi-turn tool loops using structured API requests.
- **Ollama**: Conditional tool support (Llama 3.1+, Nemo) with text fallback.

**Available AI Tools:**
- **`get_my_info`**: Retrieves the bot's own telemetry (Battery, SNR, Name, Status).
- **`get_mesh_nodes`**: Returns a list of all active neighbors currently seen on the mesh.
- **`get_node_details`**: Fetches detailed telemetry for a specific node by name or Hex ID.

**Adaptive Fallback Logic:**
If `MESHTASTIC_AWARENESS` is enabled but the model doesn't support tools, the system automatically injects:
- **Location**: GPS coordinates (latitude, longitude) for grounding.
- **Node Metadata**: A clean `[RADIO CONTEXT]` block describing the user's environment.
- **On-Demand Telemetry**: Proactive requests for fresh neighbor data during active sessions.

You can mount custom prompt files in Docker:
```yaml
volumes:
  - ./my_custom_prompt.txt:/app/system_prompt_online.txt
```

### Access Control & Channels

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_NODE_ID` | - | Comma-separated list of Node IDs authorized for admin commands (e.g., `!1234abcd`). |
| `ALLOWED_CHANNELS` | `0,3` | Comma-separated list of channel indices the bot listens on. |

### Memory Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `HISTORY_MAX_MESSAGES` | `1000` | Maximum number of messages to keep in history per user (Storage). |
| `HISTORY_MAX_BYTES` | `2097152` | Maximum size in bytes for the history file per user (default 2MB). |
| `OLLAMA_MAX_MESSAGES` | `10` | Maximum number of messages sent to Ollama (Local) for context window. |

> [!NOTE]
> **Behavior**:
> - **Message Limit**: Acts as a **rolling buffer**. When the limit (1000) is reached, the oldest message is dropped to make room for the new one.
> - **Storage Limit**: If the file size exceeds 2MB, the system automatically prunes the oldest 50% of messages to recover space.


### System Prompts (Advanced)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SYSTEM_PROMPT_LOCAL_FILE` | `system_prompt_local.txt` | Path to custom prompt for Ollama/Local |
| `SYSTEM_PROMPT_ONLINE_FILE` | `system_prompt_online.txt` | Path to custom prompt for Online providers |

To use a custom prompt:
1. Create a text file with your prompt (use `{context_id}` placeholder).
2. Mount it to the container at `/app/system_prompt_local.txt` (or change the ENV to point to your mounted path).

## Configuration Files

The application also persists runtime configuration changes (like allowed channels or provider switches) to a JSON file.

- **Path**: `/app/data/config.json`
- **Persistence**: This file is stored in the Docker volume `ai-responder-data` to survive container restarts.

**Example `config.json`:**
```json
{
  "current_provider": "ollama",
  "allowed_channels": [0, 3],
  "admin_nodes": ["!12345678"]
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
