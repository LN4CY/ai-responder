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

### 🤖 Proactive Agent Architecture
The AI can now spontaneously send messages to users without being asked:
- **Scheduled Reminders**: Ask the AI to remind you about something in 5 minutes or ping you every 30 seconds for the next 5 minutes.
- **Condition Watchers**: Register alerting rules like "message me when node L4B1's battery drops below 10%" and the AI monitors passively from live mesh telemetry.
- **Deferred Telemetry Callbacks**: When the AI requests telemetry from a slow node and times out, it registers a background listener. When the data finally arrives from the mesh (seconds to minutes later), it proactively delivers it without requiring the user to ask again.

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
| **Multi-Turn Tools** | ✅ Done | Native tool calling for all major AI providers. |
| **Adaptive Logic** | ✅ Done | Automatic fallback between tools and metadata injection. |
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

### Kubernetes / Docker Compose (Recommended)

Add to your `docker-compose.yml`:

```yaml
  ai-responder:
    image: ghcr.io/ln4cy/ai-responder:latest
    environment:
      - MESHTASTIC_HOST=meshmonitor
      - MESHTASTIC_PORT=4404
      - AI_PROVIDER=gemini
      - GEMINI_API_KEY=your_key_here
      - GEMINI_SEARCH_GROUNDING=true # Optional
      - GEMINI_MAPS_GROUNDING=true   # Optional
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

The AI Responder is fully portable and runs on Windows, macOS, or Linux. 

> [!TIP]
> **Upcoming**: We are building a **Universal Web Management Dashboard** to replace all manual setup steps. For now, use the CLI as described in [CONFIG.md](CONFIG.md).

## Configuration

See [CONFIG.md](CONFIG.md) for a complete reference of all environment variables and configuration files.

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `INTERFACE_TYPE` | `tcp` | `tcp` or `serial` |
| `SERIAL_PORT` | `/dev/ttyACM0` | Serial port (e.g. `COM3`) |
| `MESHTASTIC_HOST` | `meshmonitor` | Hostname of TCP interface |
| `MESHTASTIC_PORT` | `4404` | Port of TCP interface |
| `CONNECTION_RETRY_INTERVAL` | `10` | Seconds between reconnect attempts |
| `CHUNK_DELAY` | `15` | Seconds to wait before sending the next chunk of a split message |
| `AI_PROVIDER` | `ollama` | Initial provider (`ollama`, `gemini`, `openai`, `anthropic`) |
| `OLLAMA_HOST` | `ollama` | Hostname for Ollama service |
| `GEMINI_API_KEY` | - | API Key for Google Gemini |
| `ADMIN_NODE_ID` | - | Node ID authorized for admin commands. Supports comma-separated list (e.g. `!1234abcd,!9e044360`) |
| `ALLOWED_CHANNELS` | `0,3` | CSV list of channel indices to listen on |
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

#### Asking Questions

**In Channels:**
```
!ai What is the weather like today?
```
- Requires `!ai` prefix for every message
- Conversation history is automatically saved to a channel-specific slot
- Each channel maintains its own conversation history per user

**In Direct Messages (DMs):**
```
!ai How do I configure my radio?
```
- Can use `!ai` prefix for one-off questions
- Or start a session for continuous conversation (see Sessions below)

### Sessions (DM Only)

Sessions allow you to have continuous conversations without typing `!ai` before every message. Sessions are **only available in Direct Messages**.

#### Starting a Session

**With auto-generated name:**
```
!ai -n
```
Response: `🟢 Session started: 'chat_20260204_183000' (slot 3)`

**With custom name:**
```
!ai -n my_project_discussion
```
Response: `🟢 Session started: 'my_project_discussion' (slot 3)`

#### During a Session

Once a session is active, all your messages are treated as AI queries:
```
What is LoRa?
[🟢 my_project_discussion] LoRa is a long-range...

Tell me more about mesh networks
[🟢 my_project_discussion] Mesh networks are...
```

Notice the `[🟢 session_name]` indicator on all responses.

#### Session Timeout

Sessions automatically end after **5 minutes of inactivity**. You'll receive a proactive notification when this happens:

```
⏱️ Session 'my_project_discussion' ended (timeout after 5 minutes).
```

The AI remembers your routing information to send this alert even if you've stopped chatting. After timeout, existing context is cleared to protect privacy.

#### Ending a Session

```
!ai -end
```
Response: `Session 'my_project_discussion' ended.`

You'll always receive a confirmation message when a session ends, whether manually or by timeout.

### Conversation Management

The AI maintains up to **10 saved conversations** per user, plus automatic channel-specific conversations. All conversation commands are available to **all users** (no admin required).

#### Listing Conversations

```
!ai -c ls
```
Response:
```
📚 Saved Conversations:
1. my_project_discussion (last: 2026-02-04 18:30)
2. chat_20260203_140522 (last: 2026-02-03 14:15)
3. troubleshooting_help (last: 2026-02-02 09:22)
```

#### Recalling a Conversation

**By name:**
```
!ai -c my_project_discussion
```

**By slot number:**
```
!ai -c 1
```

**Most recent conversation:**
```
!ai -c
```

Response: `Loaded conversation 'my_project_discussion' (slot 1)`

After loading, continue the conversation with `!ai <query>` or start a new session.

#### Deleting a Conversation

**By name:**
```
!ai -c rm my_project_discussion
```

**By slot number:**
```
!ai -c rm 1
```

Response: `Deleted conversation 'my_project_discussion' (slot 1)`

**Delete All:**
```
!ai -c rm all
```
Response: `Deleted 5 conversations.`

### Channel vs DM Behavior

#### Channel Mode
- **Every message needs `!ai` prefix**
- `!ai <query>` - Ask a question, auto-saves to channel slot
- `!ai -n <query>` - Clear channel history and ask a new question
- No sessions available
- Each channel maintains separate history per user
- Channel conversations don't count against your 10-slot limit

#### DM Mode
- `!ai <query>` - One-off question
- `!ai -n [name]` - Start a session (continuous conversation)
- `!ai -end` - End current session
- During session: no `!ai` prefix needed
- Sessions use one of your 10 conversation slots

### Memory Status

Check your conversation history usage:
```
!ai -m
```
Response:
```
🧠 Context (Ollama): 8/10 (| History: 24) | 💾 Storage: 0.15/2.00 MB (7.5%) | 📚 Slots: 3/10
```

This shows:
- **Context**: Messages sent to AI (limited by provider)
- **History**: Total messages stored
- **Storage**: Disk space used for your conversations
- **Slots**: Conversation slots used out of 10 available

### New Conversation (Channel Mode)

In channels, use `!ai -n` to clear the channel's conversation history:
```
!ai -n What is Meshtastic?
```
This clears previous channel context and starts fresh with your new query.

## Admin Commands

Admin commands require your node ID to be configured as an admin (see [CONFIG.md](CONFIG.md)).

### Help Menu
```
!ai -h
```
Shows all available commands based on your permission level.

### Provider Management (Admin Only)

**List providers:**
```
!ai -p
```

**Switch provider:**
```
!ai -p local      # Switch to Ollama
!ai -p gemini     # Switch to Google Gemini
!ai -p openai     # Switch to OpenAI
!ai -p anthropic  # Switch to Anthropic Claude
```

### Channel Management (Admin Only)

**List channels:**
```
!ai -ch
!ai -ch ls
```

**Enable a channel:**
```
!ai -ch add 2
!ai -ch add LongFast
```

**Disable a channel:**
```
!ai -ch rm 2
!ai -ch rm LongFast
```

### Admin Management (Admin Only)

**List admins:**
```
!ai -a
```

**Add admin (supports comma-separated list):**
```
!ai -a add !1234abcd
!ai -a add !1234abcd,!5678efgh
!ai -a add me
```

**Remove admin (supports comma-separated list):**
```
!ai -a rm !1234abcd
!ai -a rm !1234abcd,!5678efgh
```

## Proactive Agents

Frontier AI providers (Gemini, Claude, GPT) can spontaneously send messages to users based on time or mesh events. No special command needed — just describe what you want to the AI in plain language.

### Scheduled Reminders

Ask the AI to remind you about something after a delay:
```
!ai Remind me to check the batteries in 5 minutes
```
Response: `✅ Reminder scheduled in 300s about: checking the batteries`

After 5 minutes, the AI will proactively send:
```
⏰ Just a friendly reminder: time to check those batteries!
```

### Recurring Pings

Ask the AI to ping you at a regular interval:
```
!ai Ping me every 30 seconds for the next 5 minutes
```
Response: `✅ Recurring reminder registered! I will send a message every 30s for the next 300s`

The AI will send you a message every 30 seconds until 5 minutes have passed.

### Condition-Based Alerts (Battery / Telemetry Watchers)

Ask the AI to monitor a node's telemetry and alert when a threshold is hit:
```
!ai Message me when node L4B1's battery is below 10%
```
Response: `✅ Watching L4B1: will alert you when battery_level < 10 (L4B1 battery low!)`

Whenever a telemetry packet arrives from that node showing battery at or below the threshold, you'll receive:
```
🔔 Alert: L4B1 battery low! | battery_level = 8% (threshold was < 10)
```

Supported condition metrics: `battery_level`, `voltage`, `temperature`, `humidity`, `barometric_pressure`, `iaq`, `snr`

Supported operators: `<`, `>`, `<=`, `>=`, `==`

### Deferred Telemetry Auto-Response

When the AI requests telemetry from a slow mesh node and times out, it now registers a background callback automatically. When the data eventually arrives, the AI proactively sends you the result:

```
!ai What's the environment telemetry for L4B1?
```
If the node is slow and times out:
```
📡 Sent refresh request. The mesh is slow — I'm watching and will send the data as soon as it arrives!
```
A few seconds/minutes later when the data arrives:
```
📡 Delayed telemetry arrived for !abcd1234:
Node: L4B1 | Temp: 22.1°C | Humidity: 58% | Battery: 87%
```

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

