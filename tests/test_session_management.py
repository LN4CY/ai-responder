import unittest
import os
import shutil
import json
import time
from unittest.mock import MagicMock, patch
from conversation.manager import ConversationManager

class TestSessionManagement(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/temp_conversations"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir)
        self.manager = ConversationManager(conversations_dir=self.test_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_delete_all_conversations(self):
        """Verify the ConversationManager stub responds correctly (now MemPalace-managed)."""
        user_a = "!11111111"
        # save_conversation is a no-op stub — returns True/deprecation message
        ok, msg = self.manager.save_conversation(user_a, "chat_1", [{"role": "user", "content": "hi"}])
        self.assertTrue(ok)

        # delete_all_conversations returns False stub (MemPalace managed)
        success, msg = self.manager.delete_all_conversations(user_a)
        self.assertFalse(success)
        self.assertIn("MemPalace", msg)

    def test_session_sanitization(self):
        """Verify sanitization logic strips unsafe characters."""
        unsafe = "../../../etc/passwd"
        sanitized = self.manager._sanitize_name(unsafe)
        # Dots and slashes stripped, only alphanumeric/hyphen/underscore
        self.assertEqual(sanitized, "etcpasswd")
        self.assertNotIn("..", sanitized)
        self.assertNotIn("/", sanitized)

if __name__ == '__main__':
    unittest.main()
