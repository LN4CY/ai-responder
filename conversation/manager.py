# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""
Conversation persistence manager.

This module handles saving, loading, listing, and deleting conversation history
for users. It implements a slot-based system with 10 user-managed slots plus
unlimited channel-specific slots.

When MemPalace (an external MCP memory server) is connected, disk persistence
is bypassed in favour of it. When MemPalace is NOT available, this manager
falls back to local compressed JSON files (.json.gz) so that named sessions
and conversation history survive bot restarts.
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
    - Save/load conversations with compression (.json.gz)
    - Slot-based system (10 user slots + unlimited channel slots)
    - Metadata tracking (creation time, last access)
    - Automatic naming for conversations
    - Graceful fallback when MemPalace MCP is not connected
    """

    def __init__(self, conversations_dir=None):
        self.conversations_dir = conversations_dir or config.CONVERSATIONS_DIR
        os.makedirs(self.conversations_dir, exist_ok=True)
        logger.info(f"ConversationManager initialised (disk fallback at '{self.conversations_dir}')")

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _get_user_dir(self, user_id):
        """Return (and create) the per-user directory."""
        user_dir = os.path.join(self.conversations_dir, user_id)
        os.makedirs(user_dir, exist_ok=True)
        return user_dir

    def _metadata_path(self, user_id):
        return os.path.join(self._get_user_dir(user_id), 'metadata.json')

    def _load_metadata(self, user_id):
        path = self._metadata_path(user_id)
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load metadata for {user_id}: {e}")
        return {}

    def _save_metadata(self, user_id, metadata):
        try:
            with open(self._metadata_path(user_id), 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save metadata for {user_id}: {e}")

    def _next_slot(self, metadata):
        """Return the next free slot index (1-10), or None if all full."""
        used = {data['index'] for name, data in metadata.items()
                if not name.startswith('channel_')}
        for slot in range(1, config.MAX_CONVERSATIONS + 1):
            if slot not in used:
                return slot
        return None

    def _sanitize_name(self, name):
        """Strip any characters that are not alphanumeric, hyphen, or underscore."""
        import re
        safe = re.sub(r'[^a-zA-Z0-9_\-]', '', name)
        return safe if safe else 'unnamed_session'

    def generate_conversation_name(self):
        """Generate a timestamped conversation name."""
        return f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def get_channel_conversation_name(self, channel_index):
        return f"channel_{channel_index}"

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def save_conversation(self, user_id, conversation_name, history):
        """
        Persist a conversation to disk (compressed JSON).

        Returns:
            tuple: (success: bool, message: str)
        """
        if not history:
            return False, "No conversation history to save."

        conversation_name = self._sanitize_name(conversation_name)
        metadata = self._load_metadata(user_id)

        # Determine slot
        if conversation_name in metadata:
            slot_index = metadata[conversation_name]['index']
        elif conversation_name.startswith('channel_'):
            slot_index = 0
        else:
            slot_index = self._next_slot(metadata)
            if slot_index is None:
                return False, (f"Maximum {config.MAX_CONVERSATIONS} conversations reached. "
                               "Delete one first.")

        # Write compressed history
        conv_path = os.path.join(self._get_user_dir(user_id), f"{conversation_name}.json.gz")
        try:
            with gzip.open(conv_path, 'wt', encoding='utf-8') as f:
                json.dump(history, f)
        except Exception as e:
            logger.error(f"Failed to save conversation: {e}")
            return False, f"Failed to save: {e}"

        # Update metadata
        now = time.time()
        metadata[conversation_name] = {
            'index': slot_index,
            'created': metadata.get(conversation_name, {}).get('created', now),
            'last_access': now,
        }
        self._save_metadata(user_id, metadata)
        logger.info(f"Saved '{conversation_name}' (slot {slot_index}) for {user_id}")
        return True, f"Saved conversation '{conversation_name}' (slot {slot_index})"

    def load_conversation(self, user_id, identifier):
        """
        Load a conversation by name or slot number.

        Returns:
            tuple: (success: bool, message: str, history: list|None, name: str|None)
        """
        metadata = self._load_metadata(user_id)
        if not metadata:
            return False, "No saved conversations found.", None, None

        # Resolve identifier → name
        conversation_name = None
        if identifier.isdigit():
            target = int(identifier)
            for name, data in metadata.items():
                if data['index'] == target:
                    conversation_name = name
                    break
        elif identifier in metadata:
            conversation_name = identifier

        if not conversation_name:
            return False, f"Conversation '{identifier}' not found.", None, None

        conv_path = os.path.join(self._get_user_dir(user_id), f"{conversation_name}.json.gz")
        try:
            with gzip.open(conv_path, 'rt', encoding='utf-8') as f:
                history = json.load(f)
            metadata[conversation_name]['last_access'] = time.time()
            self._save_metadata(user_id, metadata)
            slot = metadata[conversation_name]['index']
            logger.info(f"Loaded '{conversation_name}' (slot {slot}) for {user_id}")
            return True, f"Loaded conversation '{conversation_name}' (slot {slot})", history, conversation_name
        except Exception as e:
            logger.error(f"Failed to load conversation: {e}")
            return False, f"Failed to load: {e}", None, None

    def list_conversations(self, user_id, include_channels=False):
        """
        Return a formatted string listing saved conversations.
        """
        metadata = self._load_metadata(user_id)
        if not metadata:
            return "No saved conversations."

        convs = metadata if include_channels else {
            name: data for name, data in metadata.items()
            if not name.startswith('channel_')
        }

        if not convs:
            return "No saved conversations."

        sorted_convs = sorted(convs.items(), key=lambda x: x[1]['index'])
        lines = ["📚 Saved Conversations:"]
        for name, data in sorted_convs:
            last = datetime.fromtimestamp(data['last_access']).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{data['index']}. {name} (last: {last})")
        return "\n".join(lines)

    def delete_conversation(self, user_id, identifier):
        """
        Delete a conversation by name or slot number.

        Returns:
            tuple: (success: bool, message: str)
        """
        metadata = self._load_metadata(user_id)
        if not metadata:
            return False, "No saved conversations found."

        conversation_name = None
        if identifier.isdigit():
            target = int(identifier)
            for name, data in metadata.items():
                if data['index'] == target:
                    conversation_name = name
                    break
        elif identifier in metadata:
            conversation_name = identifier

        if not conversation_name:
            return False, f"Conversation '{identifier}' not found."

        conv_path = os.path.join(self._get_user_dir(user_id), f"{conversation_name}.json.gz")
        try:
            if os.path.exists(conv_path):
                os.remove(conv_path)
            del metadata[conversation_name]
            self._save_metadata(user_id, metadata)
            logger.info(f"Deleted '{conversation_name}' for {user_id}")
            return True, f"Deleted conversation '{conversation_name}'"
        except Exception as e:
            logger.error(f"Failed to delete conversation: {e}")
            return False, f"Failed to delete: {e}"

    def delete_all_conversations(self, user_id):
        """
        Delete ALL conversations for a specific user.

        Returns:
            tuple: (success: bool, message: str)
        """
        metadata = self._load_metadata(user_id)
        if not metadata:
            return False, "No conversations to delete."

        user_dir = self._get_user_dir(user_id)
        success_count = 0
        fail_count = 0

        for name in list(metadata.keys()):
            conv_path = os.path.join(user_dir, f"{name}.json.gz")
            try:
                if os.path.exists(conv_path):
                    os.remove(conv_path)
                del metadata[name]
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to delete {name}: {e}")
                fail_count += 1

        self._save_metadata(user_id, metadata)

        if success_count > 0:
            logger.info(f"Wiped {success_count} conversations for {user_id}")
            return True, f"Deleted {success_count} conversations."
        return False, "Failed to delete conversations."
