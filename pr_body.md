# PR: Connection Resiliency & Radio Watchdog

This PR implements a robust health check and reconnection system adopted from the `mqtt-proxy` project. It addresses the issue where `ai-responder` fails to reconnect after the Meshtastic virtual node (`meshmonitor`) is restarted.

## Key Changes

### 📡 Radio Watchdog Logic
- Added `last_activity` tracking to `MeshtasticHandler` which updates on every received packet.
- Implemented a "Radio Watchdog" in the main loop of `ai_responder.py`.
- If the radio is silent for more than 5 minutes (`HEALTH_CHECK_ACTIVITY_TIMEOUT`), the bot now sends an active probe (Position Query).
- If the probe is not acknowledged within 30 seconds, the bot considers the connection "zombie" and exits with `sys.exit(1)`.

### 🔒 Session Isolation Fix (DM vs Channel)
- Fixed a bug where active DM sessions would incorrectly capture messages sent in channels from the same node.
- Validated `is_dm` status before allowing session capture in `on_receive`.
- Corrected a typo in local node ID lookup (`noId` -> `nodeId`).

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
