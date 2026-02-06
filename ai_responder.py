#!/usr/bin/env python3
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
    ENV_ADMIN_NODE_ID
)
from providers import get_provider
from conversation import ConversationManager, SessionManager
from meshtastic_handler import MeshtasticHandler

# Logging setup
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('AI-Responder')

# Force DEBUG for handler to trace ACKs
logging.getLogger('meshtastic_handler').setLevel(logging.DEBUG)


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
            admin_nodes = self.config.get('admin_nodes', [])
            if ENV_ADMIN_NODE_ID not in admin_nodes:
                admin_nodes.append(ENV_ADMIN_NODE_ID)
                self.config['admin_nodes'] = admin_nodes
                self.config.save()
                logger.info(f"Added admin node from environment: {ENV_ADMIN_NODE_ID}")
    
    # ==================== History Management ====================
    
    def _get_history_path(self, user_id):
        """
        Get the file path for a user's history.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            str: Absolute path to history file
        """
        return os.path.join(self.history_dir, f"{user_id}.json")
    
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
    
    def add_to_history(self, user_id, role, content):
        """
        Add a message to conversation history.
        
        Args:
            user_id: Unique identifier for the user
            role: 'user' or 'assistant'
            content: Message content
        """
        if user_id not in self.history:
            self.load_history(user_id)
        
        self.history[user_id].append({'role': role, 'content': content})
        self.save_history(user_id)
    
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
    
    def get_ai_response(self, prompt, user_id=None):
        """
        Get AI response using the configured provider.
        
        Args:
            prompt: User's input text
            user_id: Optional user ID for history context
            
        Returns:
            str: AI response or error message
        """
        provider_name = self.config.get('current_provider', 'ollama')
        
        try:
            # Get provider instance
            provider = get_provider(provider_name, self.config)
            
            # Get history for context
            history = None
            if user_id and user_id in self.history:
                history = self.history[user_id]
            
            # Get response
            response = provider.get_response(prompt, history)
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
        # Extract command and arguments
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            return
        
        cmd = parts[1].lower() if len(parts) > 1 else ''
        args = parts[2] if len(parts) > 2 else ''
        
        is_dm = (to_node != '^all')
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
                success, message, conv_name = self.session_manager.start_session(from_node, session_name)
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
                success, message = self.session_manager.end_session(from_node)
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
        if not args:
            # Load last conversation (most recently accessed)
            metadata = self.conversation_manager._load_metadata(from_node)
            if metadata:
                # Find most recent
                latest = max(metadata.items(), key=lambda x: x[1]['last_access'])
                success, message, history = self.conversation_manager.load_conversation(from_node, latest[0])
                if success and history:
                    self.history[from_node] = history
                    # If in DM, restart the session so they can continue chatting
                    if to_node != '^all':
                        self.session_manager.start_session(from_node)
                        message += "\n🟢 Session Started"
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
            success, message, history = self.conversation_manager.load_conversation(from_node, args)
            if success and history:
                self.history[from_node] = history
                # If in DM, restart the session so they can continue chatting
                if to_node != '^all':
                    self.session_manager.start_session(from_node)
                    message += "\n🟢 Session Started"
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
        # Send initial acknowledgment
        self.send_response(initial_msg, from_node, to_node, channel, is_admin_cmd=False)
        
        # Process in background thread
        thread = threading.Thread(
            target=self._process_ai_query_thread,
            args=(query, from_node, to_node, channel)
        )
        thread.daemon = True
        thread.start()
    
    def _process_ai_query_thread(self, query, from_node, to_node, channel):
        """Background thread for processing AI queries."""
        try:
            # Give the "Thinking..." message time to clear (standard DM rate limit)
            time.sleep(10)

            # Add user message to history
            self.add_to_history(from_node, 'user', query)
            
            # Get AI response
            response = self.get_ai_response(query, from_node)
            
            # Add assistant response to history
            self.add_to_history(from_node, 'assistant', response)
            
            # Save to conversation if in session
            session_name = self.session_manager.get_session_name(from_node)
            if session_name:
                self.conversation_manager.save_conversation(from_node, session_name, self.history[from_node])
                self.session_manager.update_activity(from_node)
            
            # Send response
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
            
            # Check if user is in an active session
            if self.session_manager.is_active(from_node):
                # Check for timeout
                if self.session_manager.check_timeout(from_node):
                    # Session timed out, send notification
                    success, message = self.session_manager.end_session(from_node, is_timeout=True)
                    self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                    # Don't process the message as a session message
                else:
                    # Active session - process as AI query without !ai prefix
                    if not text.startswith('!ai'):
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
        logger.info("🚀 Starting AI Responder...")
        
        # Connect to Meshtastic
        if not self.meshtastic.connect(on_receive_callback=self.on_receive):
            logger.error("Failed to connect to Meshtastic. Exiting.")
            return
        
        logger.info("✅ AI Responder is running. Press Ctrl+C to stop.")
        
        # Main loop
        try:
            while self.running:
                time.sleep(1)
                
                # Periodic session timeout check
                timed_out_users = self.session_manager.check_all_timeouts()
                for user_id in timed_out_users:
                    # Send timeout notification
                    success, message = self.session_manager.end_session(user_id, is_timeout=True)
                    # Note: We can't easily send the message here without knowing channel/to_node
                    # The session manager already handles the notification in check_timeout

                # Heartbeat for Docker healthcheck
                with open("/tmp/healthy", "w") as f:
                    f.write(str(time.time()))
                
        except KeyboardInterrupt:
            logger.info("\n👋 Shutting down AI Responder...")
        finally:
            self.meshtastic.disconnect()
            logger.info("✅ AI Responder stopped.")


if __name__ == "__main__":
    responder = AIResponder()
    responder.connect()
