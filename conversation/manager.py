"""
Conversation persistence manager.

This module handles saving, loading, listing, and deleting conversation history
for users. It implements a slot-based system with 10 user-managed slots plus
unlimited channel-specific slots.
"""

import os
import json
import gzip
import time
import logging
from datetime import datetime
import config

logger = logging.getLogger(__name__)


class ConversationManager:
    """
    Manages persistent conversation storage for users.
    
    Features:
    - Save/load conversations with compression
    - Slot-based system (10 user slots + unlimited channel slots)
    - Metadata tracking (creation time, last access)
    - Automatic naming for conversations
    """
    
    def __init__(self, conversations_dir=None):
        """
        Initialize conversation manager.
        
        Args:
            conversations_dir: Base directory for storing conversations
        """
        if conversations_dir is None:
            conversations_dir = config.CONVERSATIONS_DIR
        self.conversations_dir = conversations_dir
        self._ensure_directory_exists()
    
    def _ensure_directory_exists(self):
        """Create conversations directory if it doesn't exist."""
        if not os.path.exists(self.conversations_dir):
            os.makedirs(self.conversations_dir)
            logger.info(f"Created conversations directory: {self.conversations_dir}")
    
    def _get_user_dir(self, user_id):
        """
        Get the directory path for a specific user's conversations.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            str: Absolute path to user's conversation directory
        """
        user_dir = os.path.join(self.conversations_dir, user_id)
        if not os.path.exists(user_dir):
            os.makedirs(user_dir)
        return user_dir
    
    def _get_metadata_path(self, user_id):
        """
        Get path to user's conversation metadata file.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            str: Absolute path to metadata JSON file
        """
        return os.path.join(self._get_user_dir(user_id), 'metadata.json')
    
    def _load_metadata(self, user_id):
        """
        Load conversation metadata for a user.
        
        Metadata structure:
        {
            "conversation_name": {
                "index": int,           # Slot number (1-10 for user, 0 for channel)
                "created": float,       # Unix timestamp
                "last_access": float    # Unix timestamp
            }
        }
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            dict: Conversation metadata
        """
        metadata_path = self._get_metadata_path(user_id)
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load metadata for {user_id}: {e}")
        return {}
    
    def _save_metadata(self, user_id, metadata):
        """
        Save conversation metadata for a user.
        
        Args:
            user_id: Unique identifier for the user
            metadata: Dictionary of conversation metadata
        """
        metadata_path = self._get_metadata_path(user_id)
        try:
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save metadata for {user_id}: {e}")
    
    def _get_next_available_slot(self, metadata):
        """
        Find the next available slot number (1-10).
        
        Args:
            metadata: Current conversation metadata
            
        Returns:
            int or None: Next available slot number, or None if all slots full
        """
        # Filter out channel conversations (index 0)
        user_slots = {data['index'] for name, data in metadata.items() 
                     if not name.startswith('channel_')}
        
        # Find first available slot from 1-10
        for slot in range(1, config.MAX_CONVERSATIONS + 1):
            if slot not in user_slots:
                return slot
        return None
    
    def generate_conversation_name(self):
        """
        Generate a timestamped conversation name.
        
        Returns:
            str: Generated name in format "chat_YYYYMMDD_HHMMSS"
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"chat_{timestamp}"
    
    def save_conversation(self, user_id, conversation_name, history):
        """
        Save conversation history to a compressed file.
        
        Args:
            user_id: Unique identifier for the user
            conversation_name: Name for this conversation
            history: List of message dictionaries
            
        Returns:
            tuple: (success: bool, message: str)
        """
        if not history:
            return False, "No conversation history to save."
        
        metadata = self._load_metadata(user_id)
        
        # Check if conversation already exists
        if conversation_name in metadata:
            # Update existing conversation
            slot_index = metadata[conversation_name]['index']
        else:
            # Check if we have space for a new conversation (unless it's a channel)
            is_channel = conversation_name.startswith('channel_')
            if not is_channel and len([n for n in metadata if not n.startswith('channel_')]) >= config.MAX_CONVERSATIONS:
                return False, f"Maximum {config.MAX_CONVERSATIONS} conversations reached. Delete one first."
            
            # Get next available slot (0 for channels, 1-10 for user conversations)
            if is_channel:
                slot_index = 0
            else:
                slot_index = self._get_next_available_slot(metadata)
                if slot_index is None:
                    return False, "No available conversation slots."
        
        # Save conversation history as compressed JSON
        conv_path = os.path.join(self._get_user_dir(user_id), f"{conversation_name}.json.gz")
        try:
            with gzip.open(conv_path, 'wt', encoding='utf-8') as f:
                json.dump(history, f)
            
            # Update metadata
            metadata[conversation_name] = {
                'index': slot_index,
                'created': metadata.get(conversation_name, {}).get('created', time.time()),
                'last_access': time.time()
            }
            self._save_metadata(user_id, metadata)
            
            logger.info(f"Saved conversation '{conversation_name}' (slot {slot_index}) for {user_id}")
            return True, f"Conversation saved as '{conversation_name}' (slot {slot_index})"
        except Exception as e:
            logger.error(f"Failed to save conversation: {e}")
            return False, f"Failed to save: {str(e)}"
    
    def load_conversation(self, user_id, identifier):
        """
        Load a conversation by name or slot number.
        
        Args:
            user_id: Unique identifier for the user
            identifier: Conversation name or slot number (as string)
            
        Returns:
            tuple: (success: bool, message: str, history: list or None)
        """
        metadata = self._load_metadata(user_id)
        
        if not metadata:
            return False, "No saved conversations found.", None
        
        # Find conversation by name or index
        conversation_name = None
        if identifier.isdigit():
            # Search by slot number
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
            return False, f"Conversation '{identifier}' not found.", None
        
        # Load conversation history
        conv_path = os.path.join(self._get_user_dir(user_id), f"{conversation_name}.json.gz")
        try:
            with gzip.open(conv_path, 'rt', encoding='utf-8') as f:
                history = json.load(f)
            
            # Update last access time
            metadata[conversation_name]['last_access'] = time.time()
            self._save_metadata(user_id, metadata)
            
            slot_index = metadata[conversation_name]['index']
            logger.info(f"Loaded conversation '{conversation_name}' for {user_id}")
            return True, f"Loaded conversation '{conversation_name}' (slot {slot_index})", history
        except Exception as e:
            logger.error(f"Failed to load conversation: {e}")
            return False, f"Failed to load: {str(e)}", None
    
    def list_conversations(self, user_id, include_channels=False):
        """
        List all conversations for a user.
        
        Args:
            user_id: Unique identifier for the user
            include_channels: Whether to include channel conversations
            
        Returns:
            str: Formatted list of conversations
        """
        metadata = self._load_metadata(user_id)
        
        if not metadata:
            return "No saved conversations."
        
        # Filter conversations
        if include_channels:
            conversations = metadata
        else:
            conversations = {name: data for name, data in metadata.items() 
                           if not name.startswith('channel_')}
        
        if not conversations:
            return "No saved conversations."
        
        # Sort by slot index
        sorted_convs = sorted(conversations.items(), key=lambda x: x[1]['index'])
        
        # Format output
        lines = ["📚 Saved Conversations:"]
        for name, data in sorted_convs:
            last_access = datetime.fromtimestamp(data['last_access'])
            formatted_time = last_access.strftime("%Y-%m-%d %H:%M")
            lines.append(f"{data['index']}. {name} (last: {formatted_time})")
        
        return "\n".join(lines)
    
    def delete_conversation(self, user_id, identifier):
        """
        Delete a conversation by name or slot number.
        
        Args:
            user_id: Unique identifier for the user
            identifier: Conversation name or slot number (as string)
            
        Returns:
            tuple: (success: bool, message: str)
        """
        metadata = self._load_metadata(user_id)
        
        if not metadata:
            return False, "No saved conversations found."
        
        # Find conversation by name or index
        conversation_name = None
        if identifier.isdigit():
            # Search by slot number
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
        conv_path = os.path.join(self._get_user_dir(user_id), f"{conversation_name}.json.gz")
        try:
            if os.path.exists(conv_path):
                os.remove(conv_path)
            
            # Remove from metadata
            del metadata[conversation_name]
            self._save_metadata(user_id, metadata)
            
            logger.info(f"Deleted conversation '{conversation_name}' for {user_id}")
            return True, f"Deleted conversation '{conversation_name}'"
        except Exception as e:
            logger.error(f"Failed to delete conversation: {e}")
            return False, f"Failed to delete: {str(e)}"
    
    def get_channel_conversation_name(self, channel_index):
        """
        Generate a standardized name for channel conversations.
        
        Args:
            channel_index: Meshtastic channel index
            
        Returns:
            str: Channel conversation name
        """
        return f"channel_{channel_index}"
