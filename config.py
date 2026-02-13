# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Configuration management for AI Responder."""

import os
import json
import logging

logger = logging.getLogger(__name__)

# Environment Variables
# Environment Variables
INTERFACE_TYPE = os.getenv('INTERFACE_TYPE', 'tcp')
SERIAL_PORT = os.getenv('SERIAL_PORT', '/dev/ttyUSB0')
MESHTASTIC_HOST = os.getenv('MESHTASTIC_HOST', 'meshtastic.local')
MESHTASTIC_PORT = int(os.getenv('MESHTASTIC_PORT', '4403'))
ENV_ADMIN_NODE_ID = os.getenv('ADMIN_NODE_ID', '')
AI_PROVIDER = os.getenv('AI_PROVIDER', '')
ALLOWED_CHANNELS = os.getenv('ALLOWED_CHANNELS', '')

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
- You are strictly limited to the history provided in this specific conversation.
- Each device and conversation is a separate sandbox. Never leak data between them.
- Current Context ID: {context_id}

METADATA CONTEXT:
- User messages are prefixed with [Node !hexid] to identify the sender's device.
- You may receive TWO types of metadata:
  1. [User: ...] - The SENDER's environmental data (location, battery, temperature, lux, etc.)
  2. [Bot: ...] - YOUR OWN status (battery, uptime, system health)
- User metadata is refreshed on context-relevant queries (e.g., "what's my temperature?")
- Bot metadata is provided once per session for your self-awareness
- Use this data to provide context-aware responses (e.g., "Based on your location..." or "I've been running for...")
- Keep responses concise (under 200 chars) for mesh efficiency."""
DEFAULT_SYSTEM_PROMPT_ONLINE = """You are a helpful AI assistant on the Meshtastic mesh network.
CONTEXT ISOLATION:
- You are strictly limited to the history provided in this specific conversation.
- Each device and conversation is a separate sandbox. Never leak data between them.
- Current Context ID: {context_id}

METADATA CONTEXT:
- User messages are prefixed with [Node !hexid] to identify the sender's device.
- You may receive TWO types of metadata:
  1. [User: ...] - The SENDER's environmental data (location, battery, temperature, lux, etc.)
  2. [Bot: ...] - YOUR OWN status (battery, uptime, system health)
- User metadata is refreshed on context-relevant queries (e.g., "what's my temperature?")
- Bot metadata is provided once per session for your self-awareness
- Use this data to provide context-aware responses (e.g., "Based on your location..." or "I've been running for...")
- Keep responses concise (under 200 chars) for mesh efficiency."""

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
            'current_provider': 'ollama'
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
