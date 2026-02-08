# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""
Session management for DM-only continuous conversations.

This module handles AI sessions where users can have continuous conversations
without using the !ai prefix for each message. Sessions are DM-only and have
automatic timeout after inactivity.
"""

import time
import logging
from config import SESSION_TIMEOUT

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages AI conversation sessions for users.
    
    Features:
    - DM-only sessions (not available in channels)
    - Automatic timeout after inactivity
    - Session indicators in responses
    - Linked to conversation slots
    """
    
    def __init__(self, conversation_manager, session_timeout=SESSION_TIMEOUT):
        """
        Initialize session manager.
        
        Args:
            conversation_manager: ConversationManager instance for saving sessions
            session_timeout: Seconds of inactivity before session expires
        """
        self.conversation_manager = conversation_manager
        self.session_timeout = session_timeout
        self.active_sessions = {}  # {user_id: session_data}
    
    def start_session(self, user_id, conversation_name=None, channel=0, to_node=None):
        """
        Start a new AI session for a user.
        
        Args:
            user_id: Unique identifier for the user
            conversation_name: Optional name for the session conversation
            channel: Channel index for notifications
            to_node: Target node ID for notifications
            
        Returns:
            tuple: (success: bool, message: str, conversation_name: str)
        """
        # Generate name if not provided
        if not conversation_name:
            conversation_name = self.conversation_manager.generate_conversation_name()
        
        # Create session data
        current_time = time.time()
        session_data = {
            'name': conversation_name,
            'started': current_time,
            'last_activity': current_time,
            'channel': channel,
            'to_node': to_node
        }
        
        # Store session
        self.active_sessions[user_id] = session_data
        
        logger.info(f"Started session '{conversation_name}' for {user_id}")
        return True, f"🟢 Session started: '{conversation_name}'", conversation_name
    
    def end_session(self, user_id, is_timeout=False):
        """
        End an active AI session.
        
        Args:
            user_id: Unique identifier for the user
            is_timeout: Whether session ended due to timeout
            
        Returns:
            tuple: (success: bool, message: str, channel: int, to_node: str)
        """
        if user_id not in self.active_sessions:
            return False, "No active session.", 0, None
        
        session_data = self.active_sessions[user_id]
        conversation_name = session_data['name']
        channel = session_data.get('channel', 0)
        to_node = session_data.get('to_node', None)
        
        # Remove session
        del self.active_sessions[user_id]
        
        # Prepare appropriate message
        if is_timeout:
            message = f"⏱️ Session '{conversation_name}' ended (timeout after {self.session_timeout // 60} minutes)."
        else:
            message = f"Session '{conversation_name}' ended."
        
        logger.info(f"Ended session '{conversation_name}' for {user_id} (timeout={is_timeout})")
        return True, message, channel, to_node
    
    def is_active(self, user_id):
        """
        Check if a user has an active session.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            bool: True if user has an active session
        """
        return user_id in self.active_sessions
    
    def update_activity(self, user_id):
        """
        Update the last activity timestamp for a session.
        
        This resets the timeout counter for the session.
        
        Args:
            user_id: Unique identifier for the user
        """
        if user_id in self.active_sessions:
            self.active_sessions[user_id]['last_activity'] = time.time()
            logger.debug(f"Updated activity for session of {user_id}")
    
    def check_timeout(self, user_id):
        """
        Check if a user's session has timed out and end it if necessary.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            bool: True if session was timed out
        """
        if user_id not in self.active_sessions:
            return False
        
        session_data = self.active_sessions[user_id]
        elapsed_time = time.time() - session_data['last_activity']
        
        if elapsed_time > self.session_timeout:
            logger.info(f"Session timeout for {user_id} after {elapsed_time:.0f}s")
            return self.end_session(user_id, is_timeout=True)
        
        return False, None, 0, None
    
    def get_session_indicator(self, user_id):
        """
        Get the session indicator string for message responses.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            str: Session indicator prefix (e.g., "[🟢 session_name] ") or empty string
        """
        if user_id not in self.active_sessions:
            return ""
        
        session_data = self.active_sessions[user_id]
        return f"[🟢 {session_data['name']}] "
    
    def get_session_name(self, user_id):
        """
        Get the conversation name for an active session.
        
        Args:
            user_id: Unique identifier for the user
            
        Returns:
            str or None: Session conversation name, or None if no active session
        """
        if user_id not in self.active_sessions:
            return None
        
        return self.active_sessions[user_id]['name']
    
    def check_all_timeouts(self):
        """
        Check all active sessions for timeouts.
        
        This should be called periodically to clean up expired sessions.
        
        Returns:
            list: List of dicts with {user_id, message, channel, to_node}
        """
        timed_out_data = []
        
        # Create a copy of keys to avoid modification during iteration
        for user_id in list(self.active_sessions.keys()):
            timed_out, message, channel, to_node = self.check_timeout(user_id)
            if timed_out:
                timed_out_data.append({
                    'user_id': user_id,
                    'message': message,
                    'channel': channel,
                    'to_node': to_node
                })
        
        if timed_out_data:
            logger.info(f"Timed out {len(timed_out_data)} sessions")
        
        return timed_out_data
