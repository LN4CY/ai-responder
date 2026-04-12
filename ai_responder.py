#!/usr/bin/env python3
# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.
"""
AI Responder for Meshtastic

A modular AI assistant that responds to messages on the Meshtastic mesh network.
Supports multiple AI providers (Ollama, Gemini, OpenAI, Anthropic) with conversation
persistence and session management.

Author: AI Responder Team
License: MIT
"""

import os
import time
import json
import logging
import threading
import sys
import re
import itertools
import requests
import pathlib
import datetime
from concurrent.futures import ThreadPoolExecutor



# Import our modular components
import config
from config import (
    Config, INTERFACE_TYPE, SERIAL_PORT, MESHTASTIC_HOST, MESHTASTIC_PORT,
    ENV_ADMIN_NODE_ID, ALLOWED_CHANNELS, AI_PROVIDER,
    HEALTH_CHECK_ACTIVITY_TIMEOUT
)
from providers import get_provider
from conversation.manager import ConversationManager
from conversation.session import SessionManager
from meshtastic_handler import MeshtasticHandler
from mcp_client import UnifiedMCPClient
from mcp_server_meshtastic import init_meshtastic_mcp

# Logging setup
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    force=True  # Prevent duplicate handlers
)
logger = logging.getLogger('AI-Responder')

# Set logging level for handler based on global log_level
logging.getLogger('meshtastic_handler').setLevel(getattr(logging, log_level, logging.INFO))


__version__ = "2.0.0"

class AIResponder:
    """
    Main AI Responder application.
    
    Orchestrates message handling, AI provider interactions, conversation management,
    and Meshtastic communication.
    """
    
    def __init__(self, history_dir=None):
        """Initialize AI Responder with all components."""
        # Core components
        self.config = Config()
        
        # Paths
        if history_dir is None:
            history_dir = config.HISTORY_DIR
        self.history_dir = history_dir
        self.meshtastic = MeshtasticHandler(
            interface_type=INTERFACE_TYPE,
            serial_port=SERIAL_PORT,
            tcp_host=MESHTASTIC_HOST,
            tcp_port=MESHTASTIC_PORT
        )
        self.conversation_manager = ConversationManager()
        self.session_manager = SessionManager(self.conversation_manager)
        
        # Initialize MCP Architecture
        init_meshtastic_mcp(self)
        self.mcp_client = UnifiedMCPClient(self.config)
        
        # Track nodes that need a metadata refresh in their next message
        self._refresh_metadata_nodes = set()
        
        # State management
        self.running = True
        self.last_activity = time.time()
        self.last_probe = 0
        self.connection_lost = False
        
        # In-memory history cache
        # Structure: {user_id: [{'role': 'user'/'assistant', 'content': '...'}]}
        self.history = {}
        
        # Worker tracking
        self._active_workers = {} # {thread_id: {start_time, from_node, to_node, channel}}
        self._workers_lock = threading.Lock()
        
        # --- Proactive Agent State ---
        # Task ID counter (thread-safe via itertools.count)
        self._task_counter = itertools.count(1)
        
        # Time-based scheduled tasks (one-shot or recurring)
        # Each entry: {id, next_time, end_time, interval, context_note, from_node, to_node, channel, targets}
        self.scheduled_tasks = []
        self._scheduled_tasks_lock = threading.Lock()
        
        # Condition-based watchers: fire when telemetry from a specific node meets criteria
        # Each entry: {id, node_id, metric, operator, threshold, context_note, from_node, to_node, channel, targets}
        self.condition_watchers = []
        self._condition_watchers_lock = threading.Lock()
        
        # Pending deferred telemetry callbacks: fire when target node's telemetry arrives
        # Keyed by node_id -> {from_node, to_node, channel, context_note}
        self.pending_telemetry_requests = {}
        
        # Node-online watchers: fire when any packet arrives from a watched node
        # Each entry: {id, node_id, context_note, from_node, to_node, channel, targets}
        self.node_online_watchers = []
        self._node_online_watchers_lock = threading.Lock()
        
        # --- Collector Pattern State ---
        # Buffers multiple events for a node to fire a single combined AI turn
        self._proactive_event_collector = {} # {node_id_lower: {'timer': Timer, 'events': [str], 'targets': str, 'from_node': str, 'to_node': str, 'channel': int}}
        self._collector_lock = threading.Lock()
        
        # --- Background Memory Indexing ---
        self._bg_executor = ThreadPoolExecutor(max_workers=3)
        self._mcp_indexing_cache = {} # {node_id: {type: last_index_time}}
        
        # Ensure history directory exists
        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)
            logger.info(f"Created history directory: {self.history_dir}")
        
        # Initial task load from disk
        self._load_proactive_tasks()
        
        # Initialize admin from environment variable
        if ENV_ADMIN_NODE_ID:
            try:
                # Parse "!abc,!def" string into list and merge with existing
                new_admins = [n.strip() for n in ENV_ADMIN_NODE_ID.split(',') if n.strip()]
                if new_admins:
                    admin_nodes = self.config.get('admin_nodes', [])
                    updated = False
                    for node in new_admins:
                        if node not in admin_nodes:
                            admin_nodes.append(node)
                            updated = True
                    if updated:
                        self.config['admin_nodes'] = admin_nodes
                        self.config.save()
                        logger.info(f"Updated admin nodes from environment: {new_admins}")
            except Exception as e:
                logger.warning(f"Failed to parse ADMIN_NODE_ID '{ENV_ADMIN_NODE_ID}': {e}")
        
        # Auto-cleanup corrupted config entries
        # If a past bug (or direct ENV injection) inserted "!node1,!node2" as a single string,
        # we flatten and re-save the list here so permissions work correctly.
        current_admins = self.config.get('admin_nodes', [])
        cleaned_admins = []
        needs_cleanup = False
        
        for item in current_admins:
            if ',' in item:
                needs_cleanup = True
                cleaned_admins.extend([n.strip() for n in item.split(',') if n.strip()])
            else:
                cleaned_admins.append(item.strip())
                
        if needs_cleanup:
            # Deduplicate while preserving order
            final_admins = []
            for item in cleaned_admins:
                if item not in final_admins:
                    final_admins.append(item)
            
            logger.info(f"🧹 Auto-cleaned corrupted admin_nodes list from config: {final_admins}")
            self.config['admin_nodes'] = final_admins
            self.config.save()
        
        # Initialize allowed channels from environment variable
        if ALLOWED_CHANNELS:
            try:
                # Parse "0,1,2" string into [0, 1, 2] list
                channels = [int(c.strip()) for c in ALLOWED_CHANNELS.split(',') if c.strip().isdigit()]
                if channels:
                    # Only apply environment variable if config is new/missing this key
                    # OR if the user explicitly wants to keep them in sync (we assume if it's new it's safe)
                    if self.config.is_new or 'allowed_channels' not in self.config.data:
                        self.config['allowed_channels'] = channels
                        self.config.save()
                        logger.info(f"Initialized allowed channels from environment: {channels}")
            except Exception as e:
                logger.warning(f"Failed to parse ALLOWED_CHANNELS '{ALLOWED_CHANNELS}': {e}")
        
        # Initialize provider from environment variable
        if AI_PROVIDER:
            # Only apply if config is new/missing key
            if self.config.is_new or 'current_provider' not in self.config.data:
                self.config['current_provider'] = AI_PROVIDER
                self.config.save()
                logger.info(f"Initialized AI provider from environment: {AI_PROVIDER}")
        
        # Admin and Channel init...
        
        # Touch health file initially
        self.touch_health()

    def touch_health(self):
        """Touch the healthy file to indicate the service is running."""
        try:
            pathlib.Path("/tmp/healthy").touch()
        except Exception:
            pass
    
    # ==================== History Management ====================
    
    def _get_history_key(self, from_node, channel, is_dm):
        """
        Generate a unique key for history isolation.
        
        Args:
            from_node: Source node ID
            channel: Channel index
            is_dm: Whether it's a DM
            
        Returns:
            str: Unique key for history
        """
        # Active sessions are DM-only. If we are in a channel context, 
        # we MUST ignore any background sessions to prevent cross-context spills.
        if is_dm:
            session_name = self.session_manager.get_session_name(from_node)
            if session_name:
                return session_name
            return f"DM:{from_node}"
        else:
            return f"Channel:{channel}:{from_node}"

    def _get_history_path(self, key):
        """
        Get the file path for a specific history key.
        
        Args:
            key: History key (Node ID, Channel:Node, etc.)
            
        Returns:
            str: Absolute path to history file
        """
        # Sanitize key for filesystem
        # 1. Replace common delimiters
        safe_key = key.replace(':', '_').replace('^', 'B')
        # 2. Strict alphanumeric/underscore/hyphen filter for the final filename
        import re
        safe_key = re.sub(r'[^a-zA-Z0-9_\-]', '', safe_key)
        
        if not safe_key:
            safe_key = "unknown_history"
            
        return os.path.join(self.history_dir, f"{safe_key}.json")
    
    def load_history(self, user_id):
        """
        Load conversation history from disk into memory.
        
        Args:
            user_id: Unique identifier for the user
        """
        history_path = self._get_history_path(user_id)
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r') as f:
                    self.history[user_id] = json.load(f)
                logger.info(f"Loaded history for {user_id}: {len(self.history[user_id])} messages")
            except Exception as e:
                logger.error(f"Failed to load history for {user_id}: {e}")
                self.history[user_id] = []
        else:
            self.history[user_id] = []
    
    def save_history(self, user_id):
        """
        Save conversation history from memory to disk.
        
        Implements size and message count limits to prevent unbounded growth.
        
        Args:
            user_id: Unique identifier for the user
        """
        if user_id not in self.history:
            return
        
        history_path = self._get_history_path(user_id)
        try:
            # Enforce message count limit
            if len(self.history[user_id]) > config.HISTORY_MAX_MESSAGES:
                logger.warning(f"History for {user_id} exceeded {config.HISTORY_MAX_MESSAGES} messages, trimming...")
                self.history[user_id] = self.history[user_id][-config.HISTORY_MAX_MESSAGES:]
            
            # Save to file
            with open(history_path, 'w') as f:
                json.dump(self.history[user_id], f)
            
            # Check file size and trim if needed
            file_size = os.path.getsize(history_path)
            if file_size > config.HISTORY_MAX_BYTES:
                logger.warning(f"History file for {user_id} is {file_size} bytes, trimming...")
                # Remove oldest 20% of messages
                trim_count = len(self.history[user_id]) // 5
                self.history[user_id] = self.history[user_id][trim_count:]
                with open(history_path, 'w') as f:
                    json.dump(self.history[user_id], f)
                logger.info(f"Trimmed {trim_count} messages from history")
                
        except Exception as e:
            logger.error(f"Failed to save history for {user_id}: {e}")
    
    def clear_history(self, user_id):
        """
        Clear conversation history for a user.
        
        Args:
            user_id: Unique identifier for the user
        """
        self.history[user_id] = []
        self.save_history(user_id)
        logger.info(f"Cleared history for {user_id}")
    
    def _format_dual_metadata(self, local_metadata, remote_metadata):
        """
        Format dual metadata with clear labels for AI context.
        
        Args:
            local_metadata: Bot's own status metadata
            remote_metadata: User's environmental metadata
            
        Returns:
            str: Combined metadata string or None
        """
        parts = []
        if remote_metadata:
            parts.append(f"[User: {remote_metadata}]")
        if local_metadata:
            # Try to get bot's own name for a descriptive label
            name = "Bot"
            try:
                my_info = self.meshtastic.get_node_info()
                if my_info:
                    name = my_info.get('user', {}).get('longName') or my_info.get('user', {}).get('shortName') or "Bot"
            except Exception:
                pass
            parts.append(f"[{name}: {local_metadata}]")
        
        return " ".join(parts) if parts else None
    
    def add_to_history(self, history_key, role, content, node_id=None, metadata=None):
        """
        Add a message to conversation history with optional metadata.
        
        Args:
            history_key: Unique identifier for the history (from _get_history_key)
            role: 'user' or 'assistant'
            content: Message content
            node_id: Optional Node ID for labeling 'user' messages
            metadata: Optional metadata string (e.g., location/battery)
        """
        if history_key not in self.history:
            self.load_history(history_key)
        
        formatted_content = content
        if role == 'user' and node_id:
            # Tag with Node ID
            label = f"[{node_id}]"
            if metadata:
                label += f" {metadata}"
            formatted_content = f"{label}: {content}"
            
        self.history[history_key].append({'role': role, 'content': formatted_content})
        self.save_history(history_key)
    
    # ==================== Memory Status ====================
    
    def get_memory_status(self, user_id, channel=0, is_dm=True):
        """
        Get memory and conversation status for a user.
        
        Args:
            user_id: Unique identifier for the user
            channel: Current radio channel
            is_dm: Whether in DM context
            
        Returns:
            str: Formatted status message
        """
        # Resolve the active history key to match what AI queries use
        history_key = self._get_history_key(user_id, channel, is_dm)
        
        # 1. Disk/Session Stats
        active_buffer = self.history.get(history_key, [])
        message_count = len(active_buffer)
        history_path = self._get_history_path(history_key)
        
        if os.path.exists(history_path):
            history_size = os.path.getsize(history_path)
            size_kb = history_size / 1024
            max_kb = config.HISTORY_MAX_BYTES / 1024
        else:
            size_kb = 0
            max_kb = config.HISTORY_MAX_BYTES / 1024
        
        metadata = self.conversation_manager._load_metadata(user_id)
        user_conversations = [name for name in metadata if not name.startswith('channel_')]
        slot_usage = len(user_conversations)
        
        # 2. Semantic Indexing Stats
        semantic_status = "Disabled"
        if self.mcp_client and self.mcp_client.has_server('mempalace'):
            # Basic stats from our indexing attempt cache
            active_hubs = len(self._mcp_indexing_cache)
            
            # Check disk size of MemPalace data directory
            mcp_data_path = "/root/.local/share/mcp-memory"
            sem_size_kb = 0
            if os.path.exists(mcp_data_path):
                total_size = 0
                for dirpath, _, filenames in os.walk(mcp_data_path):
                    for f in filenames:
                        total_size += os.path.getsize(os.path.join(dirpath, f))
                sem_size_kb = total_size / 1024
                
            semantic_status = f"Active ({active_hubs} hubs, {sem_size_kb:.1f}KB)"

        # 3. Provider Info
        provider = self.config.get('current_provider', 'ollama')
        
        # Format status message
        from config import MAX_CONVERSATIONS
        status = (
            f"💾 Memory Status\n"
            f"Local History: {message_count}/{config.HISTORY_MAX_MESSAGES} msgs\n"
            f"Disk Usage: {size_kb:.1f}KB/{max_kb:.0f}KB\n"
            f"Conv Slots: {slot_usage}/{MAX_CONVERSATIONS}\n"
            f"Semantic DB: {semantic_status}\n"
            f"AI Provider: {provider.upper()}"
        )
        
        return status
    
    # ==================== Admin & Permission Management ====================
    
    def is_admin(self, node_id):
        """
        Check if a node ID is an admin.
        
        Args:
            node_id: Node ID to check
            
        Returns:
            bool: True if node is admin
        """
        return node_id in self.config.get('admin_nodes', [])
    
    def is_channel_allowed(self, channel_index):
        """
        Check if a channel is allowed for AI responses.
        
        Args:
            channel_index: Meshtastic channel index
            
        Returns:
            bool: True if channel is allowed
        """
        return channel_index in self.config.get('allowed_channels', [0])
    
    # ==================== AI Provider Interface ====================
    
    def get_ai_response(self, prompt, history_key, is_session=False, location=None, tools=None):
        """
        Get AI response using the configured provider with tuned context.
        
        Args:
            prompt: User's input text
            history_key: Key for history context
            is_session: Whether this is an active continuous session
            location: Optional location dict {'latitude': float, 'longitude': float}
            
        Returns:
            str: AI response or error message
        """
        provider_name = self.config.get('current_provider', 'ollama')
        
        try:
            # Get provider instance
            provider = get_provider(provider_name, self.config)
            
            # Fetch dynamic tools from MCP client
            tools = None
            if provider.supports_tools:
                tools = self.mcp_client.get_all_tools()
            
            # Get history for context
            history = None
            if history_key and history_key in self.history:
                # Context tuning — three tiers:
                #
                # 1. Channel / quick query  → last 2 messages only (no session state needed)
                # 2. Session, MemPalace ON  → short bootstrap window so the AI doesn't get the
                #    full raw log AND MemPalace semantic recall simultaneously.
                #    Frontier models get a larger window than local Ollama models.
                # 3. Session, no MemPalace  → full window (30 messages) — disk history is the
                #    only long-term memory so we send as much as we safely can.
                if not is_session:
                    limit = 2
                elif self.mcp_client.has_server('mempalace'):
                    # Provider-aware bootstrap cap
                    is_local = provider_name == 'ollama'
                    limit = (config.MEMPALACE_BOOTSTRAP_LOCAL if is_local
                             else config.MEMPALACE_BOOTSTRAP_ONLINE)
                    logger.debug(f"MemPalace active → bootstrap history limit={limit} "
                                 f"({'local' if is_local else 'online'} provider)")
                else:
                    limit = 30
                history = self.history[history_key][-limit:]
            
            # Get response
            response = provider.get_response(
                prompt, history, context_id=history_key, location=location, 
                tools=tools, mcp_client=self.mcp_client
            )
            return response
            
        except ValueError as e:
            logger.error(f"Provider error: {e}")
            return f"Error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error getting AI response: {e}")
            return f"Error: {str(e)}"
    
    # ==================== Background Semantic Indexing ====================
    
    def _index_to_mcp(self, data_type, payload):
        """Dispatch indexing task to the background executor."""
        # Only proceed if MemPalace is connected
        if not self.mcp_client or not self.mcp_client.has_server('mempalace'):
            return
        
        self._bg_executor.submit(self._index_task_wrapper, data_type, payload)

    def _index_task_wrapper(self, data_type, payload):
        """Internal wrapper for the background executor to handle Knowledge Graph logic."""
        # 1. Graceful Fallback check
        if not self.mcp_client.has_server('mempalace'):
            # Silently skip if the service is down; this is expected in some environments
            # or during service restarts.
            return

        try:
            if data_type == 'conversation':
                self._bg_index_conversation(payload)
            elif data_type == 'telemetry':
                self._bg_index_telemetry(payload)
            elif data_type == 'delete_history':
                self._bg_delete_semantic_history(payload)
        except Exception as e:
            logger.warning(f"⚠️ Background indexing failed ({data_type}). MemPalace service may be unreachable: {e}")

    def _get_semantic_hub_name(self, node_id, channel=0, is_dm=False):
        """Standardized naming logic for Semantic Hubs."""
        if is_dm:
            return f"Hub_Default_{node_id}"
        else:
            return f"Hub_CH{channel}"

    def _bg_delete_semantic_history(self, payload):
        """Perform a Nuclear Wipe of semantic memory for a context."""
        node_id = payload.get('node_id')
        topic = payload.get('topic')
        channel = payload.get('channel', 0)
        
        if not node_id:
            return
        
        targets = []
        if topic == 'all':
            # 1. Target the persistent conversation hubs
            targets.append(self._get_semantic_hub_name(node_id, channel, is_dm=True)) # Use DM hub as default user hub
            targets.append(self._get_semantic_hub_name(node_id, channel, is_dm=False)) # Use Channel hub
        elif topic:
            targets.append(topic)
        
        if targets:
            logger.info(f"🗑️ Semantically deleting indexed hubs: {targets}")
            # standard mempalace/mcp-memory uses 'names' for delete_entities
            self.mcp_client.call_tool("delete_entities", {"names": targets})

    def _bg_index_conversation(self, payload):
        """Index a conversation turn into the Knowledge Graph."""
        node_id = payload.get('node_id')
        channel = payload.get('channel', 0)
        prompt = payload.get('prompt')
        response = payload.get('response')
        is_system = payload.get('is_system', False)
        
        if not node_id or not prompt or not response:
            return
        
        # 1. Active Session vs Default Hub
        session_name = self.session_manager.get_session_name(node_id)
        
        # Override: System actions ALWAYS go to the private default hub to maintain 
        # cross-session continuity and privacy (per user agreement).
        if is_system or not session_name:
            hub_name = f"Hub_Default_{node_id}"
            description = f"General discussion and system activity hub for node {node_id}"
        else:
            hub_name = f"Chat_{node_id}_CH{channel}"
            description = f"Active chat hub for node {node_id} on channel {channel}"

        # 2. Identity Hub Ensure
        self.mcp_client.call_tool("create_entities", {
            "entities": [{
                "name": hub_name,
                "entityType": "Conversation",
                "observations": [description]
            }]
        })
        
        # 3. Topic Hub (if in session)
        if session_name:
            self.mcp_client.call_tool("create_entities", {
                "entities": [{
                    "name": session_name,
                    "entityType": "Topic",
                    "observations": [f"Session Topic: {session_name}"]
                }]
            })
            # Relate Chat Hub to Topic
            self.mcp_client.call_tool("create_relations", {
                "relations": [{
                    "from": hub_name,
                    "to": session_name,
                    "relationType": "IS_ABOUT"
                }]
            })
            
        # 4. Add Activity Observation
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if is_system:
            # For system triggers, 'prompt' is the context_note which might be instructions.
            # We clean it up for indexing.
            trigger_context = prompt.split("COMMAND/CONTEXT:")[1].split("CRITICAL INSTRUCTIONS:")[0].strip() if "COMMAND/CONTEXT:" in prompt else prompt
            observation = f"[{ts}] [SYSTEM ACTION] Task: {trigger_context} | Result: {response}"
        else:
            observation = f"[{ts}] User: {prompt} | AI: {response}"
        self.mcp_client.call_tool("add_observations", {
            "observations": [{
                "entityName": hub_name,
                "contents": [observation]
            }]
        })
        
        logger.info(f"🧠 Semantically indexed conversation turn for {node_id} -> {hub_name}")

    def _bg_index_telemetry(self, payload):
        """Index telemetry status into the Node's hub."""
        node_id = payload.get('node_id')
        t_type = payload.get('type')
        data = payload.get('data')
        
        if not node_id or not data:
            return
        
        # Throttle: Only index telemetry every 15 minutes per node to avoid bloat
        now = time.time()
        cache_key = f"{node_id}_{t_type}"
        last_time = self._mcp_indexing_cache.get(cache_key, 0)
        if now - last_time < 900: # 15 minutes
            return
            
        self._mcp_indexing_cache[cache_key] = now
        
        # Index to Node Hub
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if isinstance(data, dict):
            summary = ", ".join([f"{k}: {v}" for k, v in data.items() if v is not None])
        else:
            summary = str(data)
        obs = f"[{ts}] Telemetry ({t_type}): {summary}"
        
        self.mcp_client.call_tool("create_entities", {
            "entities": [{
                "name": node_id,
                "entityType": "MeshNode",
                "observations": [f"Mesh node hardware identity: {node_id}"]
            }]
        })
        
        self.mcp_client.call_tool("add_observations", {
            "observations": [{
                "entityName": node_id,
                "contents": [obs]
            }]
        })
        logger.info(f"🧠 Semantically indexed telemetry for {node_id}")

    # ==================== Message Sending ====================
    
    def send_response(self, text, from_node, to_node, channel, is_admin_cmd=False, use_session_indicator=False):
        """
        Send a response message via Meshtastic.
        
        Handles routing logic for admin commands and regular responses.
        
        Args:
            text: Response text to send
            from_node: Source node ID
            to_node: Destination node ID (or '^all' for broadcast)
            channel: Channel index
            is_admin_cmd: Whether this is an admin command response
            use_session_indicator: Whether to include the [🟢 session_name] prefix
        """
        # Determine destination
        if is_admin_cmd:
            # Admin commands always reply privately
            destination = from_node
        elif to_node == '^all':
            # Public message - reply publicly only if channel is enabled
            if self.is_channel_allowed(channel):
                destination = '^all'
                use_session_indicator = False # Force off for channel messages
            else:
                # Channel not enabled, don't respond
                logger.info(f"Skipping response on disabled channel {channel}")
                return
        else:
            # DM - reply privately
            destination = from_node
        
        # Get session indicator if applicable
        session_indicator = ""
        if use_session_indicator:
            session_indicator = self.session_manager.get_session_indicator(from_node)
        
        # Send via Meshtastic handler
        self.meshtastic.send_message(text, destination, channel, session_indicator)
    
    # ==================== Command Processing ====================
    
    def process_command(self, text, from_node, to_node, channel):
        """
        Process AI commands and queries.
        
        This is the main command router that handles all !ai commands and
        delegates to appropriate handlers.
        
        Args:
            text: Command text
            from_node: Source node ID
            to_node: Destination node ID
            channel: Channel index
        """
        # Track node for telemetry logging of active users
        try:
            self.meshtastic.track_node(from_node)
        except Exception:
            pass
            
        # Extract command and arguments
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            return
        
        cmd = parts[1].lower() if len(parts) > 1 else ''
        args = parts[2] if len(parts) > 2 else ''
        
        is_dm = (to_node != '^all' and (channel == 0 or to_node.startswith('!')))
        is_admin = self.is_admin(from_node)
        
        # ===== Help Command =====
        if cmd == '-h':
            self._handle_help_command(from_node, to_node, channel, is_dm, is_admin)
            return
        
        # ===== Memory Status =====
        elif cmd == '-m':
            status = self.get_memory_status(from_node, channel, is_dm)
            self.send_response(status, from_node, to_node, channel, is_admin_cmd=False)
            return
        
        # ===== Session Commands (DM only) =====
        if cmd == '-n':
            # Check for explicit wipe command: !ai -n rm all
            if args.lower() == 'rm all':
                # 1. Clear local in-memory history buffer
                key = f"DM:{from_node}" if is_dm else f"Channel:{channel}:{from_node}"
                self.clear_history(key)
                
                # 2. Disk & Semantic Wipe
                if is_dm:
                    # Wipe all disk-based sessions for this user
                    self.conversation_manager.delete_all_conversations(from_node)
                    # Semantic: Wipe everything (Topic='all') for this node's hubs
                    self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': 'all', 'channel': 0})
                    self.send_response("☢️ Nuclear Wipe: All DM sessions and Graph memory forgotten.", from_node, to_node, channel)
                else:
                    # Wipe channel graph memory
                    self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': 'all', 'channel': channel})
                    self.send_response(f"☢️ Nuclear Wipe: All history for Channel {channel} index forgotten in Graph.", from_node, to_node, channel)
                return

            if is_dm:
                # 1. If in a session, end it first (don't clear its history)
                if self.session_manager.is_active(from_node):
                    self.session_manager.end_session(from_node)
                
                if args:
                    # 2. Start NEW named session (Pivot)
                    success, message, conv_name = self.session_manager.start_session(from_node, args, channel, to_node)
                    self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                else:
                    # 3. No args: Safe Context Reset (Non-destructive)
                    dm_key = f"DM:{from_node}"
                    self.clear_history(dm_key)
                    self.send_response("✨ DM context reset to Default. (Memories safely archived in Graph)", from_node, to_node, channel, is_admin_cmd=False)
            else:
                # Channel mode: Safe Context Reset (Non-destructive)
                channel_key = f"Channel:{channel}:{from_node}"
                self.clear_history(channel_key)
                if args:
                    # Treat args as a fresh query after reset
                    self._handle_ai_query(args, from_node, to_node, channel, "Thinking (New Conversation)... 🤖")
                else:
                    self.send_response(f"✨ Channel {channel} window reset. (History archived in Graph)", from_node, to_node, channel, is_admin_cmd=False)
            return
        
        if cmd == '-end':
            if is_dm:
                success, message, _, _ = self.session_manager.end_session(from_node)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            else:
                self.send_response("⚠️ Sessions are DM-only. Use !ai -n in channels to clear history.", 
                                 from_node, to_node, channel, is_admin_cmd=False)
            return
        
        # ===== Conversation Management =====
        if cmd == '-c':
            self._handle_conversation_command(args, from_node, to_node, channel)
            return
        
        # ===== Admin Commands (DM only) =====
        admin_only_commands = ['-p', '-ch', '-a', '-s']
        if cmd in admin_only_commands:
            if not is_admin:
                self.send_response("⛔ Unauthorized: Admin only.", from_node, to_node, channel, is_admin_cmd=True)
                return
            
            if not is_dm:
                self.send_response("⚙️ Admin commands are DM only. Please send this command in a direct message.", 
                                 from_node, to_node, channel, is_admin_cmd=True)
                return
            
            # Route to appropriate admin handler
            if cmd == '-p':
                self._handle_provider_command(args, from_node, to_node, channel)
            elif cmd == '-ch':
                self._handle_channel_command(args, from_node, to_node, channel)
            elif cmd == '-a':
                self._handle_admin_command(args, from_node, to_node, channel)
            elif cmd == '-s':
                self._handle_scheduler_command(args, from_node, to_node, channel)
            return
        
        # ===== Default: AI Query =====
        # If no command matched, treat the entire text (minus !ai) as a query
        query = ' '.join(parts[1:]) if len(parts) > 1 else ''
        if query:
            self._handle_ai_query(query, from_node, to_node, channel)
    
    def _handle_help_command(self, from_node, to_node, channel, is_dm, is_admin):
        """Send expanded categorized multi-message help."""
        # 1. Basic Commands
        if is_dm:
            msg1 = (
                "🤖 AI Basic Commands\n"
                "!ai [msg] : Ask AI (No prefix in session)\n"
                "!ai -h : Show this help\n"
                "!ai -m : Memory/context status\n"
                "!ai -n : Safely reset context"
            )
        else:
            msg1 = (
                "🤖 AI Basic Commands\n"
                "!ai [msg] : Ask AI (prefix required)\n"
                "!ai -h : Show this help\n"
                "!ai -m : Memory/context status\n"
                "!ai -n : Safely reset context"
            )
        self.send_response(msg1, from_node, to_node, channel, is_admin_cmd=False)
        
        # 2. Session Management (DM only)
        if is_dm:
            msg2 = (
                "👤 Session Management\n"
                "!ai -n [name] : New named session\n"
                "!ai -n rm all : Nuclear Wipe\n"
                "!ai -c ls : List saved convos\n"
                "!ai -c [id] : Load convo #id\n"
                "!ai -c rm [id] : Delete convo\n"
                "!ai -end : Close session"
            )
            self.send_response(msg2, from_node, to_node, channel, is_admin_cmd=False)
        
        # 3. Actions & Tasks
        msg3 = (
            "📡 Proactive Tasks & Alerts\n"
            "!ai -s : List tasks\n"
            "!ai cancel [id] : Stop alert"
        )
        self.send_response(msg3, from_node, to_node, channel, is_admin_cmd=False)
        
        # 4. Practical Examples
        msg4 = (
            "💡 Examples\n"
            "- \"!ai Ping SNR/count every 15s\"\n"
            "- \"!ai Watch XYZ temp; alert if >35\"\n"
            "- \"!ai Remind me at 10pm to swap\"\n"
            "- \"!ai Tell Node X I am coming\""
        )
        self.send_response(msg4, from_node, to_node, channel, is_admin_cmd=False)
        
        # 5. Admin Tools (Admin + DM only)
        if is_admin and is_dm:
            msg5 = (
                "⚙️ Admin Tools\n"
                "!ai -p  [ollama|gemini] : Switch AI\n"
                "!ai -ch [ls|add 1|rm 1] : Channels\n"
                "!ai -a  [ls|add !id|rm !id] : Admins\n"
                "!ai -s  [ls|rm id|rm all] : Proactive tasks"
            )
            self.send_response(msg5, from_node, to_node, channel, is_admin_cmd=False)
    
    def _handle_conversation_command(self, args, from_node, to_node, channel):
        """Handle conversation management commands with Semantic Memory integration."""
        is_dm = (to_node != '^all' and (channel == 0 or to_node.startswith('!')))
        
        if not args:
            # Load last conversation
            metadata = self.conversation_manager._load_metadata(from_node)
            if metadata:
                latest = max(metadata.items(), key=lambda x: x[1]['last_access'])
                self._load_and_respond(latest[0], from_node, to_node, channel, is_dm)
            else:
                self.send_response("No saved conversations found.", from_node, to_node, channel)
            return
        
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower()
        
        if subcmd == 'ls':
            # 1. Get Disk listing
            listing = self.conversation_manager.list_conversations(from_node)
            
            # 2. Add Semantic clues if enabled
            if self.mcp_client and self.mcp_client.has_server('mempalace'):
                listing += "\n(MemPalace Active: Deep Archive enabled)"
            
            self.send_response(listing, from_node, to_node, channel)
        
        elif subcmd == 'rm':
            if len(parts) < 2:
                self.send_response("Usage: !ai -c rm [id/all]", from_node, to_node, channel)
                return
            identifier = parts[1]
            
            if identifier.lower() == 'all':
                success, message = self.conversation_manager.delete_all_conversations(from_node)
                # Semantic Wipe
                self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': 'all', 'channel': channel})
                self.send_response(f"{message} (Graph pruned)", from_node, to_node, channel)
                self.session_manager.end_session(from_node)
                
                # Clear correct active history key
                key = self._get_history_key(from_node, channel, is_dm)
                self.clear_history(key)
            else:
                # Sync delete - find name first
                name = self._resolve_conversation_name(from_node, identifier)
                success, message = self.conversation_manager.delete_conversation(from_node, identifier)
                if name:
                    self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': name, 'channel': channel})
                self.send_response(f"{message} (Graph synced)", from_node, to_node, channel)
        
        else:
            # Load specific conversation
            self._load_and_respond(args, from_node, to_node, channel, is_dm)

    def _resolve_conversation_name(self, node_id, identifier):
        """Resolve a slot ID or prefix to a full conversation name."""
        metadata = self.conversation_manager._load_metadata(node_id)
        if identifier.isdigit():
            target = int(identifier)
            for name, data in metadata.items():
                if data['index'] == target:
                    return name
        elif identifier in metadata:
            return identifier
        return None

    def _load_and_respond(self, identifier, from_node, to_node, channel, is_dm):
        """Helper to load a session (Disk with Graph fallback) and notify user."""
        success, message, history, conversation_name = self.conversation_manager.load_conversation(from_node, identifier)
        
        # Semantic Re-hydration Fallback
        if not success and self.mcp_client and self.mcp_client.has_server('mempalace'):
            # Try to load topic name directly from graph if identifier is a name
            rehydrated = self._rehydrate_session_from_graph(from_node, identifier)
            if rehydrated:
                history = rehydrated
                conversation_name = identifier
                success = True
                message = f"💧 Re-hydrated '{identifier}' from Semantic Graph."

        if success and history:
            self.history[conversation_name] = history
            self._refresh_metadata_nodes.add(from_node)
            if is_dm:
                self.session_manager.start_session(from_node, conversation_name, channel, to_node)
                message += "\n🟢 Session Resumed"
            self.send_response(message, from_node, to_node, channel)
        else:
            self.send_response(message, from_node, to_node, channel)

    def _rehydrate_session_from_graph(self, node_id, topic_name):
        """Attempt to reconstruct a session's history from MemPalace observations."""
        try:
            # Note: This is a synchronous call to the re-hydration tool
            # In a real graph, we'd search for the Topic hub and get its turns
            # For now, we search for the topic name to see if it's there
            self.mcp_client.call_tool("read_graph", {}) # Fetch whole graph to filter locally for now
            # Actually, read_graph without args might be too heavy. 
            # We'll use a specific search if the tool supports it.
            return None # Implementation of specific pattern matching TBD based on final graph schema
        except Exception:
            return None
    
    def _handle_provider_command(self, args, from_node, to_node, channel):
        """Handle AI provider switching."""
        if not args or args.strip().lower() == 'ls':
            # List providers
            current = self.config.get('current_provider', 'ollama')
            providers_status = []
            for p in ['ollama', 'gemini', 'openai', 'anthropic']:
                marker = "✅" if p == current else "❌"
                providers_status.append(f"{marker} {p}")
            
            message = "🤖 AI Providers:\n" + "\n".join(providers_status)
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return
        
        # Switch provider
        provider = args.lower()
        valid_providers = ['ollama', 'local', 'gemini', 'openai', 'anthropic']
        
        if provider not in valid_providers:
            self.send_response(f"Invalid provider. Choose: {', '.join(valid_providers)}", 
                             from_node, to_node, channel, is_admin_cmd=True)
            return
        
        # Normalize 'local' to 'ollama'
        if provider == 'local':
            provider = 'ollama'
        
        self.config['current_provider'] = provider
        self.config.save()
        
        provider_labels = {
            'ollama': 'LOCAL',
            'gemini': 'ONLINE',
            'openai': 'ONLINE',
            'anthropic': 'ONLINE'
        }
        label = provider_labels.get(provider, provider.upper())
        self.send_response(f"✅ Switched to {label} ({provider})", from_node, to_node, channel, is_admin_cmd=True)
    
    def _handle_channel_command(self, args, from_node, to_node, channel):
        """Handle channel management."""
        parts = args.split(maxsplit=1) if args else []
        action = parts[0].lower() if parts else ""
        
        if not action or action == 'ls':
            # List channels
            allowed = self.config.get('allowed_channels', [0])
            available_channels = self.meshtastic.get_channels()
            
            if not available_channels:
                message = f"📡 Allowed Channels: {', '.join(map(str, allowed))}\n(Could not retrieve available channels from node)"
            else:
                lines = ["📡 Channels:"]
                for ch in available_channels:
                    idx = ch['index']
                    name = ch['name']
                    if not name:
                        name = "Unnamed"
                    status = "✅" if idx in allowed else "❌"
                    lines.append(f"{status} [{idx}] {name}")
                message = "\n".join(lines)
            
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return
        
        if len(parts) < 2 or action not in ['add', 'rm']:
            self.send_response("Usage: !ai -ch [ls|add <id>|rm <id>]", from_node, to_node, channel, is_admin_cmd=True)
            return
        
        channel_id_str = parts[1]
        
        if not channel_id_str.isdigit():
            self.send_response("Channel ID must be a number", from_node, to_node, channel, is_admin_cmd=True)
            return
        
        channel_id = int(channel_id_str)
        allowed_channels = self.config.get('allowed_channels', [0])
        
        if action == 'add':
            if channel_id not in allowed_channels:
                allowed_channels.append(channel_id)
                self.config['allowed_channels'] = allowed_channels
                self.config.save()
                self.send_response(f"✅ Added channel {channel_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"Channel {channel_id} already allowed", from_node, to_node, channel, is_admin_cmd=True)
        
        elif action == 'rm':
            if channel_id in allowed_channels:
                allowed_channels.remove(channel_id)
                self.config['allowed_channels'] = allowed_channels
                self.config.save()
                self.send_response(f"✅ Removed channel {channel_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"Channel {channel_id} not in allowed list", from_node, to_node, channel, is_admin_cmd=True)
        
        else:
            self.send_response("Usage: !ai -ch add/rm <channel_id>", from_node, to_node, channel, is_admin_cmd=True)
    
    def _handle_admin_command(self, args, from_node, to_node, channel):
        """Handle admin node management."""
        if not args or args.strip().lower() == 'ls':
            # List admins
            admins = self.config.get('admin_nodes', [])
            if admins:
                message = "👑 Admin Nodes:\n" + "\n".join(admins)
            else:
                message = "No admin nodes configured"
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return
        
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            self.send_response("Usage: !ai -a [ls|add <id>|rm <id>]", from_node, to_node, channel, is_admin_cmd=True)
            return
        
        action = parts[0].lower()
        node_input = parts[1]
        
        # Handle comma-separated list of nodes
        nodes_to_process = [n.strip() for n in node_input.split(',') if n.strip()]
        if not nodes_to_process:
            self.send_response("No valid node IDs provided.", from_node, to_node, channel, is_admin_cmd=True)
            return

        admin_nodes = self.config.get('admin_nodes', [])
        messages = []
        updated = False
        
        if action == 'add':
            for nid in nodes_to_process:
                if nid not in admin_nodes:
                    admin_nodes.append(nid)
                    messages.append(f"✅ Added {nid}")
                    updated = True
                else:
                    messages.append(f"ℹ️ {nid} is already admin")
            
            if updated:
                self.config['admin_nodes'] = admin_nodes
                self.config.save()
            
            self.send_response("\n".join(messages), from_node, to_node, channel, is_admin_cmd=True)
        
        elif action == 'rm':
            for nid in nodes_to_process:
                if nid in admin_nodes:
                    admin_nodes.remove(nid)
                    messages.append(f"✅ Removed {nid}")
                    updated = True
                else:
                    messages.append(f"ℹ️ {nid} not in admin list")
            
            if updated:
                self.config['admin_nodes'] = admin_nodes
                self.config.save()
            
            self.send_response("\n".join(messages), from_node, to_node, channel, is_admin_cmd=True)
        
        else:
            self.send_response("Usage: !ai -a add/rm <node_id>", from_node, to_node, channel, is_admin_cmd=True)

    def _handle_scheduler_command(self, args, from_node, to_node, channel):
        """Handle !ai -s admin command for managing proactive tasks.

        Sub-commands:
            (no args)          List ALL active tasks across all users
            rm <id>            Remove a specific task by ID
            rm all             Remove all tasks system-wide
            add                Print usage hint for adding tasks via AI
        """
        sub_parts = args.split(maxsplit=1) if args else []
        sub = sub_parts[0].lower() if sub_parts else ''
        sub_arg = sub_parts[1] if len(sub_parts) > 1 else ''

        # ── LIST ───────────────────────────────────────────────────────────
        if not sub or sub == 'ls' or sub == 'list':
            lines = []
            now = time.time()

            with self._scheduled_tasks_lock:
                for t in self.scheduled_tasks:
                    remaining = max(0, int(t['next_time'] - now))
                    recur = f" (every {t['interval']}s)" if t.get('interval') else ""
                    lines.append(f"[{t['id']}] ⏰ {t['from_node']} → {t['context_note']} in {remaining}s{recur}")

            with self._condition_watchers_lock:
                for w in self.condition_watchers:
                    lines.append(f"[{w['id']}] 👁 {w['from_node']} → {w['node_id']} {w['metric']}{w['operator']}{w['threshold']}")

            with self._node_online_watchers_lock:
                for w in self.node_online_watchers:
                    lines.append(f"[{w['id']}] 🟢 {w['from_node']} → waiting for {w['node_id']}")

            if not lines:
                self.send_response("📋 No active proactive tasks.", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response("📋 Active tasks:\n" + "\n".join(lines), from_node, to_node, channel, is_admin_cmd=True)
            return

        # ── REMOVE ─────────────────────────────────────────────────────────
        if sub == 'rm':
            if not sub_arg:
                self.send_response("Usage: !ai -s rm <id/all>", from_node, to_node, channel, is_admin_cmd=True)
                return

            cancel_all = (sub_arg.strip().lower() == 'all')
            task_id = sub_arg.strip()
            cancelled = []

            with self._scheduled_tasks_lock:
                keep, remove = [], []
                for t in self.scheduled_tasks:
                    if cancel_all or t.get('id') == task_id:
                        remove.append(t.get('id'))
                    else:
                        keep.append(t)
                self.scheduled_tasks = keep
                cancelled.extend(remove)

            with self._condition_watchers_lock:
                keep, remove = [], []
                for w in self.condition_watchers:
                    if cancel_all or w.get('id') == task_id:
                        remove.append(w.get('id'))
                    else:
                        keep.append(w)
                self.condition_watchers = keep
                cancelled.extend(remove)

            with self._node_online_watchers_lock:
                keep, remove = [], []
                for w in self.node_online_watchers:
                    if cancel_all or w.get('id') == task_id:
                        remove.append(w.get('id'))
                    else:
                        keep.append(w)
                self.node_online_watchers = keep
                cancelled.extend(remove)

            if cancelled:
                self._save_proactive_tasks()
                ids = ', '.join(f'[{c}]' for c in cancelled)
                self.send_response(f"✅ Removed: {ids}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"❌ Task [{task_id}] not found.", from_node, to_node, channel, is_admin_cmd=True)
            return

        # ── ADD (usage hint) ────────────────────────────────────────────────
        if sub == 'add':
            hint = (
                "To add tasks, use natural language:\n"
                "• !ai Remind me in 5m → sched timer\n"
                "• !ai Ping me every 30s for 5m\n"
                "• !ai Alert me when L4B1 battery <10%\n"
                "• !ai Notify me when L4B1 comes online"
            )
            self.send_response(hint, from_node, to_node, channel, is_admin_cmd=True)
            return

        # ── FALLBACK ────────────────────────────────────────────────────────
        usage = "Usage: !ai -s [rm <id/all>]"
        self.send_response(usage, from_node, to_node, channel, is_admin_cmd=True)

    def _handle_ai_query(self, query, from_node, to_node, channel, is_dm=None, initial_msg="Thinking... 🤖"):
        """
        Handle an AI query in a background thread.
        
        Args:
            query: User's question/prompt
            from_node: Source node ID
            to_node: Destination node ID
            channel: Channel index
            is_dm: Optional DM status (if already determined)
            initial_msg: Initial "thinking" message to send
        """
        # Determine if this is a DM interaction if not provided
        if is_dm is None:
            my_node_info = self.meshtastic.get_node_info()
            my_id = my_node_info.get('user', {}).get('id', '') if my_node_info else ''
            is_dm = (to_node == my_id)
        
        # Send initial acknowledgment
        if initial_msg:
            self.send_response(initial_msg, from_node, to_node, channel, is_admin_cmd=False)
        
        # Process in background thread
        thread = threading.Thread(
            target=self._process_ai_query_thread,
            args=(query, from_node, to_node, channel, is_dm)
        )
        thread.daemon = True
        thread.start()
    
    def _touch_worker(self):
        """Update the active worker timestamp to prevent false-positive hang detection during long tools."""
        thread_id = threading.get_ident()
        with self._workers_lock:
            if thread_id in self._active_workers:
                self._active_workers[thread_id]['start_time'] = time.time()

    def _get_location_address_mcp(self, lat, lon):
        """Tool to reverse geocode lat/lon to a physical address using OpenStreetMap"""
        logger.info(f"📍 Reverse geocoding requested for {lat}, {lon}")
        url = "https://nominatim.openstreetmap.org/reverse"
        
        # Nominatim requires a user-agent
        headers = {
            "User-Agent": "AI-Responder-Meshtastic/1.5 (https://github.com/LN4CY/ai-responder)"
        }
        
        params = {
            "format": "json",
            "lat": lat,
            "lon": lon,
            "zoom": 18,
            "addressdetails": 1
        }
        
        try:
            self._touch_worker()
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "display_name" in data:
                    return f"Address found: {data['display_name']}"
                else:
                    return "No address found for these coordinates."
            else:
                return f"Error geocoding: HTTP {response.status_code}"
        except Exception as e:
            logger.error(f"Geocoding error: {e}")
            return f"Error contacting geocoding service: {e}"

    def _get_node_details_tool(self, node_id_or_name):
        """Internal handler for get_node_details tool."""
        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found."
        
        metadata = self.meshtastic.get_node_metadata(node_id)
        if not metadata:
            return f"Error: No information available for {node_id}."
        return metadata

    def _request_node_telemetry_mcp(self, node_id_or_name, telemetry_type, from_node=None, to_node=None, channel=0):
        """Internal handler for request_node_telemetry tool with short polling."""
        self._touch_worker()
        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found."
                
        node_id = node_id.lower()

        # 1. Map type to internal metric key
        type_map = {
            'device': 'device_metrics',
            'environment': 'environment_metrics',
            'local_stats': 'local_stats',
            'air_quality': 'air_quality_metrics',
            'power': 'power_metrics',
            'health': 'health_metrics',
            'host': 'host_metrics'
        }
        metric_key = type_map.get(telemetry_type, 'environment_metrics')

        self.pending_telemetry_requests[node_id] = {
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'context_note': f'{telemetry_type} telemetry for {node_id_or_name}',
            'registered_at': time.time()
        }
        logger.info(f"⏳ Registered deferred telemetry callback for {node_id} (type={telemetry_type})")

        # 3. Send Request
        request_time = time.time()
        logger.info(f"📡 AI triggering telemetry refresh ({telemetry_type}) for {node_id}")
        self.meshtastic.request_telemetry(node_id, telemetry_type)

        # 4. Short Poll (Wait up to 15 seconds for data to arrive in cache)
        # We check the cache frequently to minimize latency and race conditions
        poll_start = time.time()
        poll_timeout = 15 
        
        while time.time() - poll_start < poll_timeout:
            self._touch_worker()
            
            # Check timestamps in the handler
            node_timestamps = self.meshtastic.telemetry_timestamps.get(node_id.lower(), {})
            last_received = node_timestamps.get(metric_key, 0)
            
            if last_received > request_time:
                # Fresh data arrived! Remove the pending request so it isn't fired twice
                self.pending_telemetry_requests.pop(node_id, None)
                
                # ALSO clear and consume any collector events for this node to prevent redundant proactive turns
                # This is CRITICAL to avoid the "proactive turn answering first" race condition.
                aggregated_notes = ""
                with self._collector_lock:
                    collector_entry = self._proactive_event_collector.pop(node_id, None)
                    if collector_entry:
                        if collector_entry['timer']:
                            collector_entry['timer'].cancel()
                        # Prepend any alerts or notes that arrived alongside this telemetry
                        other_events = [e for e in collector_entry['events'] if "Requested" not in e]
                        if other_events:
                            aggregated_notes = "\n".join(other_events) + "\n\n"

                elapsed = int(last_received - request_time)
                logger.info(f"⚡ Fresh telemetry for {node_id} arrived in {elapsed}s and was consumed synchronously.")
                metadata = self.meshtastic.get_node_metadata(node_id)
                return f"Success! New telemetry received in {elapsed}s:\n{aggregated_notes}{metadata}"

            # Wait a short interval before next check
            time.sleep(0.5)

        # 5. Timeout fallback
        # If the pending request was consumed by _on_telemetry_proactive during our 15s wait,
        # we don't need to say "I'm still waiting." The proactive handler already sent the data.
        # Return a special sentinel that suppresses any further text generation by the AI model.
        if node_id not in self.pending_telemetry_requests:
            logger.info(f"🔇 Telemetry for {node_id} was handled by proactive callback — suppressing AI reply.")
            return "__SILENT_ACK__"
            
        return (f"Refresh request for {telemetry_type} sent to {node_id_or_name}. "
                "The mesh is slow—I'm watching for the response. I will send it as soon as the data arrives!")

    # ==================== Proactive Agent Tools & Handlers ====================

    def _schedule_mcp_task(self, delay_seconds=None, context_note=None, recur_interval_seconds=None, max_duration_seconds=None, notify_targets=None, absolute_time=None, from_node=None, to_node=None, channel=0):
        """Tool handler: schedule a one-shot or recurring proactive message."""

        # DM-only enforcement
        if to_node == '^all':
            return "⚠️ Proactive alerts can only be registered from a Direct Message to avoid spamming public channels."

        now = time.time()
        
        # Determine delay from absolute time if provided
        if absolute_time:
            try:
                # 1. Try HH:MM (today or tomorrow)
                if re.match(r"^\d{1,2}:\d{2}$", absolute_time):
                    dt_now = datetime.datetime.now()
                    target_time = datetime.datetime.strptime(absolute_time, "%H:%M")
                    # Combine today's date with target time
                    target_dt = dt_now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
                    # If target is in the past, assume tomorrow
                    if target_dt < dt_now:
                        target_dt += datetime.timedelta(days=1)
                    delay_seconds = (target_dt - dt_now).total_seconds()
                # 2. Try YYYY-MM-DD HH:MM
                elif re.match(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}$", absolute_time):
                    target_dt = datetime.datetime.strptime(absolute_time, "%Y-%m-%d %H:%M")
                    delay_seconds = (target_dt - datetime.datetime.now()).total_seconds()
                else:
                    return f"Error: Unsupported time format '{absolute_time}'. Use HH:MM or YYYY-MM-DD HH:MM."
                
                if delay_seconds < 0:
                    return f"Error: Target time '{absolute_time}' is in the past."
            except Exception as e:
                logger.error(f"Error parsing absolute time '{absolute_time}': {e}")
                return f"Error parsing time: {e}"

        if delay_seconds is None:
            return "Error: Either delay_seconds or absolute_time must be provided."

        task_id = f"sched-{next(self._task_counter)}"
        task = {
            'id': task_id,
            'next_time': now + delay_seconds,
            'end_time': now + (max_duration_seconds or delay_seconds),
            'interval': recur_interval_seconds,
            'context_note': context_note,
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'targets': notify_targets or 'requester',
        }
        with self._scheduled_tasks_lock:
            # Enforce 50 tasks per user limit
            user_tasks = [t for t in self.scheduled_tasks if t.get('from_node') == from_node]
            cond_watchers = [w for w in self.condition_watchers if w.get('from_node') == from_node]
            online_watchers = [w for w in self.node_online_watchers if w.get('from_node') == from_node]
            total_current = len(user_tasks) + len(cond_watchers) + len(online_watchers)
            
            if total_current >= config.MAX_PROACTIVE_TASKS_PER_USER:
                return f"⚠️ Limit reached: You can only have {config.MAX_PROACTIVE_TASKS_PER_USER} active proactive tasks. Please cancel an existing task first."

            self.scheduled_tasks.append(task)
            self._save_proactive_tasks()

        if recur_interval_seconds:
            return (f"✅ [{task_id}] Recurring reminder registered! I will send a message every {recur_interval_seconds}s "
                    f"for the next {max_duration_seconds or delay_seconds}s about: {context_note}")
        else:
            time_desc = f"at {absolute_time}" if absolute_time else f"in {int(delay_seconds)}s"
            return f"✅ [{task_id}] Reminder scheduled {time_desc} about: {context_note}"

    def _send_message_mcp(self, target, message):
        """Tool handler: send a one-off message to a specific node or channel."""
        logger.info(f"📤 Tool request: send_message to {target}: {message}")
        
        _to = '^all'
        _ch = 0
        
        # 1. Resolve Target
        if target.startswith('ch:'):
            try:
                _ch = int(target[3:])
                allowed_channels = self.config.get('allowed_channels', [])
                if _ch not in allowed_channels:
                    return f"Error: Channel {_ch} is not in the allowed_channels list."
            except ValueError:
                return f"Error: Invalid channel format '{target}'. Use 'ch:N'."
        else:
            # Node resolution
            node_id = target
            if not node_id.startswith('!'):
                found_id = self.meshtastic.find_node_by_name(target)
                if found_id:
                    node_id = found_id
                else:
                    return f"Error: Node '{target}' not found on the mesh."
            _to = node_id
            
        # 2. Enqueue Message
        # We use a special prefix or session indicator if needed, 
        # but for one-off tool messages, we can just send it as is.
        self.meshtastic.send_message(message, _to, _ch, "")
        
        return f"✅ Message queued for {target}."

    def _watch_condition_mcp(self, node_id_or_name, metric, operator, threshold, context_note, notify_targets=None, is_persistent=False, from_node=None, to_node=None, channel=0):
        """Tool handler: add a telemetry condition watcher."""

        # DM-only enforcement
        if to_node == '^all':
            return "⚠️ Proactive alerts can only be registered from a Direct Message to avoid spamming public channels."

        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found."
                
        node_id = node_id.lower()

        task_id = f"cond-{next(self._task_counter)}"
        watcher = {
            'id': task_id,
            'node_id': node_id,
            'metric': metric,
            'operator': operator,
            'threshold': threshold,
            'context_note': context_note,
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'targets': notify_targets or 'requester',
            'is_persistent': is_persistent,
        }
        with self._condition_watchers_lock:
            # Enforce limit
            scheduled = [t for t in self.scheduled_tasks if t.get('from_node') == from_node]
            cond_watchers = [w for w in self.condition_watchers if w.get('from_node') == from_node]
            online_watchers = [w for w in self.node_online_watchers if w.get('from_node') == from_node]
            total_current = len(scheduled) + len(cond_watchers) + len(online_watchers)
            
            if total_current >= config.MAX_PROACTIVE_TASKS_PER_USER:
                return f"⚠️ Limit reached: You can only have {config.MAX_PROACTIVE_TASKS_PER_USER} active proactive tasks."

            self.condition_watchers.append(watcher)
            self._save_proactive_tasks()

        logger.info(f"👁️ Condition watcher [{task_id}] registered: {node_id} {metric}{operator}{threshold}")
        return f"✅ [{task_id}] Watching {node_id_or_name}: will alert when {metric} {operator} {threshold}"

    def _watch_node_online_mcp(self, node_id_or_name, context_note, notify_targets=None, is_persistent=False, from_node=None, to_node=None, channel=0):
        """Tool handler: add a node-online watcher."""

        # DM-only enforcement
        if to_node == '^all':
            return "⚠️ Proactive alerts can only be registered from a Direct Message to avoid spamming public channels."

        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found. Make sure I've seen it at least once before."
                
        node_id = node_id.lower()

        task_id = f"node-{next(self._task_counter)}"
        watcher = {
            'id': task_id,
            'node_id': node_id,
            'context_note': context_note,
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'targets': notify_targets or 'requester',
            'is_persistent': is_persistent,
        }
        with self._node_online_watchers_lock:
            # Enforce limit
            scheduled = [t for t in self.scheduled_tasks if t.get('from_node') == from_node]
            cond_watchers = [w for w in self.condition_watchers if w.get('from_node') == from_node]
            online_watchers = [w for w in self.node_online_watchers if w.get('from_node') == from_node]
            total_current = len(scheduled) + len(cond_watchers) + len(online_watchers)
            
            if total_current >= config.MAX_PROACTIVE_TASKS_PER_USER:
                return f"⚠️ Limit reached: You can only have {config.MAX_PROACTIVE_TASKS_PER_USER} active proactive tasks."

            self.node_online_watchers.append(watcher)
            self._save_proactive_tasks()

        logger.info(f"👀 Node-online watcher [{task_id}] registered for {node_id}")
        return f"✅ [{task_id}] Watching for {node_id_or_name}: I'll alert you when it's heard on the mesh"

    def _list_proactive_tasks_mcp(self, from_node=None):
        """Tool handler: list all active proactive tasks for the current user."""
        caller = from_node
        lines = []
        now = time.time()

        with self._scheduled_tasks_lock:
            for t in self.scheduled_tasks:
                if t.get('from_node') != caller:
                    continue
                remaining = max(0, int(t['next_time'] - now))
                recur = f" (every {t['interval']}s)" if t.get('interval') else ""
                lines.append(f"[{t['id']}] ⏰ Fires in {remaining}s{recur}: {t['context_note']} → {t['targets']}")

        with self._condition_watchers_lock:
            for w in self.condition_watchers:
                if w.get('from_node') != caller:
                    continue
                lines.append(f"[{w['id']}] 👁 {w['node_id']} {w['metric']}{w['operator']}{w['threshold']} → {w['targets']}")

        with self._node_online_watchers_lock:
            for w in self.node_online_watchers:
                if w.get('from_node') != caller:
                    continue
                lines.append(f"[{w['id']}] 🟢 Waiting for {w['node_id']} → {w['targets']}")

        if not lines:
            return "📋 You have no active proactive tasks."
        return "📋 Your active tasks:\n" + "\n".join(lines)

    def _cancel_proactive_task_mcp(self, task_id, from_node=None):
        """Tool handler: cancel a proactive task by ID, or 'all' to cancel everything."""
        caller = from_node
        cancelled = []
        cancel_all = (task_id.strip().lower() == 'all')

        with self._scheduled_tasks_lock:
            keep, remove = [], []
            for t in self.scheduled_tasks:
                if t.get('from_node') == caller and (cancel_all or t.get('id') == task_id):
                    remove.append(t.get('id'))
                else:
                    keep.append(t)
            self.scheduled_tasks = keep
            cancelled.extend(remove)

        with self._condition_watchers_lock:
            keep, remove = [], []
            for w in self.condition_watchers:
                if w.get('from_node') == caller and (cancel_all or w.get('id') == task_id):
                    remove.append(w.get('id'))
                else:
                    keep.append(w)
            self.condition_watchers = keep
            cancelled.extend(remove)

        with self._node_online_watchers_lock:
            keep, remove = [], []
            for w in self.node_online_watchers:
                if w.get('from_node') == caller and (cancel_all or w.get('id') == task_id):
                    remove.append(w.get('id'))
                else:
                    keep.append(w)
            self.node_online_watchers = keep
            cancelled.extend(remove)

        if cancelled:
            self._save_proactive_tasks()
            ids = ', '.join(f'[{c}]' for c in cancelled)
            return f"✅ Cancelled: {ids}"
        return f"❌ Task [{task_id}] not found or does not belong to you."

    def _save_proactive_tasks(self):
        """Save all proactive tasks to a JSON file."""
        try:
            tasks_data = {
                'scheduled_tasks': self.scheduled_tasks,
                'condition_watchers': self.condition_watchers,
                'node_online_watchers': self.node_online_watchers
            }
            file_path = os.path.join(self.history_dir, config.PROACTIVE_TASKS_FILE)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(tasks_data, f, indent=2)
            logger.debug(f"💾 Saved proactive tasks to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save proactive tasks: {e}")

    def _load_proactive_tasks(self):
        """Load proactive tasks from disk and restore ID counter."""
        file_path = os.path.join(self.history_dir, config.PROACTIVE_TASKS_FILE)
        if not os.path.exists(file_path):
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tasks_data = json.load(f)
            
            self.scheduled_tasks = tasks_data.get('scheduled_tasks', [])
            self.condition_watchers = tasks_data.get('condition_watchers', [])
            self.node_online_watchers = tasks_data.get('node_online_watchers', [])
            
            # Restore ID counter to skip used IDs
            all_tasks = self.scheduled_tasks + self.condition_watchers + self.node_online_watchers
            max_id = 0
            for t in all_tasks:
                try:
                    # Parse ID like "sched-5" or "cond-12"
                    id_num = int(t['id'].split('-')[1])
                    max_id = max(max_id, id_num)
                except (ValueError, IndexError, KeyError):
                    continue
            
            self._task_counter = itertools.count(max_id + 1)
            logger.info(f"📂 Loaded {len(all_tasks)} proactive tasks (Next ID: {max_id + 1})")
        except Exception as e:
            logger.error(f"Failed to load proactive tasks: {e}")

    def _fire_system_trigger(self, context_note, from_node, to_node, channel, targets='requester', disable_tools=False):
        """Fire a proactive system-triggered AI response to one or more targets.

        targets: comma-separated string of recipients:
            'requester'       - the node that originally scheduled the task
            'NodeName'        - any node by long or short name
            '!hexid'          - any specific node by hex ID
            'ch:N'            - broadcast on channel N (must be in allowed_channels)
        """
        prompt = (
            f"[SYSTEM WAKEUP] A proactive task has triggered.\n\n"
            f"COMMAND/CONTEXT: {context_note}\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. You MUST execute any instructions or checks contained in the COMMAND/CONTEXT above.\n"
            f"2. SEMANTIC CONTINUITY: Check your Knowledge Graph (MemPalace) for previous '[SYSTEM ACTION]' observations related to this task. This allows you to track state, counts, or trends across recurring events.\n"
            f"3. Use appropriate tools (telemetry, location, etc.) to fetch fresh data if the command requires it.\n"
            f"4. Your natural text response will be delivered automatically to: {targets}.\n"
            f"5. DO NOT use the 'send_message' tool to deliver the final report; your text response handles this."
        )
        logger.info(f"🔔 Firing system trigger for {from_node}: {context_note} -> targets={targets}")

        target_list = [t.strip() for t in targets.split(',') if t.strip()]
        allowed_channels = self.config.get('allowed_channels', [])

        for target in target_list:
            if target == 'requester':
                _to = from_node # The original requester who ran the !ai command
                _ch = channel   # The original channel (usually 0/DM)
            elif target.startswith('ch:'):
                try:
                    ch_idx = int(target[3:])
                except ValueError:
                    logger.warning(f"Invalid channel target: {target}")
                    continue
                if ch_idx not in allowed_channels:
                    logger.warning(f"Channel {ch_idx} not in allowed_channels — skipping.")
                    continue
                _to = '^all'
                _ch = ch_idx
            elif target.startswith('!'):
                _to = target
                _ch = 0
            else:
                # Resolve name to ID
                found_id = self.meshtastic.find_node_by_name(target)
                if found_id:
                    _to = found_id
                    _ch = 0
                else:
                    logger.warning(f"Unknown target (not a node name or ID): {target}")
                    continue

            # We pass _to as BOTH from_node and to_node to _process_ai_query_thread
            # because the AI query needs to be contextually 'to' the recipient.
            # is_dm=True ensures the response logic uses destination=_to.
            t = threading.Thread(
                target=self._process_ai_query_thread,
                args=(prompt, _to, _to, _ch),
                kwargs={'is_dm': (_to != '^all'), 'is_system_trigger': True, 'disable_tools': disable_tools},
                daemon=True
            )
            t.start()

    def _on_telemetry_proactive(self, packet, interface):
        """Callback fired on every incoming telemetry packet. Evaluates deferred requests and condition watchers."""
        try:
            from_id_raw = packet.get('fromId')
            if isinstance(from_id_raw, int):
                from_id = f"!{from_id_raw:08x}"
            else:
                from_id = str(from_id_raw).lower() if from_id_raw else None
            
            if not from_id:
                return

            now = time.time()
            # Quick check if ANY watcher or pending request cares about this node
            has_interest = (from_id in self.pending_telemetry_requests)
            if not has_interest:
                with self._condition_watchers_lock:
                    has_interest = any(w['node_id'].lower() == from_id for w in self.condition_watchers)
            

            # --- 1. Deferred telemetry callbacks ---
            if from_id in self.pending_telemetry_requests:
                req = self.pending_telemetry_requests[from_id] # Peak, don't pop yet (poll might consume it)
                event_str = f"Requested {req.get('context_note', 'telemetry')} arrived."
                self._add_to_collector(from_id, event_str, req)
                logger.info(f"⏳ Telemetry for {from_id} buffered in collector.")

            # --- 2. Condition watchers ---
            decoded = packet.get('decoded', {})
            telemetry = decoded.get('telemetry', {})
            
            
            if not telemetry:
                return

            # Flatten all metric values into a simple key->value dict for comparison
            # We must check BOTH snake_case (protobuf) and camelCase (library API)
            metric_values = {}
            category_map = {
                'device_metrics': 'deviceMetrics',
                'environment_metrics': 'environmentMetrics',
                'power_metrics': 'powerMetrics',
                'health_metrics': 'healthMetrics',
                'air_quality_metrics': 'airQualityMetrics',
                'local_stats': 'localStats',
                'host_metrics': 'hostMetrics'
            }

            for snake_cat, camel_cat in category_map.items():
                cat_data = telemetry.get(snake_cat) or telemetry.get(camel_cat)
                if isinstance(cat_data, dict):
                    for k, v in cat_data.items():
                        # Normalize key to snake_case for internal matching
                        snake_key = re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower()
                        metric_values[snake_key] = v
            
            # Also pull SNR from the packet envelope
            if 'rxSnr' in packet:
                metric_values['snr'] = packet['rxSnr']

            # --- 3. Background Semantic Indexing ---
            # Index current telemetry snapshot into MemPalace Knowledge Graph
            self._index_to_mcp('telemetry', {
                'node_id': from_id,
                'type': 'status_snapshot',
                'data': metric_values
            })


            with self._condition_watchers_lock:
                triggered = []
                for w in self.condition_watchers:
                    if w['node_id'] != from_id:
                        continue
                    
                    # 60-second cooldown per watcher to avoid spam from burst packets
                    if now - w.get('last_fired_at', 0) < 60:
                        continue
                    
                    # Watchers use normalized snake_case metrics (e.g. 'battery_level', 'temperature')
                    val = metric_values.get(w['metric'])
                        
                    if val is None:
                        continue
                        
                    op = w['operator']
                    thr = w['threshold']
                    
                    condition_met = (
                        (op == '<'  and val < thr)  or
                        (op == '>'  and val > thr)  or
                        (op == '<=' and val <= thr) or
                        (op == '>=' and val >= thr) or
                        (op == '==' and val == thr)
                    )
                    
                        
                    if condition_met:
                        w['last_fired_at'] = now
                        triggered.append(w)
                
                for w in triggered:
                    if not w.get('is_persistent', False):
                        self.condition_watchers.remove(w)
                if triggered:
                    self._save_proactive_tasks()
                    for w in triggered:
                        event_str = (
                            f"Condition alert: {w['context_note']}. "
                            f"Reading: {w['metric']}={metric_values.get(w['metric'])} "
                            f"(threshold: {w['operator']} {w['threshold']})."
                        )
                        self._add_to_collector(from_id, event_str, w)

        except Exception as e:
            logger.warning(f"Error in proactive telemetry handler: {e}")

    def _add_to_collector(self, node_id, event_text, context_dict):
        """Add an event to the proactive collector and ensure a timer is running."""
        node_id = node_id.lower()
        with self._collector_lock:
            if node_id not in self._proactive_event_collector:
                self._proactive_event_collector[node_id] = {
                    'events': [],
                    'from_node': context_dict.get('from_node'),
                    'to_node': context_dict.get('to_node'),
                    'channel': context_dict.get('channel'),
                    'targets': context_dict.get('targets', 'requester'),
                    'timer': None
                }
            
            entry = self._proactive_event_collector[node_id]
            if event_text not in entry['events']:
                entry['events'].append(event_text)
            
            # Reset/Start the 3-second aggregation timer
            if entry['timer']:
                entry['timer'].cancel()
            
            entry['timer'] = threading.Timer(3.0, self._dispatch_collector, args=[node_id])
            entry['timer'].daemon = True
            entry['timer'].start()

    def _dispatch_collector(self, node_id):
        """Aggregate all events for a node and fire a single AI trigger."""
        node_id = node_id.lower()
        entry = None
        with self._collector_lock:
            entry = self._proactive_event_collector.pop(node_id, None)
        
        if not entry or not entry['events']:
            return

        # Double check: if we are still polling for this node in a tool thread, 
        # we should potentially skip the proactive turn to avoid double messages.
        # But wait—the tool thread will pop the pending request if it finds it.
        # If the pending request is STILL there, it means the tool thread timed out or failed.
        
        events_summary = "\n".join([f"- {e}" for e in entry['events']])
        metadata = self.meshtastic.get_node_metadata(node_id)
        
        context = (
            f"Proactive update for {node_id}:\n"
            f"{events_summary}\n\n"
            f"Current Node Metadata:\n{metadata}"
        )
        
        # Consume the pending telemetry request if matched
        if node_id in self.pending_telemetry_requests:
            self.pending_telemetry_requests.pop(node_id)
            
        self._fire_system_trigger(
            context, 
            entry['from_node'], 
            entry['to_node'], 
            entry['channel'], 
            targets=entry['targets'], 
            disable_tools=False
        )
        logger.info(f"📣 Dispatched aggregated proactive Turn for {node_id} ({len(entry['events'])} events).")


    def _inject_legacy_metadata(self, query, from_node):
        """Helper to inject a clean metadata block for tool-blind models."""
        my_info = self.meshtastic.get_node_metadata(self.meshtastic.get_node_info().get('user', {}).get('id'))
        neighbor_summary = self.meshtastic.get_node_list_summary()
        user_info = self.meshtastic.get_node_metadata(from_node)
        
        metadata_block = "\n\n[RADIO CONTEXT]\n"
        if my_info:
            metadata_block += f"Self: {my_info}\n"
        if user_info:
            metadata_block += f"User ({from_node}): {user_info}\n"
        if neighbor_summary:
            metadata_block += f"{neighbor_summary}\n"
        metadata_block += "[/RADIO CONTEXT]"
        
        return f"{query}{metadata_block}"

    def _process_ai_query_thread(self, query, from_node, to_node, channel, is_dm=False, is_system_trigger=False, disable_tools=False):
        """Background thread for processing AI queries with adaptive tool support."""
        thread_id = threading.get_ident()
        with self._workers_lock:
            self._active_workers[thread_id] = {
                'start_time': time.time(),
                'from_node': from_node,
                'to_node': to_node,
                'channel': channel,
                'is_system_trigger': is_system_trigger
            }
            
        # Inject context for MCP tools running in this thread
        threading.current_thread().ai_context = {
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'is_system_trigger': is_system_trigger
        }
            
        try:
            # Short sleep to allow "Thinking..." message to clear if needed
            time.sleep(2)

            # 1. Get History Key and Session Status
            # Sessions are strictly DM-only. Ensure is_session is False in public channels
            # to prevent session indicators or logic from leaking into broadcasts.
            is_session = is_dm and self.session_manager.is_active(from_node)
            history_key = self._get_history_key(from_node, channel, is_dm)
            current_history = self.history.get(history_key, [])
            
            # 2. Capability Check & Tool Orchestration
            provider_name = self.config.get('current_provider', 'ollama')
            provider = get_provider(provider_name, self.config)
            
            # User-controlled awareness toggle
            awareness_enabled = self.config.get('meshtastic_awareness', config.MESHTASTIC_AWARENESS)
            
            # Adaptive Logic: Tools vs Metadata Injection
            tools = None
            final_query = query
            
            if not awareness_enabled:
                logger.info("🚫 Meshtastic Awareness is DISABLED. Skipping metadata/tools.")
                if not is_system_trigger:
                    self.add_to_history(history_key, 'user', query, node_id=from_node)
                else:
                    current_history.append({'role': 'user', 'content': query})
            else:
                # Awareness is enabled - determine if we need metadata refresh
                # Standard logic: Inject on first message or if refresh is pending
                is_first_msg = len(current_history) == 0
                needs_refresh = (from_node in self._refresh_metadata_nodes)
                
                # Intelligent logic: Inject if keywords (battery, location, status) or node names are mentioned
                keywords = ['battery', 'voltage', 'location', 'where', 'snr', 'rssi', 'distance', 'away', 'status']
                is_keyword_query = any(k in query.lower() for k in keywords)
                
                # Check for mentions of bot or neighbors
                mentions_bot = False
                my_node_info = self.meshtastic.get_node_info()
                if my_node_info:
                    bot_names = [
                        str(my_node_info.get('user', {}).get('longName') or '').lower(),
                        str(my_node_info.get('user', {}).get('shortName') or '').lower(),
                        'bot', 'you'
                    ]
                    bot_names = [n for n in bot_names if n]
                    mentions_bot = any(n in query.lower() for n in bot_names)

                must_refresh = is_first_msg or needs_refresh or is_keyword_query or mentions_bot
                
                combined_metadata = None
                if must_refresh:
                    # Fetch dual metadata for context
                    my_node_info = self.meshtastic.get_node_info() or {}
                    my_id = my_node_info.get('user', {}).get('id')
                    local_metadata = self.meshtastic.get_node_metadata(my_id)
                    remote_metadata = self.meshtastic.get_node_metadata(from_node)
                    combined_metadata = self._format_dual_metadata(local_metadata, remote_metadata)
                    
                    # Clear refresh flag
                    if from_node in self._refresh_metadata_nodes:
                        self._refresh_metadata_nodes.remove(from_node)
                
                if provider.supports_tools:
                    if not disable_tools:
                        logger.info(f"🤖 Provider '{provider.name}' supports tools. Using function calling.")
                        tools = self.mcp_client.get_all_tools()
                    else:
                        logger.info(f"🤖 Provider '{provider.name}' supports tools, but they are disabled for this turn.")
                    
                    # Log to history (metadata included in prompt content for system triggers)
                    if not is_system_trigger:
                        self.add_to_history(history_key, 'user', query, node_id=from_node, metadata=combined_metadata)
                    else:
                        msg = f"User ({from_node}): {query}" if combined_metadata else query
                        current_history.append({'role': 'user', 'content': msg})
                else:
                    logger.info(f"💾 Provider '{provider.name}' does not support native tools. Injecting legacy metadata block.")
                    final_query = self._inject_legacy_metadata(query, from_node) if combined_metadata else query
                    if not is_system_trigger:
                        self.add_to_history(history_key, 'user', query, node_id=from_node, metadata=combined_metadata)
                    else:
                        current_history.append({'role': 'user', 'content': final_query})

            # 3. Add to history logging
            current_session = self.session_manager.get_session_name(from_node)
            msgs_count = len(current_history)
            logger.info(f"🧠 AI Context: Session='{current_session or 'None'}' | Messages={msgs_count} | Trigger={is_system_trigger}")

            # 4. Extract Primary Location for Grounding if available (only if awareness is enabled)
            location = None
            if awareness_enabled:
                try:
                    node_info = self.meshtastic._get_node_by_id(from_node)
                    if node_info:
                        pos = node_info.get('position', {})
                        lat = pos.get('latitude')
                        if lat is None and pos.get('latitudeI') is not None:
                            lat = pos.get('latitudeI') / 1e7
                        lon = pos.get('longitude')
                        if lon is None and pos.get('longitudeI') is not None:
                            lon = pos.get('longitudeI') / 1e7
                        if lat is not None and lon is not None:
                            location = {'latitude': lat, 'longitude': lon}
                            logger.info(f"📍 Primary location identified for grounding: {lat:.6f}, {lon:.6f}")
                except Exception as e:
                    logger.debug(f"Could not extract primary location for grounding: {e}")

            # 5. Get AI response
            response = provider.get_response(final_query, current_history[-30:], 
                                          context_id=history_key if not is_system_trigger else f"sys_{history_key}", 
                                          location=location, tools=tools, mcp_client=self.mcp_client)
            
            # 6. Silent-ACK: if every tool fired proactively, the provider returns the sentinel.
            # In this case do not send any reply — the user already received the info.
            if response == "__SILENT_ACK__":
                logger.info("🔇 Silent ACK — all telemetry was handled by proactive callbacks. No reply sent.")
                return
            
            # 7. Add assistant response to history (skip for system triggers to avoid pollution)
            if not is_system_trigger:
                self.add_to_history(history_key, 'assistant', response)
                
                # 8. Save to conversation if in session
                session_name = self.session_manager.get_session_name(from_node)
                if session_name:
                    self.conversation_manager.save_conversation(from_node, session_name, self.history[history_key])
                    self.session_manager.update_activity(from_node)
            
            # 9. Background Semantic Indexing (Enabled for both users and system triggers)
            self._index_to_mcp('conversation', {
                'node_id': from_node,
                'channel': channel,
                'prompt': query,
                'response': response,
                'is_system': is_system_trigger
            })
            
            logger.info(f"💬 {provider.name} response ({len(response)} chars): {response[:80]}...")
            self.send_response(response, from_node, to_node, channel, is_admin_cmd=False, use_session_indicator=is_session)
            logger.info(f"✅ Response queued to {from_node} on ch{channel}")
            
        except Exception as e:
            logger.error(f"Error processing AI query: {e}", exc_info=True)
            self.send_response(f"❌ Error: {str(e)[:50]}", from_node, to_node, channel)
        finally:
            with self._workers_lock:
                self._active_workers.pop(thread_id, None)
    
    # ==================== Meshtastic Message Handler ====================
    
    def on_receive(self, packet, interface):
        """Callback for incoming Meshtastic messages."""
        self.touch_health()
        
        try:
            # Update activity timestamp
            self.last_activity = time.time()
            
            # Check node-online watchers on EVERY packet (before portnum filter)
            from_id_raw = packet.get('fromId')
            if from_id_raw:
                if isinstance(from_id_raw, int):
                    from_id_check = f"!{from_id_raw:08x}"
                else:
                    from_id_check = from_id_raw
                
                with self._node_online_watchers_lock:
                    triggered = [w for w in self.node_online_watchers if w['node_id'] == from_id_check]
                    for w in triggered:
                        if not w.get('is_persistent', False):
                            self.node_online_watchers.remove(w)
                    if triggered:
                        self._save_proactive_tasks()
                    
                for w in triggered:
                    context = f"Node online alert: {w['context_note']}. Node {from_id_check} was just heard on the mesh."
                    self._fire_system_trigger(context, w['from_node'], w['to_node'], w['channel'], targets=w.get('targets', 'requester'), disable_tools=True)
                    logger.info(f"🟢 Node-online watcher fired for {from_id_check}")
            
            # Extract packet data
            if 'decoded' not in packet or 'portnum' not in packet['decoded']:
                return
            
            if packet['decoded']['portnum'] != 'TEXT_MESSAGE_APP':
                return
            
            # Get message details
            from_node = packet.get('fromId', 'unknown')
            to_node = packet.get('toId', 'unknown')
            channel = packet.get('channel', 0)
            text = packet['decoded'].get('text', '').strip()
            
            if not text:
                return
            
            logger.info(f"📨 Message from {from_node} to {to_node} on channel {channel}: {text[:50]}...")
            
            # Determine DM status
            my_node_info = self.meshtastic.get_node_info()
            my_id = my_node_info.get('user', {}).get('id', '') if my_node_info else ''
            # In MeshPacket, 'toId' is our ID for DMs. STRICT check.
            is_dm = (to_node == my_id)

            # Check if user is in an active session (DM only)
            if is_dm and self.session_manager.is_active(from_node):
                # Check for timeout
                timed_out, message, session_channel, session_to_node = self.session_manager.check_timeout(from_node)
                if timed_out:
                    # Session timed out, send notification
                    self.send_response(message, from_node, session_to_node, session_channel, is_admin_cmd=False)
                    # Don't process the message as a session message
                else:
                    # Active session - process as AI query without !ai prefix (DMs only)
                    if not text.startswith('!ai'):
                        self._handle_ai_query(text, from_node, to_node, channel, is_dm=is_dm)
                        return
            
            # Check for !ai command
            if text.lower().startswith('!ai'):
                self.process_command(text, from_node, to_node, channel)
            
        except Exception as e:
            logger.error(f"Error in on_receive: {e}", exc_info=True)
    
    # ==================== Connection Management ====================
    
    def connect(self):
        """Connect to Meshtastic and start the main loop."""
        logger.info("🚀 AI Responder Service Starting...")
        
        # Log AI Provider info at startup
        self._log_provider_info()
        
        # Initial Connection
        # We don't exit if this fails, we just enter the loop and retry there
        if self.meshtastic.connect(on_receive_callback=self.on_receive):
            logger.info("✅ Initial connection successful.")
        else:
            logger.warning("⚠️ Initial connection failed. Will retry in main loop.")
        
        # Subscribe to telemetry for proactive callbacks (condition watchers + deferred requests)
        try:
            from pubsub import pub
            try:
                pub.unsubscribe(self._on_telemetry_proactive, "meshtastic.receive.telemetry")
            except Exception:
                pass
            pub.subscribe(self._on_telemetry_proactive, "meshtastic.receive.telemetry")
            logger.info("✅ Subscribed to meshtastic.receive.telemetry for proactive agents")
        except Exception as e:
            logger.warning(f"⚠️ Could not subscribe to telemetry for proactive agents: {e}")
        
        self.running = True
        
        # Main loop
        try:
            self._last_health_log = 0  # track periodic health status
            while self.running:
                # 2. Radio Watchdog & Health Check
                current_time = time.time()
                health_ok = True
                reasons = []

                # Check radio activity
                last_radio = self.meshtastic.last_activity
                if last_radio > 0:
                    time_since_radio = current_time - last_radio
                    if time_since_radio > HEALTH_CHECK_ACTIVITY_TIMEOUT:
                        # Radio silent too long
                        time_since_last_probe = current_time - self.last_probe
                        if time_since_last_probe > 40: # Probe every 40s when silent
                            logger.warning(f"Radio silent for {int(time_since_radio)}s. Sending active probe...")
                            self.last_probe = current_time
                            self.meshtastic.send_probe()
                        elif time_since_last_probe > 30:
                            # If we probed 30s ago and still no activity, health is failing
                            health_ok = False
                            reasons.append(f"Radio silent (Probed {int(time_since_last_probe)}s ago - NO REPLY)")
                
                # Check connection status
                if not self.meshtastic.is_connected():
                    if not self.connection_lost:
                        self.connection_lost = True
                        self.last_activity = current_time # Start tracking disconnect duration
                        logger.warning("Meshtastic connection lost. Attempting to reconnect...")
                    
                    # Try to reconnect every 10 seconds
                    if int(current_time) % 10 == 0:
                        if self.meshtastic.connect(on_receive_callback=self.on_receive):
                            logger.info("✅ Reconnected to Meshtastic successfully.")
                            self.connection_lost = False
                        else:
                            logger.warning("Still disconnected from Meshtastic...")

                    # Fallback to exit/restart if we can't recover quickly.
                    # Match mqtt-proxy's pattern: exit fast and let Docker restart us cleanly.
                    # This ensures hung DNS/Gemini threads are cleaned up by the OS.
                    if current_time - self.last_activity > 60:
                        health_ok = False
                        reasons.append("Connection lost for >60s (Reconnection attempts failed)")
                else:
                    self.connection_lost = False

                # 3. Update Heartbeat / Health Check
                # Also check message queue heartbeat (should tick every 500ms when idle)
                queue_heartbeat = getattr(self.meshtastic, 'queue', None)
                if queue_heartbeat and (current_time - queue_heartbeat.last_heartbeat > 300):
                    health_ok = False
                    reasons.append("Message queue thread stalled (>300s)")
                
                # Check for stalled worker threads
                with self._workers_lock:
                    for tid, worker_data in list(self._active_workers.items()):
                        # Backwards compatibility check in case of mid-run reload
                        if isinstance(worker_data, dict):
                            start_time = worker_data.get('start_time', 0)
                            from_node = worker_data.get('from_node')
                            to_node = worker_data.get('to_node')
                            channel = worker_data.get('channel')
                        else:
                            start_time = worker_data
                            from_node = to_node = channel = None

                        age = int(current_time - start_time)
                        if age > 90: # 90s > 45s hard thread timeout
                            health_ok = False
                            reasons.append(f"AI Worker thread {tid} stalled ({age}s > 90s limit)")
                            logger.warning(f"🐛 Worker thread {tid} has been running for {age}s — likely stuck in DNS/network hang.")
                            
                            if from_node and to_node and channel is not None:
                                logger.info(f"Sending timeout notification to {from_node}")
                                self.send_response(
                                    "⚠️ AI request timed out due to network congestion or API failure.",
                                    from_node,
                                    to_node,
                                    channel,
                                    is_admin_cmd=False
                                )
                                # Wait a moment to try and ensure the message is queued before exiting
                                time.sleep(2)
                            break

                # Periodic health status log (every 60s, always)
                if current_time - self._last_health_log > 60:
                    self._last_health_log = current_time
                    with self._workers_lock:
                        active_count = len(self._active_workers)
                    queue = getattr(self.meshtastic, '_message_queue', None)
                    q_age = int(current_time - queue.last_heartbeat) if queue else -1
                    connected = self.meshtastic.is_connected()
                    logger.info(
                        f"💓 Health: connected={connected} | "
                        f"active_workers={active_count} | "
                        f"queue_last_heartbeat={q_age}s ago"
                    )

                if health_ok:
                    try:
                        with open("/tmp/healthy", "w") as f:
                            f.write(str(current_time))
                    except Exception:
                        pass
                else:
                    logger.error(f"Health check FAILED: {', '.join(reasons)}. Exiting...")
                    if os.path.exists("/tmp/healthy"):
                        try:
                            os.remove("/tmp/healthy")
                        except Exception:
                            pass
                    sys.exit(1)

                # 4. Periodic session timeout check
                timed_out_data = self.session_manager.check_all_timeouts()
                for data in timed_out_data:
                    self.send_response(
                        data['message'], 
                        data['user_id'], 
                        data['to_node'], 
                        data['channel'], 
                        is_admin_cmd=False
                    )

                # 5. Scheduled task ticker
                now = time.time()
                with self._scheduled_tasks_lock:
                    to_keep = []
                    for task in self.scheduled_tasks:
                        if now >= task['next_time']:
                            self._fire_system_trigger(
                                task['context_note'],
                                task['from_node'],
                                task['to_node'],
                                task['channel']
                            )
                            # Reschedule if recurring and not expired
                            if task['interval'] and now < task['end_time']:
                                task['next_time'] = now + task['interval']
                                to_keep.append(task)
                            # else: one-shot or expired, discard
                        else:
                            to_keep.append(task)
                    self.scheduled_tasks = to_keep

                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("\n👋 Shutting down AI Responder...")
        finally:
            self.meshtastic.disconnect()
            logger.info("✅ AI Responder stopped.")


    def _log_provider_info(self):
        """Log the current AI provider and model."""
        current_provider = self.config.get('current_provider', 'ollama')
        logger.info(f"🤖 Active AI Provider: {current_provider.upper()}")
        
        if current_provider == 'gemini':
            from config import GEMINI_MODEL
            logger.info(f"🧠 Model: {GEMINI_MODEL}")
        elif current_provider == 'ollama':
            from config import OLLAMA_MODEL
            logger.info(f"🦙 Model: {OLLAMA_MODEL}")
        elif current_provider == 'openai':
            from config import OPENAI_MODEL
            logger.info(f"🤖 Model: {OPENAI_MODEL}")
        elif current_provider == 'anthropic':
            from config import ANTHROPIC_MODEL
            logger.info(f"🧠 Model: {ANTHROPIC_MODEL}")


if __name__ == "__main__":
    responder = AIResponder()
    responder.connect()
