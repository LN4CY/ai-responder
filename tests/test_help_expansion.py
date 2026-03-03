import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_responder import AIResponder

class TestHelpAndNewTopic(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.test_dir = os.path.dirname(os.path.abspath(__file__))
        self.mock_history_dir = os.path.join(self.test_dir, 'history_test')
        self.mock_config_file = os.path.join(self.test_dir, 'config_test.json')
        self.mock_conv_dir = os.path.join(self.test_dir, 'conversations_test')
        
        with patch('ai_responder.AIResponder._load_proactive_tasks'), \
             patch('config.CONFIG_FILE', self.mock_config_file), \
             patch('config.CONVERSATIONS_DIR', self.mock_conv_dir):
            self.responder = AIResponder(history_dir=self.mock_history_dir)
        self.responder.meshtastic = MagicMock()
        self.responder.send_response = MagicMock()
        self.responder.session_manager = MagicMock()
        self.responder.clear_history = MagicMock()
        
        # Default mock returns
        self.responder.session_manager.start_session.return_value = (True, "Started", "session1")
        self.responder.session_manager.end_session.return_value = (True, "Ended", 0, None)

    def test_help_multi_message_dm_admin(self):
        # Test DM + Admin
        self.responder._handle_help_command("from", "to", 0, is_dm=True, is_admin=True)
        # Should call send_response 5 times (Basic, Session, Tasks, Examples, Admin)
        self.assertEqual(self.responder.send_response.call_count, 5)
        
        # Verify content of first message (Basic DM)
        args, kwargs = self.responder.send_response.call_args_list[0]
        self.assertIn("🤖 AI Basic Commands", args[0])
        self.assertIn("!ai [msg] : Ask AI (No prefix in session)", args[0])

        # Verify Examples has prefixes
        args_ex, _ = self.responder.send_response.call_args_list[3]
        self.assertIn("!ai Ping SNR/count", args_ex[0])
        
        # Verify Admin has shortened text
        args_admin, _ = self.responder.send_response.call_args_list[4]
        self.assertIn("⚙️ Admin Tools", args_admin[0])
        self.assertIn("!ai -p [ollama|gemini] : Switch AI", args_admin[0])

    def test_new_topic_logic_dm_no_args(self):
        # Setup: Active session
        self.responder.session_manager.is_active.return_value = True
        
        # !ai -n in DM (no args)
        self.responder.process_command("!ai -n", "from", "to", 0)
        
        # 1. Should END the session
        self.responder.session_manager.end_session.assert_called_with("from")
        # 2. Should CLEAR the default DM key only
        self.responder.clear_history.assert_called_with("DM:from")
        # 3. Should NOT start a new session
        self.responder.session_manager.start_session.assert_not_called()

    def test_new_topic_logic_dm_with_args(self):
        # Setup: Active session
        self.responder.session_manager.is_active.return_value = True
        
        # !ai -n MyNewSession in DM
        self.responder.process_command("!ai -n MyNewSession", "from", "to", 0)
        
        # 1. Should END the previous session
        self.responder.session_manager.end_session.assert_called_with("from")
        # 2. Should START a new named session
        self.responder.session_manager.start_session.assert_called_with("from", "MyNewSession", 0, "to")
        # 3. Should NOT clear any history (user said only -c rm should do that)
        self.responder.clear_history.assert_not_called()

    def test_new_topic_logic_channel(self):
        # !ai -n in Channel
        self.responder.process_command("!ai -n", "from", "^all", 0)
        
        # Should clear the correct channel key
        self.responder.clear_history.assert_called_with("Channel:0:from")
        # Should send confirmation
        self.responder.send_response.assert_called_with("✨ History cleared. Starting fresh.", "from", "^all", 0, is_admin_cmd=False)

if __name__ == '__main__':
    unittest.main()
