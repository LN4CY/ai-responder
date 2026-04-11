# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

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
        Initialize conversation manager. (Disk persistence removed in favor of MemPalace)
        """
        self._mock_metadata = {}
    
    def generate_conversation_name(self):
        """Generate a timestamped conversation name."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"chat_{timestamp}"
    
    def _sanitize_name(self, name):
        """Sanitize conversation name."""
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '', name)
        if not safe_name:
            safe_name = "unnamed_session"
        return safe_name

    def save_conversation(self, user_id, conversation_name, history):
        """
        Deprecated. MemPalace autonomous memory manages persistence now.
        Always returns True to satisfy `ai_responder` internal logic.
        """
        if not history:
            return False, "No conversation history."
        return True, "History is now managed dynamically by MemPalace MCP."
    
    def load_conversation(self, user_id, identifier):
        """
        Deprecated. AI agents pull memory via MCP instead of loading full JSON arrays.
        """
        return False, "Loading full historical files is deprecated. AI automatically recalls necessary knowledge from MemPalace.", [], None
    
    def list_conversations(self, user_id, include_channels=False):
        """Deprecated list command."""
        return "📚 Manual conversation slots are deprecated. Context is fully autonomous via MemPalace."
    
    def delete_conversation(self, user_id, identifier):
        """Deprecated."""
        return False, "Deletion is now handled via AI interactions with MemPalace."

    def delete_all_conversations(self, user_id):
        """Deprecated."""
        return False, "Deletion is now handled via AI interactions with MemPalace."
    
    def get_channel_conversation_name(self, channel_index):
        """Generate a standardized name for channel conversations."""
        return f"channel_{channel_index}"
