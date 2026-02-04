import os
import time
import json
import logging
import threading
import requests
import gzip
import shutil
from meshtastic.tcp_interface import TCPInterface
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

# Configuration Defaults
DEFAULT_MESHTASTIC_HOST = 'meshmonitor'
DEFAULT_MESHTASTIC_PORT = 4404
DEFAULT_AI_PROVIDER = 'ollama'
DEFAULT_OLLAMA_HOST = 'ollama'
DEFAULT_OLLAMA_PORT = '11434'
DEFAULT_OLLAMA_MODEL = 'llama3.2:1b'
ACK_TIMEOUT = 20 # Seconds to wait for radio/neighbor ACK
DEFAULT_ALLOWED_CHANNELS = [0, 3] # Default to DM and Private
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/app/data/config.json')
HISTORY_DIR = '/app/data/history'
CONVERSATIONS_DIR = '/app/data/conversations'
HISTORY_MAX_BYTES = int(os.environ.get('HISTORY_MAX_BYTES', 2 * 1024 * 1024)) # Default 2MB
HISTORY_MAX_MESSAGES = int(os.environ.get('HISTORY_MAX_MESSAGES', 1000)) # Default 1000
OLLAMA_MAX_MESSAGES = int(os.environ.get('OLLAMA_MAX_MESSAGES', 10)) # Max messages for Local context
MAX_CONVERSATIONS = 10 # Maximum saved conversations per user
SESSION_TIMEOUT = 300 # 5 minutes in seconds
SYSTEM_PROMPT_LOCAL_FILE = os.environ.get('SYSTEM_PROMPT_LOCAL_FILE', '/app/system_prompt_local.txt')
SYSTEM_PROMPT_ONLINE_FILE = os.environ.get('SYSTEM_PROMPT_ONLINE_FILE', '/app/system_prompt_online.txt')

# Environment Variables (Static config)
INTERFACE_TYPE = os.environ.get('INTERFACE_TYPE', 'tcp').lower()
SERIAL_PORT = os.environ.get('SERIAL_PORT', '/dev/ttyACM0')
MESHTASTIC_HOST = os.environ.get('MESHTASTIC_HOST', DEFAULT_MESHTASTIC_HOST)
MESHTASTIC_PORT = int(os.environ.get('MESHTASTIC_PORT', DEFAULT_MESHTASTIC_PORT))
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', DEFAULT_OLLAMA_HOST)
OLLAMA_PORT = os.environ.get('OLLAMA_PORT', DEFAULT_OLLAMA_PORT)
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', DEFAULT_OLLAMA_MODEL)
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ENV_ADMIN_NODE_ID = os.environ.get('ADMIN_NODE_ID', '')

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('AI-Responder')

# Default system prompts (fallback if files don't exist)
DEFAULT_SYSTEM_PROMPT_LOCAL = "You are a helpful AI assistant on a Meshtastic mesh network. You can answer general questions or help with mesh topics. Keep responses concise as bandwidth is limited."
DEFAULT_SYSTEM_PROMPT_ONLINE = "You are a helpful AI assistant on a Meshtastic mesh network. You can answer general questions or help with mesh topics. While bandwidth is limited, you can provide comprehensive responses when needed."

def load_system_prompt(provider):
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
                    return prompt
    except Exception as e:
        logger.warning(f"Failed to load system prompt from {prompt_file}: {e}")
    
    logger.info(f"Using default system prompt for {provider}")
    return default


class AIResponder:
    def __init__(self):
        self.iface = None
        self.running = True
        self.config = self.load_config()
        self.last_activity = time.time()
        self.last_probe = 0
        self.connection_lost = False
        self.history = {} # In-memory cache: {user_id: [{'role': 'user'/'assistant', 'content': '...'}]}
        self.active_sessions = {} # {user_id: {'name': str, 'index': int, 'started': float, 'last_activity': float}}
        
        # Ensure history directory exists
        if not os.path.exists(HISTORY_DIR):
            os.makedirs(HISTORY_DIR)
        
        # Ensure conversations directory exists
        if not os.path.exists(CONVERSATIONS_DIR):
            os.makedirs(CONVERSATIONS_DIR)

    def load_history(self, user_id):
        """Load history from disk (if exists) into memory."""
        file_path = os.path.join(HISTORY_DIR, f"{user_id}.json.gz")
        if os.path.exists(file_path):
            try:
                with gzip.open(file_path, 'rt', encoding='utf-8') as f:
                    self.history[user_id] = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load history for {user_id}: {e}")
                self.history[user_id] = []
        else:
            self.history[user_id] = []

    def save_history(self, user_id):
        """Save history to disk with compression and size limits."""
        if user_id not in self.history: return
        
        file_path = os.path.join(HISTORY_DIR, f"{user_id}.json.gz")
        try:
            # Write to temp file first
            tmp_path = file_path + ".tmp"
            with gzip.open(tmp_path, 'wt', encoding='utf-8') as f:
                json.dump(self.history[user_id], f)
            
            # Check size
            current_size = os.path.getsize(tmp_path)
            if current_size > HISTORY_MAX_BYTES:
                # Truncate older messages if too big
                logger.warning(f"History file for {user_id} ({current_size} bytes) exceeds limit ({HISTORY_MAX_BYTES}). Pruning...")
                # Reduce in-memory history by half and retry save
                keep_count = max(1, len(self.history[user_id]) // 2)
                self.history[user_id] = self.history[user_id][-keep_count:]
                
                # Retry save with reduced history
                with gzip.open(tmp_path, 'wt', encoding='utf-8') as f:
                    json.dump(self.history[user_id], f)

            os.replace(tmp_path, file_path)
        except Exception as e:
            logger.error(f"Failed to save history for {user_id}: {e}")

    def update_history(self, user_id, role, content):
        # Ensure loaded first
        if user_id not in self.history:
            self.load_history(user_id)
            
        self.history[user_id].append({'role': role, 'content': content})
        
        # Soft limit for online providers
        if len(self.history[user_id]) > HISTORY_MAX_MESSAGES:
            self.history[user_id] = self.history[user_id][-HISTORY_MAX_MESSAGES:]
            
        self.save_history(user_id)

    def get_memory_status(self, user_id):
        """Return memory usage stats for a user."""
        if user_id not in self.history:
            self.load_history(user_id)
        
        msg_count = len(self.history[user_id])
        provider = self.config.get('current_provider', 'ollama')
        
        if provider in ['local', 'ollama']:
            context_limit = OLLAMA_MAX_MESSAGES
            provider_label = "Ollama"
        else:
            context_limit = HISTORY_MAX_MESSAGES
            provider_label = "Online"
            
        active_context = min(msg_count, context_limit)
        
        # Storage stats
        file_path = os.path.join(HISTORY_DIR, f"{user_id}.json.gz")
        size_bytes = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        size_mb = size_bytes / (1024 * 1024)
        size_limit_mb = HISTORY_MAX_BYTES / (1024 * 1024)
        size_pct = (size_bytes / HISTORY_MAX_BYTES) * 100
        
        # Conversation slot usage
        metadata = self.load_conversation_metadata(user_id)
        user_convs = {name: data for name, data in metadata.items() if not name.startswith('channel_')}
        slots_used = len(user_convs)
        
        return f"🧠 Context ({provider_label}): {active_context}/{context_limit} (| History: {msg_count}) | 💾 Storage: {size_mb:.2f}/{size_limit_mb:.2f} MB ({size_pct:.1f}%) | 📚 Slots: {slots_used}/{MAX_CONVERSATIONS}"

    def clear_history(self, user_id):
        """Clear memory and disk history for a user."""
        self.history[user_id] = []
        file_path = os.path.join(HISTORY_DIR, f"{user_id}.json.gz")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Cleared history for {user_id}")
            except Exception as e:
                logger.error(f"Failed to delete history file for {user_id}: {e}")

    # ===== Conversation Management =====
    
    def get_user_conversations_dir(self, user_id):
        """Get the conversations directory for a user."""
        user_dir = os.path.join(CONVERSATIONS_DIR, user_id)
        if not os.path.exists(user_dir):
            os.makedirs(user_dir)
        return user_dir
    
    def get_conversation_metadata_path(self, user_id):
        """Get the metadata file path for a user's conversations."""
        return os.path.join(self.get_user_conversations_dir(user_id), 'metadata.json')
    
    def load_conversation_metadata(self, user_id):
        """Load conversation metadata for a user."""
        metadata_path = self.get_conversation_metadata_path(user_id)
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load conversation metadata for {user_id}: {e}")
        return {}
    
    def save_conversation_metadata(self, user_id, metadata):
        """Save conversation metadata for a user."""
        metadata_path = self.get_conversation_metadata_path(user_id)
        try:
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save conversation metadata for {user_id}: {e}")
    
    def generate_conversation_name(self):
        """Generate a unique conversation name based on timestamp."""
        return f"chat_{time.strftime('%Y%m%d_%H%M%S')}"
    
    def get_channel_conversation_name(self, channel_index):
        """Get the conversation name for a channel-specific slot."""
        # Try to get channel name from interface
        channel_name = f"Ch{channel_index}"
        if self.iface and self.iface.localNode:
            try:
                ch = self.iface.localNode.channels[channel_index]
                if ch and ch.settings and ch.settings.name:
                    channel_name = ch.settings.name
            except:
                pass
        return f"channel_{channel_name}"
    
    def get_next_available_index(self, metadata):
        """Get the next available conversation index (1-10)."""
        # Filter out channel conversations (they don't use numbered slots)
        user_convs = {name: data for name, data in metadata.items() if not name.startswith('channel_')}
        used_indices = {conv['index'] for conv in user_convs.values()}
        for i in range(1, MAX_CONVERSATIONS + 1):
            if i not in used_indices:
                return i
        return None
    
    def save_conversation(self, user_id, conversation_name, is_channel=False):
        """Save current history as a named conversation."""
        if user_id not in self.history:
            return False, "No conversation history to save."
        
        metadata = self.load_conversation_metadata(user_id)
        
        # Check if conversation already exists
        if conversation_name in metadata:
            # Update existing conversation
            index = metadata[conversation_name]['index']
        else:
            # Channel conversations get index 0 (reserved, doesn't count against limit)
            if is_channel:
                index = 0
            else:
                # Check if we have space for a new user conversation
                user_convs = {name: data for name, data in metadata.items() if not name.startswith('channel_')}
                if len(user_convs) >= MAX_CONVERSATIONS:
                    return False, f"Maximum {MAX_CONVERSATIONS} conversations reached. Delete one first."
                
                # Get next available index
                index = self.get_next_available_index(metadata)
                if index is None:
                    return False, "No available conversation slots."
        
        # Save conversation history
        conv_path = os.path.join(self.get_user_conversations_dir(user_id), f"{conversation_name}.json.gz")
        try:
            with gzip.open(conv_path, 'wt', encoding='utf-8') as f:
                json.dump(self.history[user_id], f)
            
            # Update metadata
            metadata[conversation_name] = {
                'index': index,
                'created': metadata.get(conversation_name, {}).get('created', time.time()),
                'last_access': time.time(),
                'is_channel': is_channel
            }
            self.save_conversation_metadata(user_id, metadata)
            
            if is_channel:
                logger.info(f"Saved channel conversation '{conversation_name}' for {user_id}")
            else:
                logger.info(f"Saved conversation '{conversation_name}' (index {index}) for {user_id}")
            return True, f"Conversation saved as '{conversation_name}' (slot {index})"
        except Exception as e:
            logger.error(f"Failed to save conversation: {e}")
            return False, f"Error saving conversation: {str(e)}"
    
    def load_conversation(self, user_id, identifier):
        """Load a conversation by name or index."""
        metadata = self.load_conversation_metadata(user_id)
        
        if not metadata:
            return False, "No saved conversations found."
        
        # Find conversation by name or index
        conversation_name = None
        if identifier.isdigit():
            # Search by index
            target_index = int(identifier)
            for name, data in metadata.items():
                if data['index'] == target_index:
                    conversation_name = name
                    break
        else:
            # Search by name
            if identifier in metadata:
                conversation_name = identifier
        
        if not conversation_name:
            return False, f"Conversation '{identifier}' not found."
        
        # Load conversation history
        conv_path = os.path.join(self.get_user_conversations_dir(user_id), f"{conversation_name}.json.gz")
        try:
            with gzip.open(conv_path, 'rt', encoding='utf-8') as f:
                self.history[user_id] = json.load(f)
            
            # Update last access time
            metadata[conversation_name]['last_access'] = time.time()
            self.save_conversation_metadata(user_id, metadata)
            
            logger.info(f"Loaded conversation '{conversation_name}' for {user_id}")
            return True, f"Loaded conversation '{conversation_name}' (slot {metadata[conversation_name]['index']})"
        except Exception as e:
            logger.error(f"Failed to load conversation: {e}")
            return False, f"Error loading conversation: {str(e)}"
    
    def list_conversations(self, user_id):
        """List all saved conversations for a user."""
        metadata = self.load_conversation_metadata(user_id)
        
        if not metadata:
            return "No saved conversations."
        
        # Filter out channel conversations
        user_convs = {name: data for name, data in metadata.items() if not name.startswith('channel_')}
        
        if not user_convs:
            return "No saved conversations."
        
        # Sort by index
        sorted_convs = sorted(user_convs.items(), key=lambda x: x[1]['index'])
        
        lines = ["📚 Saved Conversations:"]
        for name, data in sorted_convs:
            index = data['index']
            last_access = time.strftime('%Y-%m-%d %H:%M', time.localtime(data['last_access']))
            lines.append(f"{index}. {name} (last: {last_access})")
        
        return "\n".join(lines)
    
    def delete_conversation(self, user_id, identifier):
        """Delete a conversation by name or index."""
        metadata = self.load_conversation_metadata(user_id)
        
        if not metadata:
            return False, "No saved conversations found."
        
        # Find conversation by name or index
        conversation_name = None
        if identifier.isdigit():
            # Search by index
            target_index = int(identifier)
            for name, data in metadata.items():
                if data['index'] == target_index:
                    conversation_name = name
                    break
        else:
            # Search by name
            if identifier in metadata:
                conversation_name = identifier
        
        if not conversation_name:
            return False, f"Conversation '{identifier}' not found."
        
        # Delete conversation file
        conv_path = os.path.join(self.get_user_conversations_dir(user_id), f"{conversation_name}.json.gz")
        try:
            if os.path.exists(conv_path):
                os.remove(conv_path)
            
            # Remove from metadata
            index = metadata[conversation_name]['index']
            del metadata[conversation_name]
            self.save_conversation_metadata(user_id, metadata)
            
            logger.info(f"Deleted conversation '{conversation_name}' (index {index}) for {user_id}")
            return True, f"Deleted conversation '{conversation_name}' (slot {index})"
        except Exception as e:
            logger.error(f"Failed to delete conversation: {e}")
            return False, f"Error deleting conversation: {str(e)}"
    
    # ===== Session Management =====
    
    def start_session(self, user_id, conversation_name=None, channel_name=None):
        """Start a new AI session. Sessions can only occur in DMs."""
        # Sessions are DM-only - this should be enforced by caller
        # but we'll add a safety check here
        
        # Generate name if not provided
        if not conversation_name:
            conversation_name = self.generate_conversation_name()
        
        # Clear current history for new conversation
        self.clear_history(user_id)
        
        # Try to save as new conversation
        success, message = self.save_conversation(user_id, conversation_name)
        
        if not success:
            return False, message
        
        # Get the index from metadata
        metadata = self.load_conversation_metadata(user_id)
        index = metadata[conversation_name]['index']
        
        # Create session
        self.active_sessions[user_id] = {
            'name': conversation_name,
            'index': index,
            'started': time.time(),
            'last_activity': time.time()
        }
        
        logger.info(f"Started session '{conversation_name}' (slot {index}) for {user_id}")
        return True, f"🟢 Session started: '{conversation_name}' (slot {index})"
    
    def end_session(self, user_id, is_timeout=False):
        """End the current AI session."""
        if user_id not in self.active_sessions:
            return False, "No active session."
        
        session = self.active_sessions[user_id]
        conversation_name = session['name']
        
        # Save final state
        self.save_conversation(user_id, conversation_name)
        
        # Remove session
        del self.active_sessions[user_id]
        
        # Prepare message
        if is_timeout:
            message = f"⏱️ Session '{conversation_name}' ended (timeout after 5 minutes)."
        else:
            message = f"Session '{conversation_name}' ended."
        
        logger.info(f"Ended session '{conversation_name}' for {user_id} (timeout={is_timeout})")
        
        # Always send notification to user
        if self.iface:
            try:
                self.iface.sendText(message, destinationId=user_id, channelIndex=0)
            except Exception as e:
                logger.error(f"Failed to send session end notification: {e}")
        
        return True, message
    
    def check_session_timeout(self, user_id):
        """Check if a user's session has timed out."""
        if user_id not in self.active_sessions:
            return
        
        session = self.active_sessions[user_id]
        elapsed = time.time() - session['last_activity']
        
        if elapsed > SESSION_TIMEOUT:
            logger.info(f"Session timeout for {user_id} after {elapsed:.0f}s")
            # End session with timeout flag
            self.end_session(user_id, is_timeout=True)
    
    def update_session_activity(self, user_id):
        """Update the last activity time for a session."""
        if user_id in self.active_sessions:
            self.active_sessions[user_id]['last_activity'] = time.time()
    
    def is_in_session(self, user_id):
        """Check if a user is in an active session."""
        return user_id in self.active_sessions
    
    def get_session_indicator(self, user_id):
        """Get session indicator string for responses."""
        if user_id not in self.active_sessions:
            return ""
        
        session = self.active_sessions[user_id]
        return f"[🟢 {session['name']}] "


    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    # Migrate old config if necessary
                    if 'admin_nodes' not in config:
                        config['admin_nodes'] = []
                    logger.info(f"Loaded config: {config}")
                    return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
        
        # Default config
        admins = []
        default_config = {
            "current_provider": os.environ.get('AI_PROVIDER', DEFAULT_AI_PROVIDER).lower(),
            "allowed_channels": [int(c.strip()) for c in os.environ.get('ALLOWED_CHANNELS', '0,3').split(',') if c.strip().isdigit()],
            "admin_nodes": admins
        }
        logger.info(f"Using default config: {default_config}")
        return default_config

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Config saved successfully.")
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    def on_connection_established(self, interface, **kwargs):
        logger.info("Connection established event received.")
        self.last_activity = time.time()
        self.connection_lost = False

    def on_connection_lost(self, interface, **kwargs):
        logger.warning("Connection lost event received! Closing interface to force reconnect.")
        self.connection_lost = True
        if self.iface:
            try:
                self.iface = None
                # Interface closing might lag or hang, but setting iface to None breaks the main loop
                interface.close() 
            except: 
                pass

    def connect(self):
        # Subscribe to connection events
        pub.subscribe(self.on_connection_established, "meshtastic.connection.established")
        pub.subscribe(self.on_connection_lost, "meshtastic.connection.lost")

        while self.running:
            try:
                if INTERFACE_TYPE == 'serial':
                    logger.info(f"Connecting to Serial node at {SERIAL_PORT}...")
                    self.iface = SerialInterface(devPath=SERIAL_PORT)
                else:
                    logger.info(f"Connecting to TCP node at {MESHTASTIC_HOST}:{MESHTASTIC_PORT}...")
                    self.iface = TCPInterface(hostname=MESHTASTIC_HOST, portNumber=MESHTASTIC_PORT)
                
                logger.info(f"Connected successfully to {INTERFACE_TYPE} interface!")
                self.connection_lost = False
                self.last_activity = time.time()
                
                # Standard subscription
                pub.subscribe(self.on_receive, "meshtastic.receive")
                
                # Keep alive loop
                last_heartbeat = 0
                while self.iface and self.running:
                    current_time = time.time()
                    
                    # 1. Check for broken connection
                    if self.connection_lost:
                        logger.warning("Connection marked as lost. Skipping heartbeat.")
                        time.sleep(1)
                        continue

                    # 2. Activity Check
                    time_since_activity = current_time - self.last_activity
                    
                    # If silent for > 300s (5m), send active probe
                    if time_since_activity > 300:
                        if current_time - self.last_probe > 30: # Don't spam probes
                            logger.info(f"No activity for {int(time_since_activity)}s. Sending probe...")
                            try:
                                self.iface.sendPosition() # Lightweight keepalive
                                self.last_probe = current_time
                            except Exception as e:
                                logger.error(f"Failed to send probe: {e}")
                    
                    # 3. Heartbeat Update
                    # Only update heartbeat if we are active or within tolerance window (360s = 6m)
                    if time_since_activity < 360:
                        # Throttled update (every 10s)
                        if current_time - last_heartbeat > 10:
                            try:
                                with open('/tmp/healthy', 'w') as f:
                                    f.write(str(current_time))
                                last_heartbeat = current_time
                            except Exception as e:
                                logger.error(f"Failed to update heartbeat: {e}")
                    else:
                        logger.error(f"CRITICAL: No activity for {int(time_since_activity)}s! Stopping heartbeat to trigger restart.")
                        # Optionally remove the file to ensure immediate failure
                        try:
                             if os.path.exists('/tmp/healthy'):
                                 os.remove('/tmp/healthy')
                        except: pass
                    
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Connection error: {e}. Retrying in 5 seconds...")
                if self.iface:
                    try:
                        self.iface.close()
                    except:
                        pass
                self.iface = None
                time.sleep(5)

    def resolve_channel_input(self, input_val):
        if input_val.isdigit():
            return int(input_val)
        if self.iface and self.iface.localNode:
            for idx, ch in enumerate(self.iface.localNode.channels):
                if ch and ch.settings and ch.settings.name and ch.settings.name.lower() == input_val.lower():
                    return idx
        return None

    def on_receive(self, packet, interface):
        try:
            self.last_activity = time.time() # Update activity on ANY packet
            decoded = packet.get('decoded', {})
            message = decoded.get('text', '')
            from_node = packet.get('fromId')
            to_node = packet.get('toId')
            channel = packet.get('channel', 0)
            
            if not message:
                return

            # Check if channel is allowed (DMs are always allowed)
            if to_node == '^all' and channel not in self.config['allowed_channels']:
                return

            # Check for session timeout
            self.check_session_timeout(from_node)

            # Session Mode: If user is in session, treat all messages as AI queries
            if self.is_in_session(from_node):
                # Update session activity
                self.update_session_activity(from_node)
                
                # Check if it's a command to end session
                if message.strip() == '!ai -end':
                    self.process_command(message, from_node, to_node, channel)
                # Check if it's any other !ai command
                elif message.startswith('!ai '):
                    self.process_command(message, from_node, to_node, channel)
                else:
                    # Treat as AI query
                    session = self.active_sessions[from_node]
                    threading.Thread(target=self.handle_ai_request, 
                                   args=(from_node, to_node, channel, message),
                                   kwargs={'in_session': True}).start()
                return

            # Command Processing (not in session)
            if message.startswith('!ai '):
                self.process_command(message, from_node, to_node, channel)

        except Exception as e:
            logger.error(f"Error processing packet: {e}")

    def is_admin(self, node_id):
        # Bootstrap mode: If no admins configured, everyone is admin
        if not self.config.get('admin_nodes'):
            return True
        return node_id in self.config['admin_nodes']

    def split_message(self, text, limit=200):
        """Splits a message into chunks within the limit, trying to break at words."""
        chunks = []
        while len(text) > limit:
            # Find the last space before the limit
            split_idx = text.rfind(' ', 0, limit)
            if split_idx == -1:
                # No space found, hard split
                split_idx = limit
            
            chunks.append(text[:split_idx].strip())
            text = text[split_idx:].strip()
        
        if text:
            chunks.append(text)
        return chunks

    def process_command(self, message, from_node, to_node, channel):
        args = message.split()
        if len(args) < 2: 
            return
        cmd = args[1]
        
        # !ai -h (Help)
        if cmd == '-h':
            is_admin = from_node in self.config.get('admin_nodes', [])
            is_dm = (to_node != '^all')
            
            # Message 1: Basic Commands (for everyone)
            basic_help = (
                "🤖 AI Responder - Basic Commands\n\n"
                "!ai <query> : Ask the AI a question\n"
                "!ai -m : Show memory & slot usage\n"
                "!ai -h : Show this help"
            )
            self.send_response(basic_help, from_node, to_node, channel, is_admin_cmd=False)
            time.sleep(2)  # Delay between messages
            
            # Message 2: Session Commands (DM only - only show in DMs)
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
                    "📚 Conversation Management\n\n"
                    "!ai -c : Recall last conversation\n"
                    "!ai -c <name/slot> : Recall specific\n"
                    "!ai -c ls : List all (max 10)\n"
                    "!ai -c rm <name/slot> : Delete one"
                )
            else:
                # Channel mode - show channel-specific help
                conv_help = (
                    "📚 Conversation Management\n\n"
                    "!ai -c : Recall last conversation\n"
                    "!ai -c <name/slot> : Recall specific\n"
                    "!ai -c ls : List all (max 10)\n"
                    "!ai -c rm <name/slot> : Delete one\n\n"
                    "Channel Mode:\n"
                    "!ai -n <query> : Clear & ask new"
                )
            self.send_response(conv_help, from_node, to_node, channel, is_admin_cmd=False)
            
            # Message 4: Admin Commands
            if is_admin:
                time.sleep(2)
                if is_dm:
                    # Full admin help in DM
                    admin_help = (
                        "⚙️ Admin Commands (DM Only)\n\n"
                        "!ai -p : List providers\n"
                        "!ai -p <name> : Switch provider\n"
                        "  • local, gemini, openai, anthropic\n"
                        "!ai -ch : List channels\n"
                        "!ai -ch add/rm <id/name>\n"
                        "!ai -a : List admins\n"
                        "!ai -a add/rm <node_id>"
                    )
                else:
                    # Hint in channel
                    admin_help = (
                        "⚙️ Admin Note\n\n"
                        "Send !ai -h in DM for admin commands."
                    )
                self.send_response(admin_help, from_node, to_node, channel, is_admin_cmd=False)
            
            return

        # !ai -m (Memory Status)
        if cmd == '-m':
            self.handle_memory_cmd(from_node, to_node, channel)
            return

        # !ai -n [name] (Start New Session in DM or Clear Channel Conversation)
        if cmd == '-n':
            # Check if this is a DM or channel message
            is_dm = (to_node != '^all')
            
            if is_dm:
                # DM Mode: Start a session
                conversation_name = None
                if len(args) > 2:
                    # Extract conversation name from the rest of the message
                    conversation_name = ' '.join(args[2:]).strip()
                
                success, message = self.start_session(from_node, conversation_name)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            else:
                # Channel Mode: Clear channel conversation and process query
                # Get the query (everything after !ai -n)
                query = ""
                if len(args) > 2:
                    query = ' '.join(args[2:]).strip()
                
                if not query:
                    self.send_response("❌ Usage in channel: !ai -n <query>", from_node, to_node, channel, is_admin_cmd=False)
                    return
                
                # Clear the channel-specific conversation
                channel_conv_name = self.get_channel_conversation_name(channel)
                self.clear_history(from_node)
                
                # Process the query as a new conversation
                threading.Thread(target=self.handle_ai_request, 
                               args=(from_node, to_node, channel, query),
                               kwargs={'initial_msg': "Thinking (New Conversation)... 🤖"}).start()
            return
        
        # !ai -end (End Session - DM only)
        if cmd == '-end':
            if to_node != '^all':
                success, message = self.end_session(from_node)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            else:
                self.send_response("❌ Sessions are only available in DMs.", from_node, to_node, channel, is_admin_cmd=False)
            return

        # Check Admin for ALL other commands (starting with -)
        # Admin commands: -p, -ch, -a
        admin_only_commands = ['-p', '-ch', '-a']
        
        if cmd in admin_only_commands:
            # Check if user is admin
            if not self.is_admin(from_node):
                self.send_response("⛔ Unauthorized: Admin only.", from_node, to_node, channel, is_admin_cmd=True)
                return
            
            # Admin commands are DM only
            if to_node == '^all':
                self.send_response("⚙️ Admin commands are DM only. Please send this command in a direct message.", from_node, to_node, channel, is_admin_cmd=True)
                return

        # !ai -p [provider]
        if cmd == '-p':
            if len(args) == 2:
                current = self.config['current_provider']
                providers = ['ollama', 'gemini', 'openai', 'anthropic']
                msg = ["Providers:"]
                for p in providers:
                    status = "✅" if p == current else "❌"
                    msg.append(f"{status} {p}")
                self.send_response(", ".join(msg), from_node, to_node, channel, is_admin_cmd=True)
            else:
                new_provider = args[2].lower()
                if new_provider in ['online', 'gemini', 'openai', 'anthropic']:
                    self.config['current_provider'] = new_provider
                    self.save_config()
                    self.send_response(f"✅ Switched to ONLINE provider ({new_provider}).", from_node, to_node, channel, is_admin_cmd=True)
                elif new_provider in ['local', 'ollama']:
                    self.config['current_provider'] = 'ollama'
                    self.save_config()
                    self.send_response("✅ Switched to LOCAL provider (Ollama).", from_node, to_node, channel, is_admin_cmd=True)
                else:
                    self.send_response("❌ Unknown provider. Use 'local', 'gemini', 'openai', or 'anthropic'.", from_node, to_node, channel, is_admin_cmd=True)

        # !ai -c (Conversation Management)
        elif cmd == '-c':
            if len(args) == 2:
                # No subcommand - try to recall last conversation
                metadata = self.load_conversation_metadata(from_node)
                if not metadata:
                    self.send_response("No saved conversations.", from_node, to_node, channel, is_admin_cmd=False)
                    return
                
                # Find most recently accessed conversation
                sorted_convs = sorted(metadata.items(), key=lambda x: x[1]['last_access'], reverse=True)
                if sorted_convs:
                    conversation_name = sorted_convs[0][0]
                    success, message = self.load_conversation(from_node, conversation_name)
                    self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                return
            
            action = args[2]
            
            # !ai -c ls (List conversations)
            if action == 'ls':
                message = self.list_conversations(from_node)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                return
            
            # !ai -c rm <name/index> (Delete conversation)
            if action == 'rm':
                if len(args) < 4:
                    self.send_response("❌ Usage: !ai -c rm <name/index>", from_node, to_node, channel, is_admin_cmd=False)
                    return
                identifier = ' '.join(args[3:]).strip()
                success, message = self.delete_conversation(from_node, identifier)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                return
            
            # !ai -c <name/index> (Recall conversation)
            identifier = ' '.join(args[2:]).strip()
            success, message = self.load_conversation(from_node, identifier)
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            return

        # !ai -ch (Channel Management - Admin only)
        elif cmd == '-ch':
            if not self.is_admin(from_node):
                self.send_response("⛔ Unauthorized: Admin only.", from_node, to_node, channel, is_admin_cmd=True)
                return
                
            if len(args) == 2:
                msg = ["Channels:"]
                if self.iface and self.iface.localNode:
                    for idx, ch in enumerate(self.iface.localNode.channels):
                        try:
                            if ch.role == 0: continue # Skip if no role
                            name = ch.settings.name if ch.settings.name else f"Ch{idx}"
                            status = "✅" if idx in self.config['allowed_channels'] else "❌"
                            msg.append(f"{status} {idx}:{name}")
                        except: pass
                self.send_response(", ".join(msg), from_node, to_node, channel, is_admin_cmd=True)
            else:
                action = args[2]
                if len(args) < 4:
                    self.send_response("❌ Usage: !ai -ch add|rm <id/name>", from_node, to_node, channel, is_admin_cmd=True)
                    return
                target_idx = self.resolve_channel_input(args[3])
                if target_idx is None:
                    self.send_response(f"❌ Could not find channel '{args[3]}'", from_node, to_node, channel, is_admin_cmd=True)
                    return
                if action == 'add' or action == '-add':
                    if target_idx not in self.config['allowed_channels']:
                        self.config['allowed_channels'].append(target_idx)
                        self.save_config()
                        self.send_response(f"✅ Added Channel {target_idx}", from_node, to_node, channel, is_admin_cmd=True)
                elif action == 'rm' or action == '-rem' or action == '-rm':
                    if target_idx in self.config['allowed_channels']:
                        self.config['allowed_channels'].remove(target_idx)
                        self.save_config()
                        self.send_response(f"✅ Removed Channel {target_idx}", from_node, to_node, channel, is_admin_cmd=True)

        # !ai -a [-add/-rem]
        elif cmd == '-a':
            if len(args) == 2:
                admins = self.config.get('admin_nodes', [])
                if not admins:
                    # Specific note already here, but using the flag for consistency
                    self.send_response("⚠️ No admins configured (Bootstrap Mode).", from_node, to_node, channel, is_admin_cmd=True)
                else:
                    self.send_response(f"Admins: {', '.join(admins)}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                action = args[2]
                if len(args) < 4:
                    self.send_response("❌ Usage: !ai -a add|rm <node_id>", from_node, to_node, channel, is_admin_cmd=True)
                    return
                target_id = args[3].strip()
                if target_id.lower() == 'me':
                    target_id = from_node
                
                if action == 'add' or action == '-add':
                    if target_id not in self.config['admin_nodes']:
                        self.config['admin_nodes'].append(target_id)
                        self.save_config()
                        self.send_response(f"✅ Added Admin {target_id}", from_node, to_node, channel, is_admin_cmd=True)
                elif action == 'rm' or action == '-rem' or action == '-rm':
                    if target_id in self.config['admin_nodes']:
                        self.config['admin_nodes'].remove(target_id)
                        self.save_config()
                        self.send_response(f"✅ Removed Admin {target_id}", from_node, to_node, channel, is_admin_cmd=True)

        else:
            prompt = message[4:].strip()
            # Run AI request in a separate thread to avoid blocking the radio interface
            threading.Thread(target=self.handle_ai_request, args=(from_node, to_node, channel, prompt)).start()

    def handle_memory_cmd(self, from_node, to_node, channel):
        """Handle !ai -m command."""
        status = self.get_memory_status(from_node)
        self.send_response(status, from_node, to_node, channel, is_admin_cmd=False)

    def send_response(self, text, from_node, to_node, channel, is_admin_cmd=False):
        """
        Send a response message.
        If dm -> reply dm.
        If broadcast -> broadcast.
        If broadcast AND admin command -> reply dm (to reduce spam).
        """
        target = '^all'
        
        # If it's an admin command (or error), and it came from a specific user, reply privately
        # even if they shouted it to the channel.
        if is_admin_cmd and from_node.startswith('!'):
            target = from_node
        # Normal logic: reply to sender if they DM'd us
        elif to_node != '^all':
            target = from_node

        logger.info(f"Sending response to {target}: {text[:50]}...")
        try:
            self.iface.sendText(text, destinationId=target, channelIndex=channel)
        except Exception as e:
            logger.error(f"Failed to send response: {e}")

    def handle_ai_request(self, from_node, to_node, channel, prompt, initial_msg="Thinking... 🤖", in_session=False):
        target = '^all' if to_node == '^all' else from_node
        logger.info(f"Processing AI request from {from_node} (reply to {target}): {prompt[:50]}...")
        
        # Get session indicator if in session
        session_indicator = self.get_session_indicator(from_node) if in_session else ""
        
        self.update_history(from_node, 'user', prompt)

        try:
            self.iface.sendText(f"{session_indicator}{initial_msg}", destinationId=target, channelIndex=channel)
            time.sleep(2) # Give the radio time to send the first packet
        except Exception as e:
            logger.error(f"Failed to send acknowledgment: {e}")
        
        response_text = self.get_ai_response(prompt, from_node)
        
        self.update_history(from_node, 'assistant', response_text)

        # Save conversation state
        if in_session and from_node in self.active_sessions:
            # Session mode: save to session conversation
            session = self.active_sessions[from_node]
            self.save_conversation(from_node, session['name'])
        elif to_node == '^all':
            # Channel mode: auto-save to channel-specific slot
            channel_conv_name = self.get_channel_conversation_name(channel)
            self.save_conversation(from_node, channel_conv_name, is_channel=True)

        # Clean up common AI markdown that might be problematic or waste space
        response_text = response_text.replace('**', '')
        
        logger.info(f"Generated AI response: {response_text[:100]}...")
        
        chunks = self.split_message(response_text)
        total_chunks = len(chunks)
        
        for i, chunk in enumerate(chunks):
            if i > 0:
                # Dynamic rate limiting: 5s for DM, 15s for broadcast
                delay = 15 if target == '^all' else 5
                logger.info(f"Rate limiting: Waiting {delay}s before sending chunk {i+1}/{total_chunks}...")
                time.sleep(delay)
            
            try:
                display_chunk = chunk
                if total_chunks > 1:
                    display_chunk = f"[{i+1}/{total_chunks}] {chunk}"
                
                # Add session indicator to each chunk
                display_chunk = f"{session_indicator}{display_chunk}"
                
                p = self.iface.sendText(display_chunk, destinationId=target, channelIndex=channel)
                pkt_id = p.get('id') if isinstance(p, dict) else 'unknown'
                logger.info(f"Chunk {i+1}/{total_chunks} queued (ID: {pkt_id}). Waiting for ACK...")
                
                # If it's a broadcast (^all), we won't get a direct ACK, so just wait a bit
                if target == '^all':
                    logger.info("Broadcast sent. Skipping ACK wait.")
                else:
                    try:
                        # Wait for acknowledgment from the next hop or destination
                        if hasattr(p, 'wait_for_ack'):
                            if p.wait_for_ack(timeout=ACK_TIMEOUT):
                                logger.info(f"✅ Received ACK for chunk {i+1}")
                            else:
                                logger.warning(f"⚠️ ACK timeout for chunk {i+1} (Queue might be full/congested)")
                    except Exception as ack_err:
                        logger.warning(f"Error waiting for ACK: {ack_err}")
            except Exception as e:
                logger.error(f"Failed to send chunk {i+1}: {e}")

    def get_ai_response(self, prompt, user_id=None):
        provider = self.config.get('current_provider', 'ollama')
        if provider == 'ollama':
            return self.get_ollama_response(prompt, user_id)
        elif provider == 'gemini':
            return self.get_gemini_response(prompt, user_id)
        elif provider == 'openai':
            return self.get_openai_response(prompt, user_id)
        elif provider == 'anthropic':
            return self.get_anthropic_response(prompt, user_id)
        return f"Error: Unknown provider '{provider}'" # Fixed missing quotes earlier?

    def get_ollama_response(self, prompt, user_id):
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
        
        system_prompt = load_system_prompt('ollama')
        messages = [{'role': 'system', 'content': system_prompt}]
        if user_id and user_id in self.history:
             # Dynamically limit context based on configuration
             messages.extend(self.history[user_id][-OLLAMA_MAX_MESSAGES:])
        else:
             messages.append({'role': 'user', 'content': prompt})

        payload = {
            "model": OLLAMA_MODEL, 
            "messages": messages, 
            "stream": False
        }
        try:
            response = requests.post(url, json=payload, timeout=300)
            response.raise_for_status()
            return response.json().get('message', {}).get('content', 'No response.')
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return f"Error calling Ollama: {str(e)}"

    def get_gemini_response(self, prompt, user_id):
        if not GEMINI_API_KEY: return "Error: Gemini API key missing."
        
        system_prompt = load_system_prompt('gemini')
        contents = []
        if user_id and user_id in self.history:
             for msg in self.history[user_id]:
                  role = 'model' if msg['role'] == 'assistant' else 'user'
                  contents.append({'role': role, 'parts': [{'text': msg['content']}]})
        else:
             contents.append({'role': 'user', 'parts': [{'text': prompt}]})
        
        # Inject System Prompt
        if contents and contents[0]['role'] == 'user':
             contents[0]['parts'][0]['text'] = f"{system_prompt}\n\n{contents[0]['parts'][0]['text']}"
        elif contents:
             contents.insert(0, {'role': 'user', 'parts': [{'text': system_prompt}]})

        models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.5-pro', 'gemini-2.0-pro-exp']
        payload = {"contents": contents}
        last_err = ""
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
            try:
                logger.info(f"Trying Gemini model: {model}")
                response = requests.post(url, json=payload, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get('candidates', [])
                    if not candidates:
                        logger.error(f"Gemini returned 200 but no candidates: {data}")
                        last_err = "No candidates in response"
                        continue
                    text = candidates[0]['content']['parts'][0]['text'].strip()
                    return text
                else:
                    try:
                        err_msg = response.json().get('error', {}).get('message', response.text)
                    except:
                        err_msg = response.text
                    logger.error(f"Gemini error from {model}: {response.status_code} - {err_msg}")
                    last_err = f"{response.status_code} ({err_msg[:50]})"
            except Exception as e:
                logger.error(f"Gemini connection error: {e}")
                last_err = "Connection Error"
        return f"Gemini Error: {last_err}. Check API Key."

    def get_openai_response(self, prompt, user_id):
        if not OPENAI_API_KEY: return "Error: OpenAI API key missing."
        url = 'https://api.openai.com/v1/chat/completions'
        
        system_prompt = load_system_prompt('openai')
        messages = [{'role': 'system', 'content': system_prompt}]
        if user_id and user_id in self.history:
            messages.extend(self.history[user_id])
        else:
            messages.append({'role': 'user', 'content': prompt})

        payload = {
            'model': 'gpt-3.5-turbo',
            'messages': messages,
            'max_tokens': 150
        }
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}'
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            else:
                logger.error(f"OpenAI error: {response.status_code} - {response.text}")
                return f"OpenAI Error: {response.status_code}"
        except Exception as e:
            logger.error(f"OpenAI connection error: {e}")
            return f"Error calling OpenAI: {str(e)}"

    def get_anthropic_response(self, prompt, user_id):
        if not ANTHROPIC_API_KEY: return "Error: Anthropic API key missing."
        url = 'https://api.anthropic.com/v1/messages'
        
        system_prompt = load_system_prompt('anthropic')
        messages = []
        if user_id and user_id in self.history:
            # Anthropic expects list of {role, content} where roles alternate
            messages = self.history[user_id]
        else:
            messages = [{'role': 'user', 'content': prompt}]

        payload = {
            'model': 'claude-3-haiku-20240307',
            'max_tokens': 150,
            'system': system_prompt,
            'messages': messages
        }
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01'
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get('content', [{}])[0].get('text', '').strip()
            else:
                logger.error(f"Anthropic error: {response.status_code} - {response.text}")
                return f"Anthropic Error: {response.status_code}"
        except Exception as e:
            logger.error(f"Anthropic connection error: {e}")
            return f"Error calling Anthropic: {str(e)}"

if __name__ == "__main__":
    responder = AIResponder()
    responder.connect()
