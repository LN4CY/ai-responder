# Release Notes v2.2.0

This release introduces **MeshCore Support**, a **Protocol-Isolated Architecture**, **First-Class Repeaters & Rooms Awareness**, **Adaptive RF ACK Delivery Tracking**, and full **Proactive Agent Parity** across both Meshtastic and MeshCore mesh networks.

## Major Features & Enhancements 🚀

### 📡 First-Class MeshCore Protocol Support & Isolation
- **Dedicated Protocol Implementations (`responders/meshcore_responder.py` & `responders/meshtastic_responder.py`)**: Clean architectural isolation between protocols with shared base mixins (`core/base_responder.py`).
- **Flexible Hardware Interfaces**: Standalone serial interface (`MESHCORE_INTERFACE=serial`) or TCP companion interface to MeshMonitor Virtual Nodes (`MESHCORE_INTERFACE=tcp`, port `5000`).
- **Protocol-Specific Admin & Channel Isolation**: `MESHCORE_ADMIN_NODE_ID` and `MESHCORE_ALLOWED_CHANNELS` provide independent permissions for MeshCore contacts (display names and 12-char hex public key prefixes) and channels (e.g. Channel 4).

### 🏰 Repeaters & Room Servers Support
- **Protocol Type Extraction**: Decodes MeshCore 1-byte advertisement headers into first-class entity types: `Repeater` (`0x02`), `Room` (`0x03`), `Chat` (`0x01`), and `Sensor` (`0x04`).
- **Automated Knowledge Graph Semantic Indexing**: Contacts are automatically classified into the SQLite Knowledge Graph (`is_a Repeater`, `is_a Room`, `has_hops`), enabling natural language semantic recall for queries like *"what repeaters are near me?"* or *"what rooms are available?"*.
- **Dedicated MeshCore MCP Tools**:
  - `get_meshcore_repeaters`: Lists discovered repeaters, hop distances, and coordinates.
  - `get_meshcore_rooms`: Lists active community rooms and BBS servers.
  - `get_meshcore_status`: Requests live over-the-air status (`req_status_sync`) reporting battery voltage, uptime, noise floor, last SNR, and packet counters.

### ⏱️ Adaptive RF ACK Tracking & Airtime Optimization
- **Physical RF ACK Waiter**: Subscribes to `EventType.ACK` and captures the 4-byte CRC `expected_ack` from firmware responses, awaiting confirmed remote delivery rather than arbitrary fixed delays.
- **Dynamic Pacing Buffer**: Adaptive settling buffer (`MESHCORE_POST_ACK_DELAY=2.0s`) drastically speeds up multi-chunk deliveries while preventing channel collisions.
- **LoRa Thinking Packet Suppression**: Suppresses initial `"Thinking... 🤖"` packets on MeshCore (`MESHCORE_SEND_THINKING=false`) to conserve channel bandwidth and eliminate duplicate retries.
- **Automatic BAD_STATE Recovery**: Automatically retries transmission with exponential backoff when the radio firmware reports `ERR_CODE_BAD_STATE` (busy state).

### 💬 Channel Message Sender Parsing
- **Companion Header Parsing**: Automatically parses companion broadcast messages formatted as `<SenderName>: <Message>` on group channels (e.g. Channel 4).
- **Identity & Trigger Resolution**: Strips sender prefixes to trigger `!ai` commands reliably, resolves public key prefixes from contacts, and correctly attributes conversation turns and admin permissions.

### 🤖 Proactive & Autonomous Agent Parity
- **Full MCP Parity in `mcp_server_meshcore.py`**: Added `schedule_message`, `watch_condition`, `watch_node_online`, `list_proactive_tasks`, `cancel_proactive_task`, and `send_message`.
- **Bi-directional Identity Matching**: Watchers seamlessly match against contact display names (`LN4CY_TDeck`), public key prefixes (`!12a1b2dd1de3`), and resolved node IDs.

## Reliability, Session Management & Firmware Hardening 🛠️
- **Radio Payload Safety (`ERR_CODE_BAD_STATE` Fix)**: Implemented UTF-8 byte-aware chunking (`prepare_chunks` and `split_message_utf8`) ensuring each packet stays $\le 152$ bytes under the MeshCore firmware 160-byte limit (`MAX_TEXT_LEN`), with dedicated reservation for session indicators and chunk counters.
- **Intuitive Session Close Commands**: Added support for `!ai end`, `!ai -end`, `!ai close`, `!ai exit`, and `!ai quit`, as well as plain `end`/`exit`/`quit` within active DM sessions, closing sessions cleanly without unwanted LLM query routing.
- **Thread-Safe Session Timeout Deduplication**: Added re-entrant lock synchronization (`RLock`) across all `SessionManager` methods, eliminating race conditions between the main health loop and incoming packet callbacks and preventing duplicate timeout messages.
- **Radio Health Watchdog Link Stability**: Updated `MeshCoreHandler.send_probe()` and message transmissions to refresh radio `last_activity`, preventing false-positive watchdog kills and restart loops during low RF traffic periods.

---

# Release Notes v2.1.0

This release introduces the **Modular Model Context Protocol (MCP) Architecture**, a **built-in zero-dependency SQLite FTS5 + Knowledge Graph Memory Server**, **Dynamic Model Switching** (Fast vs. Thinking models), and comprehensive reliability & privacy fixes across the entire codebase.

## Major Features & Enhancements 🚀

### 🔌 Modular Model Context Protocol (MCP) Architecture
- **Unified MCP Client (`UnifiedMCPClient`)**: Bridges the synchronous bot processing loop with the asynchronous Python MCP SDK, managing both in-process and external tools.
- **In-Process Meshtastic MCP Server**: Radio operations (live node metadata, short-polled telemetry refresh, proactive scheduling, condition watchers, direct messaging) are exposed as standardized MCP tools via FastMCP.
- **Decoupled Providers**: All provider implementations (`gemini.py`, `openai.py`, `anthropic.py`, `ollama.py`) dynamically discover and execute tools via the unified client, eliminating hardcoded provider-specific schemas.

### 🧠 Built-In SQLite FTS5 + Knowledge Graph Long-Term Memory
- **Zero-Container Semantic Memory**: Built-in SQLite FTS5 (BM25 full-text search) and Knowledge Graph triple store (`mcp_server_memory.py`), eliminating external service dependencies.
- **Automatic Semantic Indexing**: Automatically records and indexes conversations, user preferences, callsigns, and telemetry observations.
- **Knowledge Graph Session Re-hydration**: If conversation JSON files are missing or pruned from disk, historical sessions seamlessly re-hydrate from semantic memory graph hubs.
- **Safe Reset (`!ai -n`) vs. Nuclear Wipe (`!ai -n rm all`)**: Safe reset archives previous context while allowing explicit, permanent memory pruning when requested.

### ⚡ Dynamic Model Switching & Modern Frontier Models
- **Query Complexity Classifier**: Automatically classifies queries as `simple` or `complex`, dynamically routing to fast, low-latency models or deep reasoning models.
- **Next-Gen Gemini & Anthropic Defaults**:
  - Gemini: Defaults updated to `gemini-3.8-flash` (fast) and `gemini-3.1-pro-preview` (thinking).
  - Anthropic: Defaults updated to `claude-haiku-5-5` (fast) and `claude-sonnet-5-5` (thinking).
- **Gemini 3+ & Thought Signature Compatibility**: Preserves model thought signatures and reasoning blocks during multi-turn function calling; uses universal `user`-role returns for function responses required by Gemini 3.8+.
- **Expanded Token Caps**: Anthropic `max_tokens` raised to 1024 to accommodate adaptive thinking models without token starvation.

## Bug Fixes & Codebase Audit 🛠️

- **Channel Privacy Isolation**: Fixed a bug where channel `!ai -n <query>` passed initial message text into the DM flag, causing channel queries to read private DM history and broadcast replies to `^all`. Channel context is now strictly isolated (`Channel:<ch>:<node>`).
- **Delivery ACK vs. Routing NAK Filter**: Fixed `SafeTCPInterface._handleFromRadio` to inspect packet `error_reason` so routing errors (e.g. `MAX_RETRANSMIT`, `NO_RESPONSE`) are never misclassified as successful message deliveries.
- **Gemini Candidate Robustness**: Fixed `KeyError: 'parts'` when Gemini returns finish reasons like `STOP` with empty content blocks or multi-part function calls. Outer retries reset from clean request snapshots.
- **Thread & Concurrency Safety**:
  - Re-entrant locks (`RLock`) guard in-memory history dictionaries and conversation disk saves.
  - Proactive tasks persistence uses atomic file replacement (`.tmp` + `os.replace`) under lock.
- **Session Manager Timeout Return**: `check_timeout()` now safely returns a 4-tuple `(False, None, 0, None)` when a node is inactive, preventing unpacking `TypeError`s.
- **Telemetry Polling Race Protection**: Deferred telemetry callbacks now mark active synchronous short-polling to prevent competing timer triggers.
- **Provider History Deduplication**: Providers check user prompt containment in trailing history to avoid duplicated prompt turns or missing user context.

---

# Release Notes v2.0.0

This major release introduces the powerful new **Proactive Agent Architecture** alongside critical reliability fixes, transforming `ai-responder` from a simple question-answering bot into an active, intelligent mesh assistant.

## Features & Enhancements 🚀

### Proactive Agent Architecture
The AI can now proactively monitor the mesh and send messages dynamically, enabling powerful new agentic capabilities:

- **Condition Watchers**: The AI can now monitor telemetry metrics and alert you when thresholds are met (e.g., `!ai alert me if L4B1 battery drops below 10%`).
- **Node-Online Alerts**: You can ask the AI to notify you when a specific node comes online or enters the mesh.
- **Dynamic Scheduling**: You can now schedule one-off or recurring reminders (`!ai remind me to check the batteries in 30 minutes`).
- **Deferred Telemetry**: Slow telemetry requests are now automatically deferred and handled asynchronously. Instead of "ask again in 60 seconds", the AI will automatically fetch the data and message you when it arrives.
- **Enhanced AI Context**: The AI can now manage its own context strings for future scheduled events.

### Help System and Responses
- **Refined Help Menu (`!ai -h`)**: Restored and improved the multi-message format, categorizing commands (Basic, Session, Task, Admin, Example) carefully so they fit well within Meshtastic message limits.
- **Better Context Clearing (`!ai -n`)**: Refined to correctly recognize Direct Messages vs. Channel context.
- **Timezone Awareness**: Timezone data is now dynamically injected via prompt engineering to ensure scheduled callbacks respect your local time.

## Bug Fixes & Stability 🛠️

- **Unbounded Queue Memory Leak (PR #15)**: Fixed a major issue where the `MessageQueue` could grow unconditionally under burst traffic. Added `MESH_MAX_QUEUE_SIZE` (default 500) that safely drops stale messages.
- **Linux CI Permission Errors**: Fixed hardcoded test paths that broke GitHub Actions across Linux runner environments.
- **Redundant AI Replies (Silent ACKs)**: The AI provider chain now correctly suppresses redundant text replies when a proactive telemetry tool already fired successfully, preventing chat spam.
- **Multi-part Tool Execution Execution**: Fixed an issue causing providers (especially Gemini) to stop short and answer text requests while failing to execute the concurrent tool calls (e.g., `provide a map AND find the nearest store`).

## Documentation Updates 📖
- All documentation has been comprehensively updated with proactive command examples.
- Docker examples now correctly reference the `ghcr.io/ln4cy/ai-responder:latest` image version to ensure operators always pull the most up-to-date image.
