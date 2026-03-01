# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Configuration management for AI Responder."""

import os
import json
import logging
import datetime

logger = logging.getLogger(__name__)

# Environment Variables
VERSION = "1.5.0"
INTERFACE_TYPE = os.getenv('INTERFACE_TYPE', 'tcp')
SERIAL_PORT = os.getenv('SERIAL_PORT', '/dev/ttyUSB0')
MESHTASTIC_HOST = os.getenv('MESHTASTIC_HOST', 'meshtastic.local')
MESHTASTIC_PORT = int(os.getenv('MESHTASTIC_PORT', '4403'))
ENV_ADMIN_NODE_ID = os.getenv('ADMIN_NODE_ID', '')
AI_PROVIDER = os.getenv('AI_PROVIDER', '')
ALLOWED_CHANNELS = os.getenv('ALLOWED_CHANNELS', '')
MESHTASTIC_AWARENESS = os.getenv('MESHTASTIC_AWARENESS', 'true').lower() == 'true'

# AI Provider Configuration
OLLAMA_HOST = os.getenv('OLLAMA_HOST', 'ollama')
OLLAMA_PORT = os.getenv('OLLAMA_PORT', '11434')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'llama3.2:1b')
OLLAMA_MAX_MESSAGES = int(os.getenv('OLLAMA_MAX_MESSAGES', '30'))

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
GEMINI_SEARCH_GROUNDING = os.getenv('GEMINI_SEARCH_GROUNDING', 'false').lower() == 'true'
GEMINI_MAPS_GROUNDING = os.getenv('GEMINI_MAPS_GROUNDING', 'false').lower() == 'true'
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-3-haiku-20240307')

# History and Storage Configuration
HISTORY_DIR = os.getenv('HISTORY_DIR', '/app/data/history')
HISTORY_MAX_MESSAGES = int(os.getenv('HISTORY_MAX_MESSAGES', '100'))
HISTORY_MAX_BYTES = int(os.getenv('HISTORY_MAX_BYTES', '2097152'))  # 2MB

# Conversation Configuration
CONVERSATIONS_DIR = os.getenv('CONVERSATIONS_DIR', '/app/data/conversations')
MAX_CONVERSATIONS = int(os.getenv('MAX_CONVERSATIONS', '10'))

# Session Configuration
SESSION_TIMEOUT = int(os.getenv('SESSION_TIMEOUT', '300'))  # 5 minutes

# Proactive Task Configuration
MAX_PROACTIVE_TASKS_PER_USER = int(os.getenv('MAX_PROACTIVE_TASKS_PER_USER', '50'))
PROACTIVE_TASKS_FILE = "proactive_tasks.json"

# System Prompts
SYSTEM_PROMPT_LOCAL_FILE = os.getenv('SYSTEM_PROMPT_LOCAL_FILE', 'system_prompt_local.txt')
SYSTEM_PROMPT_ONLINE_FILE = os.getenv('SYSTEM_PROMPT_ONLINE_FILE', 'system_prompt_online.txt')

DEFAULT_SYSTEM_PROMPT_LOCAL = """You are a helpful AI assistant on the Meshtastic mesh network.
CONTEXT ISOLATION:
- Each conversation is a separate sandbox. Never leak data between them.
- Current Context ID: {context_id}
- Current Local Time: {current_time}

PERSONA:
- Keep responses concise (under 200 chars) for mesh efficiency.
- You receive [Node ID] and minimal environment metadata with user messages."""

DEFAULT_SYSTEM_PROMPT_ONLINE = """You are a helpful AI assistant on the Meshtastic mesh network.
CONTEXT ISOLATION:
- Each conversation is a separate sandbox. Never leak data between them.
- Current Context ID: {context_id}
- Current Local Time: {current_time}

TOOL USAGE PROTOCOL:
1. MESHTASTIC TOOLS (Data Gathering Only):
   - Use these ONLY to fetch raw data from the mesh (nodes, telemetry, status).
   - "get_my_info": Call for your own identity/status.
    - "get_mesh_nodes": "Who is online" or find Node IDs. Includes calculated distances from the bot.
    - "get_node_details(node_id_or_name)": Meshtastic Data (Cached). View last known identity, signal (SNR), and ALL sensor data (Battery, Temp, Hum, Air Quality, etc). CALL THIS FIRST.
    - "request_node_telemetry(node_id_or_name, telemetry_type)": Meshtastic Refresh (Active). Force an over-the-air update for a specific sensor type (device, environment, local_stats, air_quality, power, health, host). CALL ONLY if data is missing or stale. If it times out, a deferred callback is registered automatically—no need to tell the user to ask again.

2. INTERNAL REASONING (Calculations & Logic):
   - You MUST use your own internal capabilities for math, analysis, and logic.
   - DO NOT look for tools to calculate distance, convert units, or format data.
   - Example: If you have two sets of coordinates from tool outputs, YOU calculate the distance yourself.

3. LOCATION RESOLUTION:
   - "get_location_address(lat, lon)": Use this to convert raw latitude/longitude coordinates into a human-readable street address, city, and state.
   - MAP LINKS: If the user asks for directions or to see a location, generate a clickable Google Maps URL. You MUST NOT use spaces in the URL. Either URL-encode the addresses (using '+' or '%20') or use pure coordinates. Example: `https://www.google.com/maps/dir/[start_lat],[start_lon]/[end_lat],[end_lon]`

4. GOOGLE SEARCH & MAPS (New/External Info & Places):
   - "google_search_stub(query)": Use this to search the web for real-time info (weather, news), OR to find nearby places/businesses (e.g., "closest pharmacy to [Address]").
   - If you only have coordinates, use `get_location_address` FIRST to get a readable address, then use `google_search_stub` with that address to find nearby places.
   - DO NOT use search for general knowledge (history, science, definitions). Use your internal model for that.

5. PROACTIVE AGENT TOOLS (Schedule, Monitor & Manage):
   - Use these when a user asks you to notify them later, watch for something, or manage existing tasks.
   - These tools only work from Direct Messages (DMs). Reject politely if user is in a channel.
   - "schedule_message(delay_seconds=None, context_note, recur_interval_seconds=None, max_duration_seconds=None, notify_targets=None, absolute_time=None)":
     * Use for relative: "Remind me in 5 minutes" -> delay_seconds=300.
     * Use for absolute: "Remind me at 10:00 PM" -> absolute_time="22:00". Use {current_time} to decide if today or tomorrow.
     * notify_targets: comma-separated list of who receives the alert. Options: "requester" (default), "!nodeid", "ch:0" (channel, if enabled).
     * Returns a task ID like [sched-1]. Always confirm it with the user.
   - "watch_condition(node_id_or_name, metric, operator, threshold, context_note, notify_targets=None)":
     * Use for: "Alert me when L4B1 battery < 10%", "Tell me if SNR drops below -10".
     * Supported metrics: battery_level, voltage, temperature, humidity, barometric_pressure, iaq, snr.
     * Supported operators: <, >, <=, >=, ==.
     * Returns a task ID like [cond-2]. Always confirm the watcher and the node.
   - "watch_node_online(node_id_or_name, context_note, notify_targets=None)":
     * Use for: "Message me when L4B1 comes online", "Alert me when node XYZ is seen on the mesh".
     * Returns a task ID like [node-3]. Always confirm.
   - "list_proactive_tasks()":
     * Use when user asks "what alerts do I have?", "show my reminders", "what am I watching?".
     * Returns only the caller's own tasks with their IDs and remaining time.
   - "cancel_proactive_task(task_id)":
     * Use when user says "cancel [sched-1]", "remove my battery alert", "cancel all my alerts".
     * Pass task_id="all" to cancel everything the user registered.

LOGIC FLOW:
- User asks about Mesh -> Call Meshtastic Tool -> Get Data -> Analyze Internally -> Respond.
- User asks about General Knowledge -> Use Internal Model -> Respond.
- User asks about Real-time/New Info OR Explicitly asks to Search -> Call Google Search -> Respond.
- User asks for Math/Distance -> Use Internal Reasoning.
- User asks to be notified/reminded LATER -> Call schedule_message or watch_condition or watch_node_online immediately, then confirm with task ID.
- User asks to send a message to another node/channel NOW -> Call send_message tool.
- User asks what alerts they have -> Call list_proactive_tasks.
- User asks to cancel an alert -> Call cancel_proactive_task.
- Multi-part request (e.g. "show my location AND nearest store") -> Complete ALL parts NOW using sequential tool calls in the SAME response. NEVER say "I will also find X" or "now I'll look up Y" — call the tool immediately and include the result before responding.

RESPONSE STYLE:
- Keep responses concise (under 200 chars) for mesh efficiency.
- User messages are tagged [Node ID]. Use tools for all other mesh data."""

# Meshtastic Configuration
MESH_MAX_QUEUE_SIZE = int(os.getenv('MESH_MAX_QUEUE_SIZE', '500'))
ACK_TIMEOUT = int(os.getenv('ACK_TIMEOUT', '60'))
CONNECTION_RETRY_INTERVAL = int(os.getenv('CONNECTION_RETRY_INTERVAL', '10')) # Seconds between reconnections
CONNECTION_MAX_RETRIES = int(os.getenv('CONNECTION_MAX_RETRIES', '3')) # Initial connection retries
CHUNK_DELAY = int(os.getenv('CHUNK_DELAY', '15')) # Seconds to delay between sending split message chunks

# Health check and Radio Watchdog
HEALTH_CHECK_ACTIVITY_TIMEOUT = int(os.getenv('HEALTH_CHECK_ACTIVITY_TIMEOUT', '300')) # 5 minutes default
HEALTH_CHECK_PROBE_INTERVAL = int(os.getenv('HEALTH_CHECK_PROBE_INTERVAL', str(HEALTH_CHECK_ACTIVITY_TIMEOUT // 2)))

CONFIG_FILE = os.getenv('CONFIG_FILE', '/app/data/config.json')


def load_system_prompt(provider, context_id="Unknown"):
    """Load system prompt from file based on provider type."""
    if provider in ['ollama', 'local']:
        prompt_file = SYSTEM_PROMPT_LOCAL_FILE
        default = DEFAULT_SYSTEM_PROMPT_LOCAL
    else:
        prompt_file = SYSTEM_PROMPT_ONLINE_FILE
        default = DEFAULT_SYSTEM_PROMPT_ONLINE
    
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        if os.path.exists(prompt_file):
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt = f.read().strip()
                if prompt:
                    logger.info(f"Loaded system prompt from {prompt_file}")
                    return prompt.format(context_id=context_id, current_time=current_time)
    except Exception as e:
        logger.warning(f"Failed to load system prompt from {prompt_file}: {e}")
    
    logger.info(f"Using default system prompt for {provider}")
    try:
        return default.format(context_id=context_id, current_time=current_time)
    except:
        return default


class Config:
    """Configuration manager for AI Responder."""
    
    def __init__(self, config_file=None):
        if config_file is None:
            config_file = CONFIG_FILE
        self.config_file = config_file
        self.is_new = not os.path.exists(self.config_file)
        self.data = self.load()
    
    def load(self):
        """Load configuration from file."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        
        # Default configuration
        return {
            'allowed_channels': [0],
            'admin_nodes': [],
            'current_provider': 'ollama',
            'meshtastic_awareness': True
        }
    
    def save(self):
        """Save configuration to file."""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.data, f, indent=2)
            logger.info("Configuration saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
    
    def get(self, key, default=None):
        """Get configuration value."""
        return self.data.get(key, default)
    
    def set(self, key, value):
        """Set configuration value."""
        self.data[key] = value
    
    def __getitem__(self, key):
        """Allow dict-like access."""
        return self.data[key]
    
    def __setitem__(self, key, value):
        """Allow dict-like assignment."""
        self.data[key] = value
