# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""History and memory status management mixin for AI Responder."""

import os
import re
import json
from core._compat import get_config_module, get_logger


class HistoryMixin:
    """Mixin for conversation history persistence and memory status reporting."""

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
        logger = get_logger()
        with self._history_lock:
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
        logger = get_logger()
        config = get_config_module()
        with self._history_lock:
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
        logger = get_logger()
        with self._history_lock:
            self.history[user_id] = []
            self.save_history(user_id)
            logger.info(f"Cleared history for {user_id}")
    
    def _format_dual_metadata(self, local_metadata, remote_metadata, network=None):
        """
        Format dual metadata with clear labels for AI context.
        
        Args:
            local_metadata: Bot's own status metadata
            remote_metadata: User's environmental metadata
            network: Optional originating network ('meshtastic' or 'meshcore')
            
        Returns:
            str: Combined metadata string or None
        """
        parts = []
        if network:
            parts.append(f"[Network: {network.capitalize()}]")
        if remote_metadata:
            parts.append(f"[User: {remote_metadata}]")
        if local_metadata:
            # Try to get bot's own name for a descriptive label
            name = "Bot"
            try:
                handler = getattr(self, 'radio', None) or getattr(self, 'meshtastic', None)
                if handler:
                    my_info = handler.get_node_info()
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
        with self._history_lock:
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
        logger = get_logger()
        config = get_config_module()
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
        if self.mcp_client and self.mcp_client.has_server('memory'):
            try:
                status_raw = self.mcp_client.call_tool("memory_status", {})
                try:
                    status_dict = json.loads(status_raw) if isinstance(status_raw, str) else status_raw

                    triples = status_dict.get('kg_total_triples', status_dict.get('total_triples', '?'))
                    entities = status_dict.get('kg_total_entities', status_dict.get('total_entities', '?'))
                    size_bytes = status_dict.get('kg_size_bytes', status_dict.get('db_size_bytes', 0))

                    kg_status = status_dict.get('kg_status', status_dict.get('semantic_db', {}))
                    if isinstance(kg_status, dict) and kg_status:
                        triples = kg_status.get('total_triples', triples)
                        entities = kg_status.get('total_entities', entities)
                        size_bytes = kg_status.get('size_bytes', size_bytes)

                    size_str = f"{size_bytes / 1024:.1f}KB" if size_bytes else "0.0KB"

                    if triples == '?' and entities == '?':
                        active_hubs = len(self._mcp_indexing_cache)
                        semantic_status = f"Active ({active_hubs} local hubs cached, {size_str})"
                    else:
                        semantic_status = f"Active ({entities} entities, {triples} facts, {size_str})"
                except Exception as e:
                    logger.debug(f"Could not parse memory status JSON: {e}")
                    active_hubs = len(self._mcp_indexing_cache)
                    semantic_status = f"Active (Connected, {active_hubs} local hubs cached)"
            except Exception as e:
                logger.error(f"Failed to fetch memory status: {e}")
                semantic_status = "Active (Status Unavailable)"

        # 3. Provider Info
        provider = self.config.get('current_provider', 'ollama')
        
        # Format status message
        max_convs = getattr(config, 'MAX_CONVERSATIONS', 10)
        status = (
            f"💾 Memory Status\n"
            f"Local History: {message_count}/{config.HISTORY_MAX_MESSAGES} msgs\n"
            f"Disk Usage: {size_kb:.1f}KB/{max_kb:.0f}KB\n"
            f"Conv Slots: {slot_usage}/{max_convs}\n"
            f"Semantic DB: {semantic_status}\n"
            f"AI Provider: {provider.upper()}"
        )
        
        return status
