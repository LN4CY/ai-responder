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
| `ACK_TIMEOUT` | `60` | Max seconds to wait for radio acknowledgment before retry. |
| `CHUNK_DELAY` | `15` | Seconds to wait before sending the next chunk of a split message. |
| `MESH_MAX_QUEUE_SIZE` | `500` | Maximum outgoing messages buffered in RAM to absorb bursts of AI dialogue. Size is kept small as messages are processed sequentially via chunks. Memory impact is negligible. |
| `HEALTH_CHECK_ACTIVITY_TIMEOUT` | `300` | Seconds of silence before sending a probe (Radio Watchdog). |
| `HEALTH_CHECK_PROBE_INTERVAL` | `150` | Seconds between active probes when silent. |

### AI Provider Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_FILE` | `/app/data/config.json` | Path to the persistent configuration file. |
| `MCP_SERVERS_FILE` | `/app/data/mcp_servers.json` | Path to the active Model Context Protocol definitions. |
| `AI_PROVIDER` | `ollama` | The default AI provider to use. Options: `ollama`, `gemini`, `openai`, `anthropic`. |
| `OLLAMA_HOST` | `ollama` | Hostname of the Ollama service (if using Local AI). |
| `OLLAMA_PORT` | `11434` | Port of the Ollama service. |
| `OLLAMA_MODEL` | `llama3.2:1b` | The specific fast model to use with Ollama for simple queries. |
| `OLLAMA_THINKING_MODEL` | *(empty)* | The specific thinking model to use for complex queries (defaults to OLLAMA_MODEL if empty). |
| `GEMINI_API_KEY` | - | API Key for Google Gemini (required if provider is `gemini`). |
| `GEMINI_MODEL` | `gemini-2.5-flash` | The specific Gemini fast model to use for simple queries. |
| `GEMINI_THINKING_MODEL` | `gemini-2.5-pro` | The specific Gemini thinking model to use for complex queries. |
| `GEMINI_SEARCH_GROUNDING` | `false` | Enable Google Search grounding for real-time info. Set to `true` to enable (**Gemini Only**). |
| `GEMINI_MAPS_GROUNDING` | `false` | Enable Google Maps grounding for location-based info. Set to `true` to enable (**Gemini Only**). |
| `OPENAI_API_KEY` | - | API Key for OpenAI (required if provider is `openai`). |
| `OPENAI_MODEL` | `gpt-4o-mini` | The specific OpenAI fast model to use for simple queries. |
| `OPENAI_REASONING_MODEL` | `o4-mini` | The specific OpenAI reasoning model to use for complex queries. |
| `ANTHROPIC_API_KEY` | - | API Key for Anthropic (required if provider is `anthropic`). |
| `ANTHROPIC_MODEL` | `claude-3-haiku-20240307` | The specific Anthropic fast model to use for simple queries. |
| `ANTHROPIC_THINKING_MODEL` | `claude-sonnet-4-6` | The specific Anthropic thinking model to use for complex queries. |

### AI Persona / System Prompt

System prompts are loaded from external text files, allowing easy customization without code changes.

- **Local Provider (Ollama)**: Loads from `system_prompt_local.txt`
  - Default: "You are a helpful AI assistant. Keep responses concise (under 200 chars when possible)."
  
- **Online Providers**: Loads from `system_prompt_online.txt`
  - Default: "You are a helpful AI assistant communicating via Meshtastic mesh network..."
  - **Context Isolation**: The prompt supports a `{context_id}` placeholder. The system automatically injects the current conversation ID (e.g., `Channel:0:!1234abcd`) into this placeholder to ground the AI in the specific user context.

*   **Universal Knowledge Graph (Semantic Sync)**: Automatically indexes all conversations, hardware status, and user-defined topics into MemPalace. 
*   **Safe Session Management**: Non-destructive context resets (!ai -n) with explicit "Nuclear Wipe" (!ai -n rm all) capability.
*   **Recursive Autonomous Scheduling**: AI can schedule future tasks...

**MCP Routing Implementation:**
- **Internal Meshtastic MCP Server**: Provides all radio capabilities directly via the unified MCP client.
- **External Plugins (e.g. MemPalace)**: If configured in `mcp_servers.json`, tools like `store_memory` or `search_memory` are passed dynamically to the AI.

**Internal MCP Capabilities:**
- **`get_my_info`**: Retrieves the bot's own telemetry (Battery, SNR, Name, Status).
- **`get_mesh_nodes`**: Returns a list of all active neighbors currently seen on the mesh, including their calculated distance from the bot and precise coordinates (incl. altitude) if known.
- **`get_node_details`**: Fetches detailed telemetry for a specific node by name or Hex ID.
- **`request_node_telemetry`**: Actively requests a fresh telemetry update from a specific node. Uses a **fast synchronous poll (0.5s)** for immediate results, with a **deferred callback** backup—if the mesh is slow, the AI proactively delivers the data when it eventually arrives.
- **`schedule_message`**: Schedules a future message or recurring task. 
  - **Stateful Memory**: The AI can maintain state (like counts) across recurring turns by referencing its **Conversation History**.
  - **Recursive Logic**: The AI can schedule new tasks from within a scheduled turn, allowing for complex autonomous behavior.
  - Params: `delay_seconds` (opt), `absolute_time` (opt), `context_note`, `recur_interval` (opt), `max_duration` (opt), `notify_targets` (opt).
  - `absolute_time`: HH:MM (e.g. "10:00") or YYYY-MM-DD HH:MM.
  - `notify_targets`: Comma-separated list: `requester` (default), `NodeName`, `!nodeid`, or `ch:N`.
- **`watch_condition`**: Registers a passive telemetry condition watcher.
  - Params: `node_id_or_name`, `metric`, `operator`, `threshold`, `context_note`, `notify_targets` (opt).
- **`watch_node_online`**: Registers a watcher that fires when a node is first heard on the mesh.
- **`send_message`**: Send a one-off message to a specific node or channel.
  - Params: `target` (node name, !hexid, or `ch:N`), `message`.
  - Params: `node_id_or_name`, `context_note`, `notify_targets` (opt).
- **`list_proactive_tasks`**: Lists all active tasks registered by the **calling user**.
- **`cancel_proactive_task`**: Cancels a task by ID (e.g., `sched-1`) or `all` to wipe the caller's tasks.

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
| `ADMIN_NODE_ID` | - | Comma-separated list of Node IDs authorized for admin commands (e.g., `!1234abcd,!9e044360`). Automatically loaded and deduplicated on startup—any corrupted entries from previous configs are cleaned and saved back to `config.json` automatically. |
| `ALLOWED_CHANNELS` | `0,3` | Comma-separated list of channel indices the bot listens on. |

## Session & Semantic Memory Management

The AI Responder uses a hybrid memory system combining **Local History (Disk)** for speed and **Semantic Knowledge (Graph/MemPalace)** for deep, long-term recall.

### Core Commands

| Command | Behavior | Context | Example |
| :--- | :--- | :--- | :--- |
| `!ai -n [Topic]` | **Pivot**: Starts a named session. Archives current context to the Knowledge Graph and resets the active buffer. | All | `!ai -n Solar Project` |
| `!ai -n` | **Reset**: Clears the bot’s current train of thought and reverts to the 'Default' context. (Safe: No data is deleted). | All | `!ai -n` |
| `!ai -n rm all` | **Nuclear Wipe**: Explicitly deletes all history for the current context from both disk and graph. | DM | `!ai -n rm all` |
| `!ai -end` | **Archive & Close**: Ends the active named session and returns to default mode. | DM | `!ai -end` |
| `!ai -c ls` | **Merged List**: Lists all 10 local disk slots PLUS archived topics found in the Knowledge Graph. | DM | `!ai -c ls` |
| `!ai -c [id/name]` | **Load/Re-hydrate**: Resumes a session. If the slot is gone from disk, the AI "re-hydrates" it from the Graph. | DM | `!ai -c 1` or `!ai -c Solar` |

### Multi-Dimensional Hubs (Knowledge Graph)
When MemPalace is enabled, data is indexed into three distinct "Hubs":
*   **Identity Hub (`MeshNode`)**: Permanent facts about your node and hardware trends.
*   **Topic Hub (`Topic`)**: Records tied to specific sessions (e.g., "Solar Project").
*   **Default Hub (`Default`)**: A "kitchen drawer" for out-of-session talk and loose information.

---

## Remote Administration (Admin Only)

The following commands are only accessible to `ADMIN_NODE_ID` users via **Direct Message**.

- **`!ai -p [provider]`**: Switches the active AI provider (e.g., `gemini`, `ollama`, `openai`).
- **`!ai -ch [add/rm] [idx/name]`**: Configures enabled channels for AI responses.
- **`!ai -a [ls/add/rm] [node_id]`**: Manages the list of admin authorized node IDs.
- **`!ai -s [ls/rm/add]`**: Manages proactive tasks system-wide.
  - `-s ls`: List all active tasks across all users.
  - `-s rm [id]`: Remove a specific task.
  - `-s rm all`: Wipe all active tasks.
  - `-s add`: Usage hint for adding tasks via AI.

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
  "admin_nodes": ["!12345678", "!9e044360"]
}
```

### External MCP Servers (Standardized Tools)

The responder can connect to remote services using the Model Context Protocol (MCP).

#### Networked Memory (MemPalace)
The preferred way to connect to MemPalace is via the environment variable:
- `MEMPALACE_URL`: Set this to the SSE endpoint of your `mempalace-viz` container.
  - Example: `http://mempalace-viz:8000/sse`

#### Advanced / Legacy Config
You can also define multiple remote or local servers in `/app/data/mcp_servers.json`:

```json
{
  "remote_tool": {
    "url": "http://some-other-service:8080/sse"
  },
  "local_plugin": {
    "command": "python",
    "args": ["-m", "some_module"]
  }
}
```
*Note: If `MEMPALACE_URL` is set, it will override any 'mempalace' entry in the JSON file.*

> [!NOTE]
> - Values in `config.json` take precedence over environment variables if the file already exists.
> - `admin_nodes` must be an array of individual hex IDs. The application auto-repairs any comma-concatenated strings (e.g., from `ADMIN_NODE_ID=!a,!b`) into a clean array on startup.

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
      # Optional: Map your custom MCP tools configuration (e.g. MemPalace)
      # - ./mcp_servers.json:/app/data/mcp_servers.json
    depends_on:
      - meshmonitor
      - ollama
```
