# AI Responder for Meshtastic

A powerful, plugin-based AI assistant for Meshtastic nodes. It connects to your mesh via TCP (like MeshMonitor) and processes `!ai` commands, responding using either a local LLM (Ollama) or a cloud provider (Gemini).

## Features

-   **Multi-Provider Support**: Switch between **Ollama** (Local), **Gemini**, **OpenAI**, or **Anthropic** on the fly.
-   **Admin Controls**: Restrict sensitive commands (changing providers, managing channels) to specific node IDs.
-   **Channel Management**: Configure which channels the bot listens on.
-   **Smart Rate Limiting**: Splits long responses into chunks and waits (30s) between sends to prevent mesh congestion.
-   **Reliability**: Retries connections and verifies message acknowledgments.
-   **Architecture**: [See ARCHITECTURE.md](ARCHITECTURE.md) for design details.

## Quick Start

> [!NOTE]
> For detailed configuration options, see [CONFIG.md](CONFIG.md).

## Installation

### Kubernetes / Docker Compose (Recommended)

Add to your `docker-compose.yml`:

  ai-responder:
    image: ghcr.io/ln4cy/ai-responder:latest
    environment:
      - MESHTASTIC_HOST=meshmonitor
      - MESHTASTIC_PORT=4404
      - AI_PROVIDER=ollama # or 'gemini'
      - OLLAMA_HOST=ollama
      - OLLAMA_PORT=11434
      - GEMINI_API_KEY=your_key_here
      - ADMIN_NODE_ID=!your_admin_id
    volumes:
      - ai-data:/app/data
    depends_on:
      - meshmonitor
      - ollama
```

### Ollama Setup (Local AI)

If using the `ollama` provider, you must run the Ollama container and pull a model before the bot can respond.

1.  **Start the stack**: `docker-compose up -d`
2.  **Pull the model**:
    ```bash
    docker exec -it ollama ollama pull llama3.2:1b
    ```
    *Note: The default model is `llama3.2:1b`. If you change `OLLAMA_MODEL` in env, pull that one instead.*

### Connecting to MeshMonitor

The `ai-responder` acts as a "client" to [MeshMonitor](https://github.com/yeraze/meshmonitor).
- **MeshMonitor** must have `ENABLE_VIRTUAL_NODE=true` configured.
- The `ai-responder` connects to MeshMonitor's virtual node TCP port (default `4404`).
- This allows the AI bot to "see" chat messages on the mesh without needing its own dedicated LoRa radio hardware, leveraging the radio connected to MeshMonitor.

61
62
### Standalone Docker Container

You can run the responder as a standalone container without Docker Compose:

```bash
docker run -d \
  --name ai-responder \
  --restart unless-stopped \
  -e MESHTASTIC_HOST=192.168.1.100 \
  -e MESHTASTIC_PORT=4403 \
  -e AI_PROVIDER=gemini \
  -e GEMINI_API_KEY=your_api_key_here \
  ghcr.io/ln4cy/ai-responder:latest
```

### Integration with MeshMonitor

To use with [MeshMonitor](https://github.com/Yeraze/meshmonitor), ensure MeshMonitor has `ENABLE_VIRTUAL_NODE=true`.

1.  Add `ai-responder` to your `docker-compose.yml` (see installation above).
2.  Set `MESHTASTIC_HOST=meshmonitor` (container name).
3.  Set `MESHTASTIC_PORT=4404` (MeshMonitor virtual node port).

This allows the AI to "piggyback" on the radio connected to MeshMonitor.

## Multi-Platform Native Execution

You can run the responder directly on Windows, macOS, or Linux without Docker. This is useful for development or if you only need the Cloud (Gemini) provider and don't want to run a local LLM.

### Prerequisites
- Python 3.9+
- Network access to your Meshtastic node (TCP)

### 1. Setup Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\Activate.ps1

# Linux/macOS
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run (Gemini Provider)
If you don't have Ollama, you can use Google Gemini.

```bash
# Windows (PowerShell)
$env:MESHTASTIC_HOST="192.168.1.50"
$env:MESHTASTIC_PORT="4403"
$env:AI_PROVIDER="gemini"
$env:GEMINI_API_KEY="your_api_key"
python ai-responder.py

# Linux/macOS
export MESHTASTIC_HOST="192.168.1.50"
export MESHTASTIC_PORT="4403"
export AI_PROVIDER="gemini"
export GEMINI_API_KEY="your_api_key"
python ai-responder.py
```

### 4. Run (Serial / USB)

Connect directly to a radio via USB.

```bash
# Windows
$env:INTERFACE_TYPE="serial"
$env:SERIAL_PORT="COM3"
$env:AI_PROVIDER="gemini"
$env:GEMINI_API_KEY="your_api_key"
python ai-responder.py

# Linux (Raspberry Pi)
export INTERFACE_TYPE="serial"
export SERIAL_PORT="/dev/ttyACM0"
export AI_PROVIDER="gemini"
export GEMINI_API_KEY="your_api_key"
python ai-responder.py
```

## Configuration

See [CONFIG.md](CONFIG.md) for a complete reference of all environment variables and configuration files.

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `INTERFACE_TYPE` | `tcp` | `tcp` or `serial` |
| `SERIAL_PORT` | `/dev/ttyACM0` | Serial port (e.g. `COM3`) |
| `MESHTASTIC_HOST` | `meshmonitor` | Hostname of TCP interface |
| `MESHTASTIC_PORT` | `4404` | Port of TCP interface |
| `AI_PROVIDER` | `ollama` | Initial provider (`ollama`, `gemini`, `openai`, `anthropic`) |
| `OLLAMA_HOST` | `ollama` | Hostname for Ollama service |
| `GEMINI_API_KEY` | - | API Key for Google Gemini |
| `ADMIN_NODE_ID` | - | Node ID authorized for admin commands (e.g. `!1234abcd`) |
| `ALLOWED_CHANNELS` | `0,3` | CSV list of channel indices to listen on |
| `HISTORY_MAX_MESSAGES` | `1000` | Max messages to store per user history (Storage) |
| `HISTORY_MAX_BYTES` | `2097152` | Max size in bytes for history file (Storage) |
| `OLLAMA_MAX_MESSAGES` | `10` | Max messages sent to Ollama (Context) |

## Commands

### User Commands
-   `!ai <prompt>`: Ask the AI a question.
-   `!ai -m`: Show memory usage statistics for the user.
-   `!ai -n <question>`: Start a new conversation (flushes history).

### Admin Commands
-   `!ai -h`: Show help menu.
-   `!ai -p [local|online]`: Switch AI provider.
-   `!ai -c [add|rm] <index>`: Enable/Disable listening on a channel.
-   `!ai -a [add|rm] <node_id>`: Add or remove an admin.
-   `!ai -c [add|rm] <index>`: Enable/Disable listening on a channel.
-   `!ai -a [add|rm] <node_id>`: Add or remove an admin.
