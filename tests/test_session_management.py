import unittest
import os
import shutil
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

    def test_session_manager_thread_safe_timeout_no_duplicates(self):
        """Test that SessionManager handles timeouts atomically and never produces duplicate timeouts."""
        from conversation.session import SessionManager
        import time

        sm = SessionManager(self.manager, session_timeout=1)
        user = "!44444444"

        # Start session
        success, start_msg, name = sm.start_session(user, "test_session")
        self.assertTrue(success)
        self.assertTrue(sm.is_active(user))

        # Backdate session activity
        sm.active_sessions[user]['last_activity'] = time.time() - 10

        # Run concurrent checks
        t1_timed_out, t1_msg, _, _ = sm.check_timeout(user)
        self.assertTrue(t1_timed_out)
        self.assertIn("ended (timeout", t1_msg)
        self.assertFalse(sm.is_active(user))

        # Second check immediately after must NOT fire timeout again
        t2_timed_out, t2_msg, _, _ = sm.check_timeout(user)
        self.assertFalse(t2_timed_out)
        self.assertIsNone(t2_msg)

        # check_all_timeouts must also return empty
        timeouts = sm.check_all_timeouts()
        self.assertEqual(len(timeouts), 0)

if __name__ == '__main__':
    unittest.main()
