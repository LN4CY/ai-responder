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
        """Test that delete_all wipes only the target user's conversations."""
        user_a = "!11111111"
        self.manager.save_conversation(user_a, "chat_1", [{"role": "user", "content": "hi"}])
        self.manager.save_conversation(user_a, "chat_2", [{"role": "user", "content": "hello"}])

        user_b = "!22222222"
        self.manager.save_conversation(user_b, "chat_3", [{"role": "user", "content": "hola"}])

        # Verify files exist on disk
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, user_a, "chat_1.json.gz")))
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, user_b, "chat_3.json.gz")))

        # Wipe User A
        success, msg = self.manager.delete_all_conversations(user_a)
        self.assertTrue(success)
        self.assertIn("Deleted 2", msg)

        # User A files gone
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, user_a, "chat_1.json.gz")))
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, user_a, "chat_2.json.gz")))

        # User B untouched
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, user_b, "chat_3.json.gz")))

    def test_session_sanitization(self):
        """Verify saving with unsafe name is sanitized and cannot path-traverse."""
        user = "!33333333"
        unsafe_name = "../../../etc/passwd"
        self.manager.save_conversation(user, unsafe_name, [{"role": "user", "content": "hack"}])

        # Sanitized name should be "etcpasswd"
        sanitized_name = "etcpasswd"
        expected_path = os.path.join(self.test_dir, user, f"{sanitized_name}.json.gz")
        self.assertTrue(os.path.exists(expected_path), f"Sanitized file not found at {expected_path}")
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "passwd")), "Directory traversal detected!")

if __name__ == '__main__':
    unittest.main()
