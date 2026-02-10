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
from pubsub import pub

# Import our modular components
import config
from config import (
    Config, INTERFACE_TYPE, SERIAL_PORT, MESHTASTIC_HOST, MESHTASTIC_PORT,
    HISTORY_DIR, HISTORY_MAX_BYTES, HISTORY_MAX_MESSAGES,
    ENV_ADMIN_NODE_ID, ALLOWED_CHANNELS, AI_PROVIDER
)
from providers import get_provider
from conversation.manager import ConversationManager
from conversation.session import SessionManager
from meshtastic_handler import MeshtasticHandler

# Logging setup
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('AI-Responder')

# Set logging level for handler based on global log_level
logging.getLogger('meshtastic_handler').setLevel(getattr(logging, log_level, logging.INFO))


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
        
        # Provider logging moved to connect()
    
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
        session_name = self.session_manager.get_session_name(from_node)
        if session_name:
            return session_name
            
        if is_dm:
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
        safe_key = key.replace(':', '_').replace('^', 'B')
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
            parts.append(f"[Bot: {local_metadata}]")
        
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
    
    def get_ai_response(self, prompt, history_key, is_session=False, location=None):
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
            
            # Get history for context
            history = None
            if history_key and history_key in self.history:
                # Context Tuning:
                # - Sessions get full context (e.g., 30 messages)
                # - Channel/Quick queries get minimal context (e.g., 2 messages)
                limit = 30 if is_session else 2
                history = self.history[history_key][-limit:]
            
            # Get response
            response = provider.get_response(prompt, history, context_id=history_key, location=location)
            return response
            
        except ValueError as e:
            logger.error(f"Provider error: {e}")
            return f"Error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error getting AI response: {e}")
            return f"Error: {str(e)}"
    
    # ==================== Message Sending ====================
    
    def send_response(self, text, from_node, to_node, channel, is_admin_cmd=False):
        """
        Send a response message via Meshtastic.
        
        Handles routing logic for admin commands and regular responses.
        
        Args:
            text: Response text to send
            from_node: Source node ID
            to_node: Destination node ID (or '^all' for broadcast)
            channel: Channel index
            is_admin_cmd: Whether this is an admin command response
        """
        # Determine destination
        if is_admin_cmd:
            # Admin commands always reply privately
            destination = from_node
        elif to_node == '^all':
            # Public message - reply publicly only if channel is enabled
            if self.is_channel_allowed(channel):
                destination = '^all'
            else:
                # Channel not enabled, don't respond
                logger.info(f"Skipping response on disabled channel {channel}")
                return
        else:
            # DM - reply privately
            destination = from_node
        
        # Get session indicator if applicable
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
                    "!ai -ch [ls/add/rm] : Channels\n"
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
                self.send_response("Usage: !ai -c rm <name/slot>", from_node, to_node, channel, is_admin_cmd=False)
                return
            identifier = parts[1]
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
        if not args:
            # List channels
            allowed = self.config.get('allowed_channels', [0])
            message = f"📡 Allowed Channels: {', '.join(map(str, allowed))}"
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return
        
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            self.send_response("Usage: !ai -ch add/rm <channel_id>", from_node, to_node, channel, is_admin_cmd=True)
            return
        
        action = parts[0].lower()
        channel_id = parts[1]
        
        if not channel_id.isdigit():
            self.send_response("Channel ID must be a number", from_node, to_node, channel, is_admin_cmd=True)
            return
        
        channel_id = int(channel_id)
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
    
    def _handle_ai_query(self, query, from_node, to_node, channel, initial_msg="Thinking... 🤖"):
        """
        Handle an AI query in a background thread.
        
        Args:
            query: User's question/prompt
            from_node: Source node ID
            to_node: Destination node ID
            channel: Channel index
            initial_msg: Initial "thinking" message to send
        """
        # Determine if this is a DM interaction
        # to_node == my node ID or is not a channel packet
        is_dm = (to_node != "^all" and channel == 0) # Simplification, improved in process_command
        
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
    
    def _process_ai_query_thread(self, query, from_node, to_node, channel, is_dm=False):
        """Background thread for processing AI queries."""
        try:
            # Short sleep to allow "Thinking..." message to clear if needed
            time.sleep(2)

            # 1. Get History Key and Session Status
            is_session = self.session_manager.is_active(from_node)
            history_key = self._get_history_key(from_node, channel, is_dm)
            
            # 2. Dual Metadata Injection Logic
            local_metadata = None  # Bot's own status
            remote_metadata = None  # User's environmental data
            
            if is_dm:
                history_exists = history_key in self.history and len(self.history[history_key]) > 0
                
                # Fetch LOCAL node metadata (bot's own status) on session start
                if not history_exists or (is_session and not history_exists):
                    try:
                        my_node_info = self.meshtastic.get_node_info()
                        if my_node_info:
                            my_node_id = my_node_info.get('user', {}).get('id')
                            if my_node_id:
                                local_metadata = self.meshtastic.get_node_metadata(my_node_id)
                                if local_metadata:
                                    logger.info(f"🤖 Bot metadata fetched: {local_metadata}")
                    except Exception as e:
                        logger.warning(f"Failed to fetch local node metadata: {e}")
                
                # Determine if we should inject REMOTE metadata (user's data)
                inject_remote_metadata = False
                if not history_exists:
                    inject_remote_metadata = True
                else:
                    # Check if specifically indicated for refresh
                    if getattr(self, '_refresh_metadata_nodes', set()) and from_node in self._refresh_metadata_nodes:
                        inject_remote_metadata = True
                        self._refresh_metadata_nodes.discard(from_node)
                    
                    # Check for context-related queries (location, battery, environment)
                    context_keywords = [
                        'location', 'where am i', 'gps', 'coords', 'coordinates', 'position', 'map',
                        'battery', 'voltage', 'power',
                        'temp', 'temperature', 'humidity', 'pressure', 'air', 'env',
                        'lux', 'light', 'brightness'
                    ]
                    if any(k in query.lower() for k in context_keywords):
                        logger.info(f"📍 Context query detected from {from_node}, injecting remote metadata.")
                        inject_remote_metadata = True
                
                # Fetch REMOTE node metadata (user's environmental data)
                if inject_remote_metadata:
                    # Trigger an asynchronous telemetry request to refresh our cache
                    # for future messages, while still fetching whatever we have now.
                    self.meshtastic.request_telemetry(from_node)
                    
                    remote_metadata = self.meshtastic.get_node_metadata(from_node)
            
            # Combine metadata with clear labels
            metadata = self._format_dual_metadata(local_metadata, remote_metadata)

            # 3. Add user message to history
            self.add_to_history(history_key, 'user', query, node_id=from_node, metadata=metadata)
            
            # Log what we are sending
            if metadata:
                logger.info(f"💾 Metadata Injected: {metadata}")
            
            current_session = self.session_manager.get_session_name(from_node)
            msgs_count = len(self.history.get(history_key, []))
            logger.info(f"🧠 AI Context: Session='{current_session or 'None'}' | Messages={msgs_count}")

            # 4. Extract Location for Grounding if available
            location = None
            try:
                node_info = self.meshtastic.interface.nodes.get(from_node)
                if node_info:
                    pos = node_info.get('position', {})
                    lat = pos.get('latitude')
                    lon = pos.get('longitude')
                    if lat is not None and lon is not None:
                        location = {'latitude': lat, 'longitude': lon}
                        logger.info(f"📍 Location identified for grounding: {lat}, {lon}")
            except Exception as e:
                logger.debug(f"Could not extract direct location for grounding: {e}")

            # 5. Get AI response with tuned context
            response = self.get_ai_response(query, history_key, is_session=is_session, location=location)
            
            # 5. Add assistant response to history
            self.add_to_history(history_key, 'assistant', response)
            
            # 6. Save to conversation if in session
            session_name = self.session_manager.get_session_name(from_node)
            if session_name:
                self.conversation_manager.save_conversation(from_node, session_name, self.history[history_key])
                self.session_manager.update_activity(from_node)
            
            # 7. Send response
            self.send_response(response, from_node, to_node, channel, is_admin_cmd=False)
            
        except Exception as e:
            logger.error(f"Error processing AI query: {e}")
            self.send_response(f"Error: {str(e)}", from_node, to_node, channel, is_admin_cmd=False)
    
    # ==================== Meshtastic Message Handler ====================
    
    def on_receive(self, packet, interface):
        """
        Callback for incoming Meshtastic messages.
        
        Args:
            packet: Meshtastic packet
            interface: Meshtastic interface instance
        """
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
            my_id = my_node_info.get('noId', '') if my_node_info else ''
            # In MeshPacket, 'toId' is our ID for DMs.
            # But sometimes ACKs/Routing packets confuse this. 
            # For TEXT_MESSAGE_APP:
            # if to_node == my_id or it's a DM session:
            is_dm = (to_node == my_id or (to_node != "^all" and channel == 0))

            # Check if user is in an active session
            if self.session_manager.is_active(from_node):
                # Check for timeout
                timed_out, message, session_channel, session_to_node = self.session_manager.check_timeout(from_node)
                if timed_out:
                    # Session timed out, send notification
                    self.send_response(message, from_node, session_to_node, session_channel, is_admin_cmd=False)
                    # Don't process the message as a session message
                else:
                    # Active session - process as AI query without !ai prefix (DMs only)
                    if not text.startswith('!ai'):
                        # Pass TRUE for is_dm because sessions are DM only
                        self._handle_ai_query(text, from_node, to_node, channel)
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
            while self.running:
                # 1. Connection Watchdog
                if not self.meshtastic.is_connected():
                    logger.warning("⚠️ Connection to Meshtastic lost/missing. Attempting to reconnect...")
                    try:
                        if self.meshtastic.connect(on_receive_callback=self.on_receive):
                            logger.info("✅ Reconnected to Meshtastic!")
                        else:
                            logger.error("❌ Reconnection attempt failed.")
                    except Exception as e:
                        logger.error(f"Error during reconnection: {e}")
                    
                    # Backoff before next loop iteration to avoid hammering
                    if not self.meshtastic.is_connected():
                        time.sleep(config.CONNECTION_RETRY_INTERVAL)
                        continue

                # 2. Daily Tasks / Periodic Checks
                time.sleep(1)
                
                # Periodic session timeout check
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
                    
                # Heartbeat for Docker healthcheck
                # Only update if actually connected
                if self.meshtastic.is_connected():
                    with open("/tmp/healthy", "w") as f:
                        f.write(str(time.time()))
                
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
