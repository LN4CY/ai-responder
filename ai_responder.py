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
import requests
import pathlib
from pubsub import pub



# Import our modular components
import config
from config import (
    Config, INTERFACE_TYPE, SERIAL_PORT, MESHTASTIC_HOST, MESHTASTIC_PORT,
    HISTORY_DIR, HISTORY_MAX_BYTES, HISTORY_MAX_MESSAGES,
    ENV_ADMIN_NODE_ID, ALLOWED_CHANNELS, AI_PROVIDER,
    HEALTH_CHECK_ACTIVITY_TIMEOUT, HEALTH_CHECK_PROBE_INTERVAL
)
from providers import get_provider
from conversation.manager import ConversationManager
from conversation.session import SessionManager
from meshtastic_handler import MeshtasticHandler

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


__version__ = "1.5.0"

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
        self._active_workers = {} # {thread_id: start_time}
        self._workers_lock = threading.Lock()
        
        # Ensure history directory exists
        # Ensure history directory exists
        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)
            logger.info(f"Created history directory: {self.history_dir}")
        
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
            except: pass
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
    
    def get_memory_status(self, user_id):
        """
        Get memory and conversation status for a user.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            str: Formatted status message
        """
        # Load history if not in memory
        if user_id not in self.history:
            self.load_history(user_id)
        
        # Get history stats
        message_count = len(self.history[user_id])
        history_path = self._get_history_path(user_id)
        
        if os.path.exists(history_path):
            history_size = os.path.getsize(history_path)
            size_kb = history_size / 1024
            max_kb = config.HISTORY_MAX_BYTES / 1024
        else:
            size_kb = 0
            max_kb = config.HISTORY_MAX_BYTES / 1024
        
        # Get conversation slot usage
        metadata = self.conversation_manager._load_metadata(user_id)
        user_conversations = [name for name in metadata if not name.startswith('channel_')]
        slot_usage = len(user_conversations)
        
        # Get current provider
        provider = self.config.get('current_provider', 'ollama')
        
        # Format status message
        from config import MAX_CONVERSATIONS
        status = (
            f"💾 Memory Status\n"
            f"Messages: {message_count}/{config.HISTORY_MAX_MESSAGES}\n"
            f"Size: {size_kb:.1f}KB/{max_kb:.0f}KB\n"
            f"Slots: {slot_usage}/{MAX_CONVERSATIONS}\n"
            f"Provider: {provider.upper()}"
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
            tools: Optional dict of tools for function calling
            
        Returns:
            str: AI response or error message
        """
        provider_name = self.config.get('current_provider', 'ollama')
        
        try:
            # Get provider instance
            provider = get_provider(provider_name, self.config)
            
            # Get history for context
            history = None
            if history_key and history_key in self.history:
                # Context Tuning:
                # - Sessions get full context (e.g., 30 messages)
                # - Channel/Quick queries get minimal context (e.g., 2 messages)
                limit = 30 if is_session else 2
                history = self.history[history_key][-limit:]
            
            # Get response
            response = provider.get_response(prompt, history, context_id=history_key, location=location, tools=tools)
            return response
            
        except ValueError as e:
            logger.error(f"Provider error: {e}")
            return f"Error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error getting AI response: {e}")
            return f"Error: {str(e)}"
    
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
        except:
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
        if cmd == '-m':
            status = self.get_memory_status(from_node)
            self.send_response(status, from_node, to_node, channel, is_admin_cmd=False)
            return
        
        # ===== Session Commands (DM only) =====
        if cmd == '-n':
            if is_dm:
                # Start session
                session_name = args if args else None
                success, message, conv_name = self.session_manager.start_session(from_node, session_name, channel, to_node)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            else:
                # In channel: clear history and start new conversation
                self.clear_history(from_node)
                if args:
                    # Process the query
                    self._handle_ai_query(args, from_node, to_node, channel, "Thinking (New Conversation)... 🤖")
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
        admin_only_commands = ['-p', '-ch', '-a']
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
            return
        
        # ===== Default: AI Query =====
        # If no command matched, treat the entire text (minus !ai) as a query
        query = ' '.join(parts[1:]) if len(parts) > 1 else ''
        if query:
            self._handle_ai_query(query, from_node, to_node, channel)
    
    def _handle_help_command(self, from_node, to_node, channel, is_dm, is_admin):
        """Send context-aware help messages."""
        # Message 1: Basic Commands
        basic_help = (
            "🤖 AI Responder - Basic Commands\n\n"
            "!ai <query> : Ask the AI a question\n"
            "!ai -m : Show memory & slot usage\n"
            "!ai -h : Show this help"
        )
        self.send_response(basic_help, from_node, to_node, channel, is_admin_cmd=False)
        # Increase wait time for broadcasts to ensure delivery
        wait_time = 2 if is_dm else 5
        time.sleep(wait_time)
        
        # Message 2: Session Commands (DM only)
        if is_dm:
            session_help = (
                "🟢 Session Commands (DM Only)\n\n"
                "!ai -n [name] : Start new session\n"
                "  • Auto-names if no name given\n"
                "  • No !ai prefix needed in session\n"
                "  • 5min timeout\n"
                "!ai -end : End current session"
            )
            self.send_response(session_help, from_node, to_node, channel, is_admin_cmd=False)
            time.sleep(2)
        
        # Message 3: Conversation Management
        if is_dm:
            conv_help = (
                "📚 Conversations\n"
                "!ai -c : Resume last\n"
                "!ai -c <id> : Load specific\n"
                "!ai -c ls : List saved\n"
                "!ai -c rm <id> : Delete\n"
                "!ai -c rm all : Wipe all\n"
                "In Channels:\n"
                "!ai -n <msg> : New topic"
            )
        else:
            conv_help = (
                "📚 Conversation Commands\n\n"
                "!ai -n <query> : Start new topic\n"
                "!ai -c : Recall your last topic\n"
                "(DM for advanced management)"
            )
        self.send_response(conv_help, from_node, to_node, channel, is_admin_cmd=False)
        
        # Message 4: Admin Commands
        if is_admin:
            time.sleep(2)
            if is_dm:
                admin_help = (
                    "⚙️ Admin (DM Only)\n"
                    "!ai -p [name] : Provider\n"
                    "  (local/gemini/openai)\n"
                    "!ai -ch [add/rm] : Channels\n"
                    "!ai -a [ls/add/rm] : Admins"
                )
            else:
                admin_help = (
                    "⚙️ Admin Note\n\n"
                    "Send !ai -h in DM for admin commands."
                )
            self.send_response(admin_help, from_node, to_node, channel, is_admin_cmd=False)
    
    def _handle_conversation_command(self, args, from_node, to_node, channel):
        """Handle conversation management commands."""
        # Determine if this is a DM (sessions are DM-only)
        is_dm = (to_node != '^all' and (channel == 0 or to_node.startswith('!')))
        
        if not args:
            # Load last conversation (most recently accessed)
            metadata = self.conversation_manager._load_metadata(from_node)
            if metadata:
                # Find most recent
                latest = max(metadata.items(), key=lambda x: x[1]['last_access'])
                success, message, history, conversation_name = self.conversation_manager.load_conversation(from_node, latest[0])
                if success and history:
                    # Use conversation name as history key
                    self.history[conversation_name] = history
                    # Mark for metadata refresh
                    self._refresh_metadata_nodes.add(from_node)
                    # If in DM, restart the session so they can continue chatting
                    if is_dm:
                        self.session_manager.start_session(from_node, conversation_name, channel, to_node)
                        message += "\n🟢 Session Resumed"
                    self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                else:
                    self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            else:
                self.send_response("No saved conversations found.", from_node, to_node, channel, is_admin_cmd=False)
            return
        
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower()
        
        if subcmd == 'ls':
            # List conversations
            listing = self.conversation_manager.list_conversations(from_node)
            self.send_response(listing, from_node, to_node, channel, is_admin_cmd=False)
        
        elif subcmd == 'rm':
            # Delete conversation
            if len(parts) < 2:
                self.send_response("Usage: !ai -c rm <name/slot/all>", from_node, to_node, channel, is_admin_cmd=False)
                return
            identifier = parts[1]
            
            # Handle "rm all"
            if identifier.lower() == 'all':
                success, message = self.conversation_manager.delete_all_conversations(from_node)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                # Also clear active session if in one
                self.session_manager.end_session(from_node)
                # And in-memory history cache
                self.history.pop(from_node, None) 
                return

            success, message = self.conversation_manager.delete_conversation(from_node, identifier)
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
        
        else:
            # Load specific conversation
            success, message, history, conversation_name = self.conversation_manager.load_conversation(from_node, args)
            if success and history:
                # Use conversation name as history key
                self.history[conversation_name] = history
                # Mark for metadata refresh
                self._refresh_metadata_nodes.add(from_node)
                # If in DM, restart the session so they can continue chatting
                if is_dm:
                    self.session_manager.start_session(from_node, conversation_name, channel, to_node)
                    message += "\n🟢 Session Resumed"
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            else:
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
    
    def _handle_provider_command(self, args, from_node, to_node, channel):
        """Handle AI provider switching."""
        if not args:
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
        
        if not action:
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
            self.send_response("Usage: !ai -ch add/rm <channel_id>", from_node, to_node, channel, is_admin_cmd=True)
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
        if not args:
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
            self.send_response("Usage: !ai -a add/rm <node_id>", from_node, to_node, channel, is_admin_cmd=True)
            return
        
        action = parts[0].lower()
        node_id = parts[1]
        admin_nodes = self.config.get('admin_nodes', [])
        
        if action == 'add':
            if node_id not in admin_nodes:
                admin_nodes.append(node_id)
                self.config['admin_nodes'] = admin_nodes
                self.config.save()
                self.send_response(f"✅ Added admin {node_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"{node_id} is already an admin", from_node, to_node, channel, is_admin_cmd=True)
        
        elif action == 'rm':
            if node_id in admin_nodes:
                admin_nodes.remove(node_id)
                self.config['admin_nodes'] = admin_nodes
                self.config.save()
                self.send_response(f"✅ Removed admin {node_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"{node_id} is not an admin", from_node, to_node, channel, is_admin_cmd=True)
        
        else:
            self.send_response("Usage: !ai -a add/rm <node_id>", from_node, to_node, channel, is_admin_cmd=True)
    
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
                self._active_workers[thread_id] = time.time()

    def get_tools(self):
        """
        Define tools available to the AI.
        
        Returns:
            dict: Tool definitions and handlers
        """
        return {
            "get_my_info": {
                "declaration": {
                    "name": "get_my_info",
                    "description": "Get information about the bot itself, including name, battery, and SNR.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {},
                        "required": []
                    }
                },
                "handler": lambda: self.meshtastic.get_node_metadata(
                    self.meshtastic.get_node_info().get('user', {}).get('id')
                )
            },
            "get_mesh_nodes": {
                "declaration": {
                    "name": "get_mesh_nodes",
                    "description": "Get a summary of all nodes currently seen on the network, including their calculated distance from the bot (if coordinates are available).",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {},
                        "required": []
                    }
                },
                "handler": lambda: self.meshtastic.get_node_list_summary()
            },
            "get_node_details": {
                "declaration": {
                    "name": "get_node_details",
                    "description": "Get detailed metadata, battery, and environment data for a specific node.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "node_id_or_name": {
                                "type": "STRING",
                                "description": "Hex ID (e.g. !1234abcd) or name of the node."
                            }
                        },
                        "required": ["node_id_or_name"]
                    }
                },
                "handler": self._get_node_details_tool
            },
            "request_node_telemetry": {
                "declaration": {
                    "name": "request_node_telemetry",
                    "description": "Trigger an active refresh of telemetry (device, environment, or local_stats) from a specific node. WARNING: Each request takes up to 15 seconds on the mesh. Do not request more than 2 telemetry types at once to avoid network congestion and timeouts. Prioritize 'device' and 'environment'.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "node_id_or_name": {
                                "type": "STRING",
                                "description": "Hex ID (e.g. !1234abcd) or name of the node."
                            },
                            "telemetry_type": {
                                "type": "STRING",
                                "description": "Type of telemetry to request: 'device', 'environment', 'local_stats', 'air_quality', 'power', 'health', or 'host'.",
                                "enum": ["device", "environment", "local_stats", "air_quality", "power", "health", "host"]
                            }
                        },
                        "required": ["node_id_or_name", "telemetry_type"]
                    }
                },
                "handler": self._request_node_telemetry_tool
            },
            "get_location_address": {
                "declaration": {
                    "name": "get_location_address",
                    "description": "Convert latitude and longitude coordinates into a real-world street address, city, and state.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "lat": {
                                "type": "NUMBER",
                                "description": "The latitude coordinate."
                            },
                            "lon": {
                                "type": "NUMBER",
                                "description": "The longitude coordinate."
                            }
                        },
                        "required": ["lat", "lon"]
                    }
                },
                "handler": self._get_location_address_tool
            }
        }

    def _get_location_address_tool(self, lat, lon):
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

    def _request_node_telemetry_tool(self, node_id_or_name, telemetry_type):
        """Internal handler for request_node_telemetry tool with short polling."""
        self._touch_worker()
        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found."

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

        # 2. Send Request
        request_time = time.time()
        logger.info(f"📡 AI triggering telemetry refresh ({telemetry_type}) for {node_id}")
        self.meshtastic.request_telemetry(node_id, telemetry_type)

        # 3. Short Poll (Wait up to 15 seconds for data to arrive in cache)
        # We check the cache every 3 seconds
        poll_start = time.time()
        poll_timeout = 15 
        
        while time.time() - poll_start < poll_timeout:
            self._touch_worker()
            time.sleep(3)
            # Check timestamps in the handler
            node_timestamps = self.meshtastic.telemetry_timestamps.get(node_id, {})
            last_received = node_timestamps.get(metric_key, 0)
            
            if last_received > request_time:
                # Fresh data arrived!
                metadata = self.meshtastic.get_node_metadata(node_id)
                return f"Success! New telemetry received:\n{metadata}"

        # 4. Timeout fallback
        return (f"Refresh request for {telemetry_type} sent to {node_id_or_name}. "
                "The mesh is slow—please wait about 60 seconds and ask me again. "
                "I should have the data in my memory by then.")

    def _inject_legacy_metadata(self, query, from_node):
        """Helper to inject a clean metadata block for tool-blind models."""
        my_info = self.meshtastic.get_node_metadata(self.meshtastic.get_node_info().get('user', {}).get('id'))
        neighbor_summary = self.meshtastic.get_node_list_summary()
        user_info = self.meshtastic.get_node_metadata(from_node)
        
        metadata_block = "\n\n[RADIO CONTEXT]\n"
        if my_info: metadata_block += f"Self: {my_info}\n"
        if user_info: metadata_block += f"User ({from_node}): {user_info}\n"
        if neighbor_summary: metadata_block += f"{neighbor_summary}\n"
        metadata_block += "[/RADIO CONTEXT]"
        
        return f"{query}{metadata_block}"

    def _process_ai_query_thread(self, query, from_node, to_node, channel, is_dm=False):
        """Background thread for processing AI queries with adaptive tool support."""
        thread_id = threading.get_ident()
        with self._workers_lock:
            self._active_workers[thread_id] = time.time()
            
        try:
            # Short sleep to allow "Thinking..." message to clear if needed
            time.sleep(2)

            # 1. Get History Key and Session Status
            # Sessions are strictly DM-only. Ensure is_session is False in public channels
            # to prevent session indicators or logic from leaking into broadcasts.
            is_session = is_dm and self.session_manager.is_active(from_node)
            history_key = self._get_history_key(from_node, channel, is_dm)
            
            # 2. Capability Check & Tool Orchestration
            provider_name = self.config.get('current_provider', 'ollama')
            provider = get_provider(provider_name, self.config)
            
            # User-controlled awareness toggle
            awareness_enabled = self.config.get('meshtastic_awareness', config.MESHTASTIC_AWARENESS)
            
            # Adaptive Logic: Tools vs Metadata Injection
            tools = None
            final_query = query
            
            if not awareness_enabled:
                logger.info(f"🚫 Meshtastic Awareness is DISABLED. Skipping metadata/tools.")
                self.add_to_history(history_key, 'user', query, node_id=from_node)
            else:
                # Awareness is enabled - determine if we need metadata refresh
                # Standard logic: Inject on first message or if refresh is pending
                is_first_msg = len(self.history.get(history_key, [])) == 0
                needs_refresh = (from_node in self._refresh_metadata_nodes)
                
                # Intelligent logic: Inject if keywords (battery, location, status) or node names are mentioned
                keywords = ['battery', 'voltage', 'location', 'where', 'snr', 'rssi', 'distance', 'away', 'status']
                is_keyword_query = any(k in query.lower() for k in keywords)
                
                # Check for mentions of bot or neighbors
                mentions_bot = False
                my_node_info = self.meshtastic.get_node_info()
                if my_node_info:
                    bot_names = [
                        my_node_info.get('user', {}).get('longName', '').lower(),
                        my_node_info.get('user', {}).get('shortName', '').lower(),
                        'bot', 'you'
                    ]
                    mentions_bot = any(n and n in query.lower() for n in bot_names if n)

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
                    logger.info(f"🤖 Provider '{provider.name}' supports tools. Using function calling.")
                    tools = self.get_tools()
                    # Log to history (metadata may be None if already injected/cached)
                    self.add_to_history(history_key, 'user', query, node_id=from_node, metadata=combined_metadata)
                else:
                    logger.info(f"💾 Provider '{provider.name}' is tool-blind. Injecting legacy metadata block.")
                    final_query = self._inject_legacy_metadata(query, from_node) if combined_metadata else query
                    self.add_to_history(history_key, 'user', query, node_id=from_node, metadata=combined_metadata)

            # 3. Add to history logging
            current_session = self.session_manager.get_session_name(from_node)
            msgs_count = len(self.history.get(history_key, []))
            logger.info(f"🧠 AI Context: Session='{current_session or 'None'}' | Messages={msgs_count}")

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
            response = provider.get_response(final_query, self.history.get(history_key, [])[-30:], 
                                          context_id=history_key, location=location, tools=tools)
            
            # 6. Add assistant response to history
            self.add_to_history(history_key, 'assistant', response)
            
            # 7. Save to conversation if in session
            session_name = self.session_manager.get_session_name(from_node)
            if session_name:
                self.conversation_manager.save_conversation(from_node, session_name, self.history[history_key])
                self.session_manager.update_activity(from_node)
            
            logger.info(f"💬 Gemini response ({len(response)} chars): {response[:80]}...")
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
                    for tid, start_time in list(self._active_workers.items()):
                        age = int(current_time - start_time)
                        if age > 90: # 90s > 45s hard thread timeout
                            health_ok = False
                            reasons.append(f"AI Worker thread {tid} stalled ({age}s > 90s limit)")
                            logger.warning(f"🐛 Worker thread {tid} has been running for {age}s — likely stuck in DNS/network hang.")
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
                    except: pass
                else:
                    logger.error(f"Health check FAILED: {', '.join(reasons)}. Exiting...")
                    if os.path.exists("/tmp/healthy"):
                        try: os.remove("/tmp/healthy")
                        except: pass
                    sys.exit(1)

                # 4. Periodic session timeout check
                timed_out_data = self.session_manager.check_all_timeouts()
                for data in timed_out_data:
                    # Send timeout notification proactively
                    self.send_response(
                        data['message'], 
                        data['user_id'], 
                        data['to_node'], 
                        data['channel'], 
                        is_admin_cmd=False
                    )

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
