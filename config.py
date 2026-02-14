# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Configuration management for AI Responder."""

import os
import json
import logging

logger = logging.getLogger(__name__)

# Environment Variables
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
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')
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

# System Prompts
SYSTEM_PROMPT_LOCAL_FILE = os.getenv('SYSTEM_PROMPT_LOCAL_FILE', 'system_prompt_local.txt')
SYSTEM_PROMPT_ONLINE_FILE = os.getenv('SYSTEM_PROMPT_ONLINE_FILE', 'system_prompt_online.txt')

DEFAULT_SYSTEM_PROMPT_LOCAL = """You are a helpful AI assistant on the Meshtastic mesh network.
CONTEXT ISOLATION:
- Each conversation is a separate sandbox. Never leak data between them.
- Current Context ID: {context_id}

PERSONA:
- Keep responses concise (under 200 chars) for mesh efficiency.
- You receive [Node ID] and minimal environment metadata with user messages."""

DEFAULT_SYSTEM_PROMPT_ONLINE = """You are a helpful AI assistant on the Meshtastic mesh network.
CONTEXT ISOLATION:
- Each conversation is a separate sandbox. Never leak data between them.
- Current Context ID: {context_id}

MESHTASTIC TOOLS:
- You have access to real-time tools to query the Meshtastic network.
- get_my_info: ALWAYS call this if asked about your identity, name, battery, SNR, or status. Do NOT guess your name from previous context if this tool is available.
- get_mesh_nodes: Use this to get a list of all nodes currently seen on the mesh. Use this first if a user asks "who is online" or if you need to find a Node ID for a specific name.
- get_node_details(node_id_or_name): Use this to get telemetry for a specific node. You can pass a name (e.g. "Alice") or a Hex ID (e.g. "!1234abcd").
- LOGIC: If a user asks about a node by name and you don't have its ID, call `get_mesh_nodes` first, find the ID, then call `get_node_details`.

RESPONSE STYLE:
- Keep responses concise (under 200 chars) for mesh efficiency.
- User messages are tagged [Node ID]. Use tools for all other mesh data."""

# Meshtastic Configuration
ACK_TIMEOUT = int(os.getenv('ACK_TIMEOUT', '60'))
CONNECTION_RETRY_INTERVAL = int(os.getenv('CONNECTION_RETRY_INTERVAL', '10')) # Seconds between reconnections
CONNECTION_MAX_RETRIES = int(os.getenv('CONNECTION_MAX_RETRIES', '3')) # Initial connection retries

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
    
    try:
        if os.path.exists(prompt_file):
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt = f.read().strip()
                if prompt:
                    logger.info(f"Loaded system prompt from {prompt_file}")
                    return prompt.format(context_id=context_id)
    except Exception as e:
        logger.warning(f"Failed to load system prompt from {prompt_file}: {e}")
    
    logger.info(f"Using default system prompt for {provider}")
    try:
        return default.format(context_id=context_id)
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
