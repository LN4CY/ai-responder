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
