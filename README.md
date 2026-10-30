# AI Responder for Meshtastic
> **Autonomous Mesh Intelligence for the Off-Grid World.**

The AI Responder is a powerful, plugin-based agentic AI designed specifically for Meshtastic mesh networks. It transforms a standard radio node into an intelligent participant capable of real-time network analysis, situational awareness, and automated responder capabilities. Whether running a light-weight local LLM on a Raspberry Pi or leveraging massive frontier models like Gemini, the AI Responder bridges the gap between off-grid LoRa communication and modern artificial intelligence.

## 🚀 Key Features & Capabilities

### 🧠 Adaptive Intelligence Controller
The system automatically scales its intelligence based on the selected provider.
- **Function Calling (Tools)**: Frontier models (Gemini, Claude, GPT) proactively query the mesh for telemetry, identify neighbors, and manage their own status using real-time API tools.
- **Fallback Metadata Injection**: For tool-blind or local models (Ollama), the controller automatically injects structured `[RADIO CONTEXT]` blocks, ensuring the AI remains mesh-aware even without native function calling support.

### 📡 Multi-Provider Agnostic
One agent, many brains. Choose the provider that fits your deployment:
- **Local (Ollama)**: Full privacy and off-grid autonomy using models like Llama 3.2.
- **Cloud (Gemini, OpenAI, Anthropic)**: High-reasoning capabilities with advanced tool orchestration and grounding.

### 🔌 Modular Model Context Protocol (MCP)
The architecture routes capabilities exclusively through standard MCP:
- **Plug-and-Play Tools**: AI capabilities are dynamically fetched from your configured MCP servers. Connecting a new service (like home automation or search) automatically makes it available to the bot on the mesh.
- **Built-in SQLite Semantic Memory (FTS5 + Knowledge Graph)**: Automatically indexes conversations, hardware status, and user-defined topics into an embedded `/app/data/memory.db` database with BM25 full-text search—requiring zero extra containers.
    - **Safe Session Management**: Non-destructive context resets (!ai -n) with explicit "Nuclear Wipe" (!ai -n rm all) capability for privacy and resource control.
    - **Deep Memory Re-hydration**: Ability to load and reconstruct sessions directly from the Knowledge Graph even after they've aged off the local disk.
    - **Recursive Autonomous Scheduling**: AI can schedule future tasks, recurring reminders, or complex multi-step workflows.
- **Internal Integration**: The core Meshtastic capabilities (sending messages, requesting telemetry, mapping nodes) and long-term memory tools are exposed dynamically as standard MCP tool endpoints to the AI processors.

### 🔗 Industrial-Grade Resiliency
Designed for 24/7 autonomous operation in remote environments:
- **Radio Watchdog**: Automatically detects and recovers from "zombie" connections where the radio hardware is active but the logic link has failed.
- **Proactive Reconnection**: Universal 10-second retry loop ensuring the bot recovers from service restarts or power cycles without manual intervention.
- **Adaptive Rate Limiting**: Intelligent message chunking (30s intervals) and ACK verification to prevent mesh congestion while ensuring delivery.

### 📍 Strategic Situational Awareness
The AI doesn't just respond; it understands its environment:
- **Real-time Telemetry**: Access to battery, SNR, RSSI, temperature, and humidity for the local node and neighbors.
- **Precise Location Tracking**: The AI can map out neighbors, complete with exact coordinates, altitude, and calculated distance from the bot.
- **Grounding (Gemini)**: Optional Google Search and Maps integration to provide real-world context for location-based queries.

### 🌐 Multi-Protocol Mesh Support (Meshtastic & MeshCore)
Operate seamlessly across Meshtastic and MeshCore networks with dedicated protocol-isolated responders:
- **Dedicated Protocol Responders**: Isolated `MeshtasticAIResponder` and `MeshCoreAIResponder` instances with shared base mixins.
- **Repeaters & Room Servers (MeshCore)**: First-class awareness and discovery of Repeaters (`0x02`), Room/BBS servers (`0x03`), and Chat clients (`0x01`).
- **Adaptive RF ACK Delivery Tracking**: Dynamic physical ACK confirmation (`EventType.ACK`) with settling buffers (`MESHCORE_POST_ACK_DELAY`) and automatic backoff on `ERR_CODE_BAD_STATE`.
- **Channel Broadcast Parsing**: Clean parsing of `<SenderName>: <Message>` companion packets on MeshCore group channels (e.g. Channel 4).
- **Standalone or MeshMonitor Virtual Nodes**: Connect directly via Serial or attach to MeshMonitor's Virtual Node TCP ports (4404 for Meshtastic, 5000 for MeshCore).

### 🤖 Proactive Agent Architecture
The AI can now spontaneously send messages to users or manage its own future behavior without being asked:
- **Chainable & Stateful Tasks**: Ask the AI to "ping me every 15 seconds with the SNR and an incrementing count." The AI uses its **Conversation History** to maintain state (like counts) across turns and can recursively schedule its own future updates.
- **Scheduled Reminders**: Ask the AI to remind you about something in 5 minutes or schedule complex recurring pings.
- **Condition Watchers**: Register alerting rules like "message me when node L4B1's battery drops below 10%" and the AI monitors passively from live mesh telemetry.
- **Deferred Telemetry Callbacks**: When the AI requests telemetry from a slow node and times out, it registers a background listener. When the data finally arrives from the mesh (seconds to minutes later), it proactively delivers it without requiring the user to ask again.
- **Race-Condition Optimized**: The AI uses intelligent synchronous polling (0.5s intervals) to ensure that if a mesh node responds quickly, you get a single clean reply instead of redundant proactive alerts.

### 👤 Persona-Driven Mesh Agent
- **Context Isolation**: Every user and channel has a secure sandbox, preventing data leakage between conversations.
- **Mesh Efficiency**: System prompts are tuned for LoRa—delivering high-density, concise information (typically <200 chars).
- **Session Management**: Direct Message (DM) support for continuous, stateful conversations with proactive timeout alerts.

## 🛠️ Why AI Responder? (Strengths)

- **Privacy First**: With Ollama support, your mesh data never has to leave your local network.
- **Low Barrier to Entry**: Runs on everything from a Windows desktop to a Raspberry Pi Zero 2W.
- **Zero-Config Mesh Discovery**: The AI automatically discovers neighboring nodes and can explain the network topology to users.
- **Extensible**: Architecture allows for easy addition of new Meshtastic tools and AI providers.

---

## 🗺️ Roadmap: The Future of Mesh AI

| Feature | Status | Description |
| :--- | :--- | :--- |
| **Modular MCP Architecture** | ✅ Done | Native Model Context Protocol (MCP) integration for dynamic tool discovery. |
| **Radio Resilience** | ✅ Done | Implicit ACK detection and Pending ACK Buffer. |
| **Proactive Agents** | ✅ Done | Scheduled msgs, condition watchers, and deferred telemetry callbacks. |
| **Web UI Dashboard** | 🚧 In Progress | Portable browser interface for setup and management. |
| **Remote Management** | 📅 Q3 2026 | Encrypted remote configuration over mesh or secondary link. |
| **Health Analytics** | 📅 Q4 2026 | Visual metrics of mesh health and AI interaction statistics. |

---

## Architecture: [See ARCHITECTURE.md](ARCHITECTURE.md)

## Quick Start

> [!NOTE]
> For detailed configuration options, see [CONFIG.md](CONFIG.md).

## Installation

### Docker Compose (Recommended)

Choose the deployment configuration that matches your setup and add it to your `docker-compose.yml`:

#### Option 1: MeshCore via MeshMonitor Virtual Node
Connects to a MeshCore radio (e.g. Heltec, T-Deck, T-Echo) managed by MeshMonitor on port `5000`:
```yaml
services:
  ai-responder:
    image: ghcr.io/ln4cy/ai-responder:latest
    container_name: meshmonitor-ai-responder-meshcore
    restart: unless-stopped
    environment:
      - MESH_TYPE=meshcore
      - MESHCORE_INTERFACE=tcp
      - MESHCORE_HOST=meshmonitor
      - MESHCORE_PORT=5000
      - MESHCORE_ADMIN_NODE_ID=LN4CY_TDeck        # Admin contact name or hex prefix (e.g. !12a1b2dd1de3)
      - MESHCORE_ALLOWED_CHANNELS=0,4             # Channels allowed for public !ai responses
      - AI_PROVIDER=gemini
      - GEMINI_API_KEY=your_gemini_api_key
    volumes:
      - ai-meshcore-data:/app/data
    networks:
      - mesh_net

volumes:
  ai-meshcore-data:
```

#### Option 2: Meshtastic via MeshMonitor Virtual Node
Connects to MeshMonitor's Meshtastic Virtual Node on TCP port `4404`:
```yaml
services:
  ai-responder:
    image: ghcr.io/ln4cy/ai-responder:latest
    container_name: meshmonitor-ai-responder-meshtastic
    restart: unless-stopped
    environment:
      - MESH_TYPE=meshtastic
      - MESHTASTIC_HOST=meshmonitor
      - MESHTASTIC_PORT=4404
      - ADMIN_NODE_ID=!1234abcd
      - ALLOWED_CHANNELS=0,3
      - AI_PROVIDER=gemini
      - GEMINI_API_KEY=your_gemini_api_key
    volumes:
      - ai-meshtastic-data:/app/data
    networks:
      - mesh_net

volumes:
  ai-meshtastic-data:
```

#### Option 3: Dual-Mesh (Meshtastic + MeshCore Concurrently)
Listens on both networks simultaneously from a single container, routing replies back to the originating protocol:
```yaml
services:
  ai-responder:
    image: ghcr.io/ln4cy/ai-responder:latest
    container_name: meshmonitor-ai-responder-dual
    restart: unless-stopped
    environment:
      - MESH_TYPE=dual
      # Meshtastic Settings
      - MESHTASTIC_HOST=meshmonitor
      - MESHTASTIC_PORT=4404
      - MESHTASTIC_ADMIN_NODE_ID=!1234abcd
      - MESHTASTIC_ALLOWED_CHANNELS=0,3
      # MeshCore Settings
      - MESHCORE_HOST=meshmonitor
      - MESHCORE_PORT=5000
      - MESHCORE_ADMIN_NODE_ID=LN4CY_TDeck
      - MESHCORE_ALLOWED_CHANNELS=0,4
      # AI Provider
      - AI_PROVIDER=gemini
      - GEMINI_API_KEY=your_gemini_api_key
    volumes:
      - ai-dual-data:/app/data
    networks:
      - mesh_net

volumes:
  ai-dual-data:
```

#### Option 4: Standalone Radio via USB Serial
Direct connection to physical LoRa hardware plugged into the host without MeshMonitor:
```yaml
services:
  # Standalone Meshtastic Hardware (e.g. /dev/ttyACM0)
  ai-responder-meshtastic:
    image: ghcr.io/ln4cy/ai-responder:latest
    restart: unless-stopped
    devices:
      - /dev/ttyACM0:/dev/ttyACM0
    environment:
      - MESH_TYPE=meshtastic
      - INTERFACE_TYPE=serial
      - SERIAL_PORT=/dev/ttyACM0
      - ADMIN_NODE_ID=!1234abcd
      - AI_PROVIDER=gemini
      - GEMINI_API_KEY=your_gemini_api_key
    volumes:
      - ai-serial-data:/app/data

  # Standalone MeshCore Hardware (e.g. /dev/ttyUSB1)
  ai-responder-meshcore:
    image: ghcr.io/ln4cy/ai-responder:latest
    restart: unless-stopped
    devices:
      - /dev/ttyUSB1:/dev/ttyUSB1
    environment:
      - MESH_TYPE=meshcore
      - MESHCORE_INTERFACE=serial
      - MESHCORE_SERIAL_PORT=/dev/ttyUSB1
      - MESHCORE_BAUDRATE=115200
      - MESHCORE_ADMIN_NODE_ID=LN4CY_TDeck
      - AI_PROVIDER=gemini
      - GEMINI_API_KEY=your_gemini_api_key
    volumes:
      - ai-meshcore-serial-data:/app/data
```

### Standalone Docker Run Commands

Run immediately from the command line without Docker Compose:

```bash
# MeshCore via MeshMonitor Virtual Node (TCP)
docker run -d \
  --name ai-responder-meshcore \
  --restart unless-stopped \
  -v ai-meshcore-data:/app/data \
  -e MESH_TYPE=meshcore \
  -e MESHCORE_HOST=192.168.1.100 \
  -e MESHCORE_PORT=5000 \
  -e MESHCORE_ADMIN_NODE_ID=LN4CY_TDeck \
  -e AI_PROVIDER=gemini \
  -e GEMINI_API_KEY=your_gemini_api_key \
  ghcr.io/ln4cy/ai-responder:latest

# Meshtastic via TCP (Radio IP or MeshMonitor Virtual Node)
docker run -d \
  --name ai-responder-meshtastic \
  --restart unless-stopped \
  -v ai-meshtastic-data:/app/data \
  -e MESH_TYPE=meshtastic \
  -e MESHTASTIC_HOST=192.168.1.100 \
  -e MESHTASTIC_PORT=4404 \
  -e ADMIN_NODE_ID=!1234abcd \
  -e AI_PROVIDER=gemini \
  -e GEMINI_API_KEY=your_gemini_api_key \
  ghcr.io/ln4cy/ai-responder:latest

# Standalone MeshCore via USB Serial
docker run -d \
  --name ai-responder-meshcore \
  --restart unless-stopped \
  --device=/dev/ttyUSB1:/dev/ttyUSB1 \
  -v ai-meshcore-data:/app/data \
  -e MESH_TYPE=meshcore \
  -e MESHCORE_INTERFACE=serial \
  -e MESHCORE_SERIAL_PORT=/dev/ttyUSB1 \
  -e MESHCORE_ADMIN_NODE_ID=LN4CY_TDeck \
  -e AI_PROVIDER=gemini \
  -e GEMINI_API_KEY=your_gemini_api_key \
  ghcr.io/ln4cy/ai-responder:latest
```

### Connecting to MeshMonitor Virtual Nodes

The `ai-responder` pairs directly with [MeshMonitor](https://github.com/yeraze/meshmonitor):
- **MeshCore**: Connects to MeshMonitor's MeshCore Virtual Node TCP port (default `5000`, companion protocol).
- **Meshtastic**: Connects to MeshMonitor's Meshtastic Virtual Node TCP port (default `4404`).
- **Multi-Client Multiplexing**: MeshMonitor holds the single physical USB radio link and safely multiplexes traffic between MeshMonitor's web UI, mobile apps, and `ai-responder`.

**Enabling Virtual Nodes in MeshMonitor:**

| Network | Configuration in MeshMonitor | Default Port |
|---------|------------------------------|--------------|
| **MeshCore** | Dashboard → **Sources** → **Edit MeshCore Source** → **Virtual Node** → toggle **ON** | `5000` |
| **Meshtastic** | Dashboard → **Sources** → **Edit Meshtastic Source** → **Virtual Node** → toggle **ON** | `4404` |

### Local AI Setup with Ollama

If using the local `ollama` provider instead of cloud APIs:

1. **Start Ollama** (e.g. `docker run -d --name ollama -p 11434:11434 -v ollama:/root/.ollama ollama/ollama`)
2. **Pull the model**:
   ```bash
   docker exec -it ollama ollama pull llama3.2:1b
   ```
3. Set `AI_PROVIDER=ollama` and `OLLAMA_HOST=ollama` in your environment.

### Native Python Execution (Bare-Metal / Development)

The AI Responder runs directly on Windows, macOS, or Linux without Docker:

1. **Clone the repository**:
   ```bash
   git clone https://github.com/LN4CY/ai-responder.git
   cd ai-responder
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate

   # Windows (PowerShell)
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   Set environment variables in your shell (or create a `.env` file):
   ```bash
   # Example: MeshCore via TCP Virtual Node
   export MESH_TYPE=meshcore
   export MESHCORE_INTERFACE=tcp
   export MESHCORE_HOST=127.0.0.1
   export MESHCORE_PORT=5000
   export MESHCORE_ADMIN_NODE_ID=LN4CY_TDeck
   export AI_PROVIDER=gemini
   export GEMINI_API_KEY=your_api_key_here

   # Set data directory for local storage (defaults to /app/data in Docker)
   export CONFIG_FILE=./data/config.json
   export MEMORY_DB_PATH=./data/memory.db
   export HISTORY_DIR=./data/history
   export CONVERSATIONS_DIR=./data/conversations
   ```

5. **Start the responder**:
   ```bash
   python ai_responder.py
   ```

## Configuration

See [CONFIG.md](CONFIG.md) for a complete reference of all environment variables and configuration files.

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `MESH_NETWORK_TYPE` | `meshtastic` | Network type: `meshtastic`, `meshcore`, or `dual` (both active). (Alias: `MESH_TYPE`). |
| `INTERFACE_TYPE` | `tcp` | Meshtastic connection type: `tcp` or `serial`. |
| `SERIAL_PORT` | `/dev/ttyACM0` | Meshtastic serial port (e.g. `COM3` on Windows). |
| `MESHTASTIC_HOST` | `meshmonitor` | Hostname of Meshtastic TCP interface. |
| `MESHTASTIC_PORT` | `4404` | Port of Meshtastic TCP interface. |
| `MESHCORE_INTERFACE` | `tcp` | MeshCore connection type: `tcp` or `serial`. |
| `MESHCORE_HOST` | `127.0.0.1` | Hostname/IP for MeshCore TCP connection. |
| `MESHCORE_PORT` | `5000` | Port for MeshCore TCP connection (default MeshMonitor Virtual Node port). |
| `MESHCORE_SERIAL_PORT` | `/dev/ttyUSB1` | Serial device path for standalone MeshCore hardware. |
| `MESHCORE_BAUDRATE` | `115200` | Baudrate for standalone MeshCore serial interface. |
| `MESHCORE_WAIT_FOR_ACK` | `true` | Wait for physical RF ACK on MeshCore DMs before sending next chunk. |
| `MESHCORE_POST_ACK_DELAY` | `2.0` | Seconds to settle after physical RF ACK before transmitting next chunk. |
| `MESHCORE_SEND_THINKING` | `false` | Send initial "Thinking... 🤖" LoRa packet on MeshCore (default: false to conserve airtime). |
| `MESHCORE_ADMIN_NODE_ID` | - | MeshCore admin contact name (e.g. `LN4CY_TDeck`) or hex key prefix (`!12a1b2dd1de3`). |
| `MESHCORE_ALLOWED_CHANNELS` | `0` | CSV list of channel indices allowed for MeshCore (e.g. `0,4`). |
| `CONNECTION_RETRY_INTERVAL` | `10` | Seconds between reconnect attempts |
| `CHUNK_DELAY` | `15` | Seconds to wait before sending the next chunk of a split message |
| `AI_PROVIDER` | `ollama` | Initial provider (`ollama`, `gemini`, `openai`, `anthropic`) |
| `OLLAMA_HOST` | `ollama` | Hostname for Ollama service |
| `GEMINI_API_KEY` | - | API Key for Google Gemini |
| `ADMIN_NODE_ID` | - | Node ID authorized for admin commands (global fallback). |
| `ALLOWED_CHANNELS` | `0,3` | CSV list of channel indices to listen on (global fallback). |
| `HISTORY_MAX_MESSAGES` | `1000` | Max messages to store per user history (Storage) |
| `HISTORY_MAX_BYTES` | `2097152` | Max size in bytes for history file (Storage) |
| `OLLAMA_MAX_MESSAGES` | `10` | Max messages sent to Ollama (Context) |


## Customizing System Prompts

The AI Responder comes with built-in system prompts that handle context isolation and metadata injection. If you wish to customize these prompts, you can mount your own text files into the container.

**Create your custom prompt file (e.g., `my_prompt.txt`):**
```text
You are a helpful assistant.
Context: {context_id}
```

**Mount it in Docker Compose:**
```yaml
    volumes:
      - ai-data:/app/data
      - ./my_prompt.txt:/app/system_prompt_local.txt # For Ollama
      # - ./my_online_prompt.txt:/app/system_prompt_online.txt # For Gemini/OpenAI/Anthropic
```

## User Guide

### Basic Usage

## 🤖 AI Commands

The responder supports several direct commands using the `!ai` prefix. Note that **Direct Messages (DMs)** offer full session support, while **Channels** are for one-off queries.

### 👤 Regular Commands (Available to All)

| Command | Description | Example | Mode Nuance |
| :--- | :--- | :--- | :--- |
| `!ai [msg]` | **Ask AI**: Query the mesh or general knowledge. | `!ai what nodes are near?` | **Channel**: Prefix required. **DM**: No prefix needed in session. |
| `!ai -h` | **Help**: Shows concise consolidated help. | `!ai -h` | Context-aware (hides Admin tools in public). |
| `!ai -m` | **Memory**: Shows AI context, storage, and slots usage. | `!ai -m` | System-wide status for the calling user. |
| `!ai -n [topic]` | **New Topic**: Resets context and starts fresh. | `!ai -n Mesh Routing` | **Channel**: Clears history. **DM**: Starts a named session. |
| `!ai -end` | **End Session**: Closes current active session. | `!ai -end` | **DM Only**. Sessions timeout after 5 mins. |
| `!ai -c ls` | **List History**: Show your saved conversations. | `!ai -c ls` | **DM Only**. Lists up to 10 saved slots. |
| `!ai -c [id]` | **Load History**: Resume a specific conversation. | `!ai -c 1` or `!ai -c MyTask` | **DM**: Resumes session. **Channel**: N/A. |
| `!ai -c rm [id]`| **Delete History**: Remove a saved conversation. | `!ai -c rm 1` or `!ai -c rm all`| Deletes from long-term disk storage. |

### ⚙️ Admin Commands (DM Only)

Admin commands require your node ID to be in the `admin_nodes` list (see [CONFIG.md](CONFIG.md)).

| Command | Description | Example |
| :--- | :--- | :--- |
| `!ai -s [rm <id>]` | **Scheduler**: Manage proactive tasks system-wide. | `!ai -s` (list), `!ai -s add` (help) |
| `!ai -p [name]` | **Provider**: Switch AI model (Ollama, Gemini, etc). | `!ai -p gemini` or `!ai -p local` |
| `!ai -ch [ls/add/rm]`| **Channels**: Enable/disable AI on specific channels. | `!ai -ch add 2` or `!ai -ch ls` |
| `!ai -a [ls/add/rm]` | **Admins**: Manage the list of authorized admins. | `!ai -a add !1234abcd` |

> [!TIP]
> **Consolidated Help**: Use `!ai -h` in a DM to see a concise summary of all commands available to you.

## 📡 Proactive Agents

Frontier AI providers (Gemini, Claude, GPT) can spontaneously send messages to users based on time or mesh events. No special command needed — just describe what you want to the AI in plain language.

> [!IMPORTANT]
> **DM-Only Registration**: To prevent mesh spam, proactive alerts (timers/watchers) can **only** be registered via Direct Message. The AI will politely decline if asked in a public channel.

### 🆔 Unique Task IDs
Every proactive task/watcher is assigned a unique ID (e.g., `[sched-1]`, `[cond-2]`, `[node-3]`). These IDs are shown when the task is registered and are used for management (listing/cancelling).

### 🔔 Multi-Target Notifications
By default, the AI notifies the **requester**. However, you can ask it to notify other nodes or even public channels (if the AI is authorized on that channel).
- **Example**: `!ai Notify me and !9e044360 when L4B1 comes online`
- **Example**: `!ai Ping me and channel 0 every 10 minutes`

### ⏰ Scheduled Reminders & Pings
Ask the AI to remind you about something after a delay or at a specific time:
- **One-shot (relative)**: `!ai Remind me to check the batteries in 5 minutes`
- **One-shot (absolute)**: `!ai Remind me at 10:00 PM to check the mesh`
- **Recurring**: `!ai Ping me every 30 seconds for the next 5 minutes`
- **Multi-target**: `!ai Remind me and NodeName at 09:00 to swap radios` (or use `!hexid`)
- **One-off Message**: `!ai Tell Node X I'm on my way` or `!ai Inform the group on channel 0 that the weather is clear`

### 👁️ Condition-Based Alerts (Telemetry Watchers)
Monitor a node's telemetry and alert when a threshold is hit:
- **Example**: `!ai Message me when node L4B1's battery is below 10%`
- **Example**: `!ai Alert me if the SNR on !9e044360 drops below -12`
- **Supported metrics**: `battery_level`, `voltage`, `temperature`, `humidity`, `barometric_pressure`, `iaq`, `snr`
- **Supported operators**: `<`, `>`, `<=`, `>=`, `==`

### 🔄 Dynamic & Recursive Examples
Leverage the AI's ability to maintain state and schedule future actions:
- **Stateful Counting**: `!ai For the next minute, ping me every 15 seconds with my SNR and an incrementing count starting at 1`
- **Recursive Monitoring**: `!ai Every 10 minutes, check the temperature of L4B1. If it's above 35C, alert channel 0, otherwise just remind yourself to check again in 10 minutes.`
- **Data Refresh Loops**: `!ai Watch node XYZ. Every time its battery changes, send me the new value and the current SNR.`

### 🟢 Node-Online Watchers
Get a notification as soon as a specific node is heard on the mesh:
- **Example**: `!ai Notify me when L4B1 comes online`
- **Example**: `!ai Message me and !9e044360 when node XYZ is seen`

### 📋 Managing Your Tasks (User)
You can query and cancel your own tasks at any time:
- **List your tasks**: `!ai What alerts do I have?` or `!ai show my reminders`
- **Cancel a task**: `!ai Cancel [sched-1]`
- **Cancel all**: `!ai Cancel all my alerts`

### 📡 Deferred Telemetry Auto-Response
When the AI requests telemetry from a slow mesh node and times out, it registers a background callback automatically. When the data eventually arrives, the AI proactively sends you the result without requiring you to ask again.

## Tips & Best Practices

1. **Use sessions in DMs** for back-and-forth conversations to save typing
2. **Name your sessions** descriptively for easy recall later
3. **Delete old conversations** when you hit the 10-slot limit
4. **Use `!ai -n` in channels** when you want to start a fresh topic
5. **Check `!ai -m`** periodically to monitor storage usage
6. **Sessions timeout after 5 minutes** - you'll see the session indicator disappear from responses

## License

This project is licensed under the **MIT License**.

- **Meshtastic Integration**: This project depends on the `meshtastic` Python library which is licensed under **GPLv3**.
- **Inspiration & Components**: [MeshMonitor](https://github.com/Yeraze/meshmonitor) components included in this project are licensed under the **BSD 3-Clause License**.

> [!IMPORTANT]
> **GPL Compatibility Note:**
> Because this responder imports and links with `meshtastic` (GPLv3), any distributed binary or Docker image containing these components is effectively subject to the terms of the **GPLv3**.
>
> If you are building upon this project and plan to distribute it, ensure you comply with the requirements of the GPLv3 for the combined work.

See the [LICENSE](LICENSE) file for the full text.

