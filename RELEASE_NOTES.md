# Release Notes - v1.4.0

## 🚀 Overview
Version 1.4.0 introduces significant improvements in connection resiliency, context-aware AI grounding (with Gemini), and extended telemetry capabilities. This release also formalizes the project's licensing structure and prepares the codebase for future web dashboard integration.

## ✨ New Features

### 🔗 Connection Resiliency
- **Automatic Reconnection Loop**: The responder now intelligently detects connection loss to the radio or MeshMonitor and attempts to reconnect automatically.
- **Configurable Retries**: New configuration options `CONNECTION_RETRY_INTERVAL` and `CONNECTION_MAX_RETRIES` allow finetuning of recovery behavior.
- **Radio Watchdog**: A background monitor proactively checks for "zombie" connections where the interface appears up but data is not flowing.

### 🧠 Advanced Context & Grounding (Gemini Provider)
- **GPS Awareness**: The AI now knows the node's own location, enabling location-relative queries (e.g., "What is the weather here?").
- **Surgical Tool Fallback**: Implemented a smart fallback mechanism that injects metadata directly into the context when tool calls fail or for simple queries, reducing latency.
- **Google Search & Maps Integration**: 
  - Enable `GEMINI_SEARCH_GROUNDING=true` to let the AI fetch real-time data from the web.
  - Enable `GEMINI_MAPS_GROUNDING=true` for location-based services.

### 📡 Telemetry & Metadata
- **Dual Metadata reasoning**: The AI can now distinguish and reason about both *local node* metadata (self) and *remote node* metadata (user), preventing confusion in responses.
- **On-Demand Telemetry**: If a user asks for sensor data (e.g., "humidity") and it's stale or missing, the AI now proactively requests an update from the mesh instead of just saying "I don't know."

## 🛠 Fixes & Improvements
- **Duplicate Log Suppression**: Fixed an issue where the same log message would appear multiple times in the console.
- **Session Handling**: Resolved a bug where `end_session` could crash if the session file was missing or corrupted.
- **Docker Optimization**: The Dockerfile now supports custom volume mounts more flexibly.
- **License Headers**: Added SPDX license identifiers and full MIT license text to all source files.

## ⚙️ Configuration Updates
- Added `VERSION` constant to `config.py`.
- New environment variables for Grounding: `GEMINI_SEARCH_GROUNDING`, `GEMINI_MAPS_GROUNDING`.

## 📦 Upgrade Instructions
1.  Pull the latest Docker image: `docker pull ghcr.io/ln4cy/ai-responder:latest`
2.  Update your `docker-compose.yml` if you wish to enable the new Grounding features.
3.  Restart the container: `docker-compose up -d`
