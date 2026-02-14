# PR: AI Tool Use & Connection Resiliency

This major update introduces **AI Function Calling** for proactive mesh interaction and implements a robust **Radio Watchdog** system for stable connectivity.

## Key Changes

### 🛠️ AI Tool Use (Function Calling)
- **Gemini Integration**: Implemented a multi-turn function calling loop in `GeminiProvider`.
- **Meshtastic Tools**: Defined the following tools for the AI to query the network:
    - `get_my_info`: Returns the bot's own identity, status, and telemetry.
    - `get_mesh_nodes`: Returns a summary of neighboring nodes currently seen on the mesh.
    - `get_node_details`: Returns telemetry for a specific node by name or ID.
- **Strategic Persona**: Refined the system prompt to instruct the AI on "Always Call" identity rules and tool-chaining strategies (e.g., search mesh → resolve ID → get telemetry).
- **Metadata Refactoring**: Removed over 150 lines of noisy, reactive metadata injection logic. The AI now queries what it needs, when it needs it.

### 📡 Radio Watchdog & Resiliency
- Implemented a "Radio Watchdog" that sends active probes if the radio is silent for 5 minutes.
- Added a 10s reconnection loop that automatically recovers explicit Meshtastic connection losses.
- Tracks `connection_healthy` state via library events to detect "zombie" connections.

### 🔄 Session Isolation & Management
- **Strict DM Detection**: Sessions are now strictly isolated to DMs (`to_node == my_id`).
- **Improved History**: Centralized node lookup logic for reliable context management across hex IDs and names.

## Verification

### 🧪 Automated Tests
- `tests/test_reconnection.py`: Verifies automated recovery of lost connections.
- `tests/test_tools.py`: Verifies Gemini's function calling logic and Meshtastic tool handlers.

### 📟 Hardware Verification (Live Node)
- **Environment**: Tested on `COM3` with channel `LN4CY-01` (Indx 1).
- **Identity**: AI correctly called `get_my_info` to identify itself as `L4TA` and reported battery status.
- **Mesh Discovery**: AI correctly called `get_mesh_nodes` and reported seeing 81 nodes on the network.
- **Stability**: Confirmed real-time tool orchestration and connection recovery on physical hardware.

## Impact
This PR significantly elevates the bot's intelligence and reliability. It transitions from a reactive "keyword-guessing" state to a proactive, tool-aware agent capable of maintaining stable connections in complex RF environments.
