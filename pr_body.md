# PR: Connection Resiliency & Radio Watchdog

This PR implements a robust health check and reconnection system adopted from the `mqtt-proxy` project. It addresses the issue where `ai-responder` fails to reconnect after the Meshtastic virtual node (`meshmonitor`) is restarted.

## Key Changes

### 📡 Radio Watchdog Logic
- Added `last_activity` tracking to `MeshtasticHandler` which updates on every received packet.
- Implemented a "Radio Watchdog" in the main loop of `ai_responder.py`.
- If the radio is silent for more than 5 minutes (`HEALTH_CHECK_ACTIVITY_TIMEOUT`), the bot now sends an active probe (Position Query).
- If the probe is not acknowledged within 30 seconds, the bot considers the connection "zombie" and exits with `sys.exit(1)`.

### 🔒 Session Isolation & Location Accuracy
- **Strict DM Detection**: The bot now strictly identifies DMs by checking `to_node == my_id`. Active DM sessions no longer capture channel traffic.
- **Session Indicator Isolation**: Responses to channel `!ai` commands no longer include the `[🟢 session_name]` prefix.
- **Dynamic Node Metadata**: Improved `get_node_info` to pull dynamic data (Position, Metrics) from the Meshtastic `nodes` database for the local node.
- **Robust Node Lookups**: Added a centralized `_get_node_by_id` helper to correctly handle hex string nodes (e.g., `!1234abcd`).

### 🔍 Mesh Discovery & Multi-Node Queries
- **Advanced Search**: Added `find_node_by_name` to search nodes by Long/Short name (case-insensitive).
- **Multi-Node Detection**: AI now detects hex IDs and names in prompts and injects telemetry for all referenced nodes.
- **Mesh Status**: Users can ask "who is online?" or "list nodes" to see a summary of all active neighbors.
- **System Prompt Updates**: Revised prompts to guide AI on interpreting multi-node metadata.

### 🔄 Prolonged Connection Loss Handling
- Detects if the Meshtastic interface reports being disconnected for more than 60 seconds.
- Forces a process exit if the connection is unrecoverable, relying on the Docker restart policy (`restart: unless-stopped`) to perform a clean reconnection.

### 🏥 Health Diagnostic Improvements
- Enhanced `/tmp/healthy` heartbeat management.
- The heartbeat file is now immediately deleted upon any health check failure, ensuring the Docker healthcheck accurately reflects the system state.

### ⚙️ Enhanced Configuration
- Added new environment variables to `config.py` for fine-tuning the watchdog behavior:
  - `HEALTH_CHECK_ACTIVITY_TIMEOUT` (Default: 300s)
  - `HEALTH_CHECK_PROBE_INTERVAL` (Default: 150s)

## Verification
- Added new unit tests in `tests/test_handler.py` to verify:
  - Activity tracking on packet reception.
  - Radio probe transmission logic.
- All 14 tests in `tests/test_handler.py` are passing.
- Manual verification confirms `/tmp/healthy` is correctly managed.

## Impact
This change significantly improves the "hands-off" reliability of the `ai-responder` in complex network environments where virtual nodes may restart or become unresponsive.
