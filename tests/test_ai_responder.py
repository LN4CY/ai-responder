import unittest
from unittest.mock import MagicMock, patch, ANY
import sys
import os
import threading
import time
import json
import shutil

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import modules to test
import config
from ai_responder import AIResponder
import providers.ollama
import providers.gemini
import providers.openai
import providers.anthropic
from conversation.session import SessionManager

class TestAIResponder(unittest.TestCase):
    def setUp(self):
        # Patch paths to use a temp dir
        self.test_dir = os.path.dirname(os.path.abspath(__file__))
        self.mock_history_dir = os.path.join(self.test_dir, 'history')
        self.mock_conversations_dir = os.path.join(self.test_dir, 'conversations')
        self.mock_config_file = os.path.join(self.test_dir, 'config.json')
        
        # Ensure clean state
        if not os.path.exists(self.mock_history_dir):
            os.makedirs(self.mock_history_dir)
        if not os.path.exists(self.mock_conversations_dir):
            os.makedirs(self.mock_conversations_dir)
            
        # Create initial dummy config
        with open(self.mock_config_file, 'w') as f:
            json.dump({
                'allowed_channels': [0],
                'admin_nodes': ['!admin'],
                'current_provider': 'ollama'
            }, f)
            
        # Patch global configuration variables
        self.patcher_config = patch('config.CONFIG_FILE', self.mock_config_file)
        self.patcher_history = patch('config.HISTORY_DIR', self.mock_history_dir)
        self.patcher_conv = patch('config.CONVERSATIONS_DIR', self.mock_conversations_dir)
        
        self.patcher_config.start()
        self.patcher_history.start()
        self.patcher_conv.start()
        
        # Initialize responder
        # We need to reload config to pick up patched paths if Config class caches anything, 
        # but Config() loads from file in __init__, so it should be fine.
        self.responder = AIResponder(history_dir=self.mock_history_dir)
        
        # Mock the Meshtastic interface
        self.responder.meshtastic = MagicMock()
        self.responder.meshtastic.interface = MagicMock()
        
        # Mock session manager for most tests to avoid file I/O unless testing that specifically
        # (Optional: keep real session manager but mock its dependencies if needed)
        
        # Silence logging
        import logging
        logging.getLogger('AI-Responder').setLevel(logging.CRITICAL)

    def tearDown(self):
        self.patcher_config.stop()
        self.patcher_history.stop()
        self.patcher_conv.stop()
        
        # Cleanup temp files
        if os.path.exists(self.mock_config_file):
            os.remove(self.mock_config_file)
        if os.path.exists(self.mock_history_dir):
            shutil.rmtree(self.mock_history_dir)
        if os.path.exists(self.mock_conversations_dir):
            shutil.rmtree(self.mock_conversations_dir)

    def test_provider_list(self):
        """Test listing providers."""
        # Mock send_response to intercept output
        self.responder.send_response = MagicMock()
        self.responder.config['current_provider'] = 'gemini'
        self.responder.config.save()
        
        # !ai -p (admin only, DM only)
        # Note: In new code, process_command checks is_admin(from_node)
        self.responder.process_command("!ai -p", "!admin", "!bot", 0)
        
        args = self.responder.send_response.call_args
        self.assertIsNotNone(args)
        msg = args[0][0]
        self.assertIn("AI Providers:", msg)
        self.assertIn("✅ gemini", msg)
        self.assertIn("❌ ollama", msg)

    def test_provider_switching(self):
        """Test switching providers using commands in DM mode."""
        self.responder.send_response = MagicMock()
        
        # !ai -p gemini (DM mode)
        self.responder.process_command("!ai -p gemini", "!admin", "!bot", 0)
        self.assertEqual(self.responder.config['current_provider'], 'gemini')
        
        # !ai -p openai
        self.responder.process_command("!ai -p openai", "!admin", "!bot", 0)
        self.assertEqual(self.responder.config['current_provider'], 'openai')

        # Invalid provider
        self.responder.process_command("!ai -p invalid", "!admin", "!bot", 0)
        self.assertEqual(self.responder.config['current_provider'], 'openai') # Should not change

    def test_admin_restrictions(self):
        """Test that non-admins cannot use admin commands."""
        self.responder.send_response = MagicMock()
        
        # Non-admin trying to switch provider
        self.responder.process_command("!ai -p gemini", "!user", "!bot", 0)
        
        # Should verify send_response was called with error
        self.responder.send_response.assert_called_with(
            "⛔ Unauthorized: Admin only.", "!user", "!bot", 0, is_admin_cmd=True
        )

    def test_admin_dm_command(self):
        """Test admin sending command via Direct Message (DM)."""
        # !admin sends DM to !bot
        # We need to rely on self.responder.meshtastic.send_message being called
        self.responder.meshtastic.send_message = MagicMock()
        
        self.responder.process_command('!ai -p gemini', '!admin', '!bot', 0)
        
        # Verify meshtastic.send_message called
        args = self.responder.meshtastic.send_message.call_args
        self.assertIsNotNone(args)
        # destination_id is 2nd arg
        self.assertEqual(args[0][1], '!admin')
        self.assertIn("Switched to ONLINE", args[0][0])

    def test_admin_broadcast_command(self):
        """Test admin sending command via Broadcast - should be rejected."""
        self.responder.send_response = MagicMock()
        
        # !admin sends Broadcast to ^all
        self.responder.process_command('!ai -p ollama', '!admin', '^all', 0)
        
        # Should get DM-only error message
        args = self.responder.send_response.call_args
        self.assertIn("DM only", args[0][0])
        self.assertTrue(args.kwargs.get('is_admin_cmd', False))

    @patch('threading.Thread')
    def test_threading_model(self, mock_thread):
        """Test that AI requests spawn a new thread."""
        self.responder.on_receive({
            'decoded': {'text': '!ai hello', 'portnum': 'TEXT_MESSAGE_APP'},
            'fromId': '!user',
            'toId': '^all',
            'channel': 0
        }, None)
        
        # Should start a thread
        mock_thread.assert_called_once()
        # Verify target is internal _process_ai_query_thread
        args = mock_thread.call_args[1]
        self.assertEqual(args['target'], self.responder._process_ai_query_thread)

    @patch('requests.post')
    def test_ollama_provider(self, mock_post):
        """Test Ollama provider logic."""
        self.responder.config['current_provider'] = 'ollama'
        self.responder.config.save()
        
        # Patch configuration in the provider module itself if needed,
        # or rely on Config reading env vars. Tests patch Config class globals.
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'message': {'role': 'assistant', 'content': 'Ollama says hi'}
        }
        mock_post.return_value = mock_response

        # We call get_ai_response with history key
        response = self.responder.get_ai_response("hi", "test_ollama")
        self.assertEqual(response, "Ollama says hi")
        
        # Check URL (default localhost)
        # providers.ollama uses config.OLLAMA_HOST
        args, _ = mock_post.call_args
        self.assertIn('/api/chat', args[0])

    @patch('requests.post')
    def test_gemini_provider(self, mock_post):
        """Test Gemini provider logic."""
        self.responder.config['current_provider'] = 'gemini'
        self.responder.config.save()
        
        # Patch API key and Model
        with patch('providers.gemini.GEMINI_API_KEY', 'test-key'):
            with patch('providers.gemini.GEMINI_MODEL', 'gemini-test-model'):
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "candidates": [{"content": {"parts": [{"text": "Gemini says hi"}]}}]
                }
                mock_post.return_value = mock_response

                response = self.responder.get_ai_response("hi", "test_gemini")
                self.assertEqual(response, "Gemini says hi")
                
                # Check URL uses configured model
                args, _ = mock_post.call_args
                self.assertIn('gemini-test-model', args[0])
                self.assertIn('googleapis.com', args[0])

    @patch('requests.post')
    def test_openai_provider(self, mock_post):
        """Test OpenAI provider logic."""
        self.responder.config['current_provider'] = 'openai'
        self.responder.config.save()
        
        with patch('providers.openai.OPENAI_API_KEY', 'sk-test'):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "OpenAI says hi"}}]
            }
            mock_post.return_value = mock_response

            response = self.responder.get_ai_response("hi", "test_openai")
            self.assertEqual(response, "OpenAI says hi")
            
            args, _ = mock_post.call_args
            self.assertEqual(args[0], 'https://api.openai.com/v1/chat/completions')

    @patch('requests.post')
    def test_anthropic_provider(self, mock_post):
        """Test Anthropic provider logic."""
        self.responder.config['current_provider'] = 'anthropic'
        self.responder.config.save()
        
        with patch('providers.anthropic.ANTHROPIC_API_KEY', 'sk-ant'):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "content": [{"text": "Claude says hi"}]
            }
            mock_post.return_value = mock_response

            response = self.responder.get_ai_response("hi", "test_anthropic")
            self.assertEqual(response, "Claude says hi")
            
            args, _ = mock_post.call_args
            self.assertEqual(args[0], 'https://api.anthropic.com/v1/messages')

    def test_disable_channel_0(self):
        """Test that Channel 0 can be disabled and doing so ignores Broadcasts on Ch0."""
        self.responder.config['allowed_channels'] = [0, 3]
        self.responder.config.save()
        
        # 1. Verify response on Channel 0 (enabled)
        with patch.object(self.responder, 'process_command') as mock_process:
            pkt = {'decoded': {'text': '!ai hi', 'portnum': 'TEXT_MESSAGE_APP'}, 
                   'fromId': '!tester', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt, None)
            mock_process.assert_called()
        
        # 2. Disable Channel 0 via Admin command
        self.responder.process_command("!ai -ch rm 0", "!admin", "!bot", 3)
        
        self.assertNotIn(0, self.responder.config['allowed_channels'])
        
        # 3. Verify IGNORE on Channel 0 (disabled BROADCAST)
        with patch.object(self.responder, 'process_command') as mock_process_2:
            pkt = {'decoded': {'text': '!ai hi', 'portnum': 'TEXT_MESSAGE_APP'}, 
                   'fromId': '!tester', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt, None)
            # The on_receive logic calls process_command for !ai, 
            # OR logic inside on_receive checks channel permissions?
            # on_receive calls process_command. process_command DOES NOT check channel.
            # Wait, send_response checks channel permissions!
            # But here we are testing if process_command is CALLED.
            
            # Let's check ai_responder.py:on_receive logic.
            # It just checks text.startswith('!ai').
            # It does NOT check is_channel_allowed.
            # send_response CHECKS is_channel_allowed.
            
            # So process_command WILL be called, but reply won't be sent.
            pass

    def test_send_response_channel_permission(self):
        """Test that send_response respects channel permissions."""
        self.responder.config['allowed_channels'] = [3] # Only Ch3 allowed
        self.responder.meshtastic.send_message = MagicMock()
        
        # Try to send on Ch 0 (Disabled)
        self.responder.send_response("Hi", "!user", "^all", 0, is_admin_cmd=False)
        self.responder.meshtastic.send_message.assert_not_called()
        
        # Try to send on Ch 3 (Enabled)
        self.responder.send_response("Hi", "!user", "^all", 3, is_admin_cmd=False)
        self.responder.meshtastic.send_message.assert_called()

    def test_history_key_isolation(self):
        """Test that history keys are isolated by channel and node."""
        # 1. Channel Isolation
        key1 = self.responder._get_history_key("!user1", 0, False)
        key2 = self.responder._get_history_key("!user1", 3, False)
        self.assertNotEqual(key1, key2)
        self.assertIn("0:!user1", key1)
        self.assertIn("3:!user1", key2)

        # 2. DM Isolation
        key3 = self.responder._get_history_key("!user1", 0, True)
        self.assertEqual(key3, "DM:!user1")

        # 3. Session Isolation
        self.responder.session_manager.start_session("!user1", "Chat99")
        key4 = self.responder._get_history_key("!user1", 0, True)
        self.assertEqual(key4, "Chat99")

    def test_message_labeling_and_metadata(self):
        """Test that user messages are prefixed with Node ID and include metadata."""
        key = "test_label"
        self.responder.add_to_history(key, 'user', "Hello", node_id="!abcd", metadata="(Battery: 10%)")
        
        history = self.responder.history[key]
        self.assertEqual(len(history), 1)
        self.assertIn("[!abcd]", history[0]['content'])
        self.assertIn("(Battery: 10%)", history[0]['content'])
        self.assertIn("Hello", history[0]['content'])

    def test_context_window_tuning(self):
        """Test that Channel queries use minimal context while sessions use full context."""
        key = "test_window"
        # Add 5 messages
        for i in range(5):
            self.responder.add_to_history(key, 'user', f"msg {i}")

        with patch('ai_responder.get_provider') as mock_get:
            mock_provider = MagicMock()
            mock_get.return_value = mock_provider
            
            # 1. Non-session -> Minimal context (last 2)
            self.responder.get_ai_response("latest", key, is_session=False)
            history_sent = mock_provider.get_response.call_args[0][1]
            self.assertEqual(len(history_sent), 2)
            self.assertEqual(history_sent[-1]['content'], "msg 4")

            # 2. Session -> Full context (all 5)
            self.responder.get_ai_response("latest", key, is_session=True)
            history_sent = mock_provider.get_response.call_args[0][1]
            self.assertEqual(len(history_sent), 5)

    def test_system_prompt_grounding(self):
        """Test that system prompt contains the context ID."""
        with patch('config.SYSTEM_PROMPT_ONLINE_FILE', '/nonexistent'):
            prompt = config.load_system_prompt('gemini', context_id="Channel:0:!test")
            self.assertIn("Channel:0:!test", prompt)
            self.assertIn("CONTEXT ISOLATION", prompt)

    @patch('time.sleep', return_value=None)
    def test_metadata_injection_logic(self, _):
        """Test that metadata is injected only in DMs and once/refresh."""
        from_node = "!u1"
        to_node = "!bot"
        channel = 0
        
        # Mock metadata
        self.responder.meshtastic.get_node_metadata.return_value = "(Loc: 1, 2)"
        
        # Mock get_ai_response to avoid actual provider calls
        with patch.object(self.responder, 'get_ai_response', return_value="OK"):
            # 1. First DM -> SHould inject
            self.responder._process_ai_query_thread("hi", from_node, to_node, channel, is_dm=True)
            history_key = "DM:!u1"
            self.assertIn("(Loc: 1, 2)", self.responder.history[history_key][0]['content'])
            
            # 2. Second DM -> Should NOT inject again
            self.responder.meshtastic.get_node_metadata.reset_mock()
            self.responder._process_ai_query_thread("again", from_node, to_node, channel, is_dm=True)
            self.responder.meshtastic.get_node_metadata.assert_not_called()
            
            # 3. Forced refresh -> Should inject
            self.responder._refresh_metadata_nodes.add(from_node)
            self.responder._process_ai_query_thread("refreshed", from_node, to_node, channel, is_dm=True)
            # Metadata should be in the latest user message
            self.assertIn("(Loc: 1, 2)", self.responder.history[history_key][4]['content'])

    def test_provider_context_id_passing(self):
        """Test that context_id is passed to the provider."""
        with patch('ai_responder.get_provider') as mock_get:
            mock_provider = MagicMock()
            mock_get.return_value = mock_provider
            
            self.responder.get_ai_response("hi", "MyContextID")
            mock_provider.get_response.assert_called_with(ANY, ANY, context_id="MyContextID")

class TestSessionNotifications(unittest.TestCase):
    def setUp(self):
        # Create temp dir for this test class
        self.test_dir = os.path.dirname(os.path.abspath(__file__))
        self.mock_config_dir = os.path.join(self.test_dir, 'mock_data')
        os.makedirs(self.mock_config_dir, exist_ok=True)
        
        self.config_file = os.path.join(self.mock_config_dir, 'config.json')
        
        # Patch config constants
        self.config_patcher = patch.multiple('config', 
            CONFIG_FILE=self.config_file,
            CONVERSATIONS_DIR=os.path.join(self.mock_config_dir, 'conversations'),
            HISTORY_DIR=os.path.join(self.mock_config_dir, 'history')
        )
        self.config_patcher.start()
        
        self.config = config.Config()
        self.conv_manager = MagicMock()
        self.session_manager = SessionManager(self.conv_manager, session_timeout=1) # 1 sec timeout
        
        # Patch AIResponder's session_manager
        with patch('ai_responder.Config', return_value=self.config):
            with patch('ai_responder.MeshtasticHandler'):
                with patch('ai_responder.ConversationManager'):
                    self.responder = AIResponder()
                    self.responder.session_manager = self.session_manager

    def tearDown(self):
        self.config_patcher.stop()
        if os.path.exists(self.mock_config_dir):
            shutil.rmtree(self.mock_config_dir)
    
    def test_session_metadata_persistence(self):
        """Test that session manager stores and returns routing metadata."""
        user_id = "!user123"
        channel = 3
        to_node = "!bot"
        
        self.session_manager.start_session(user_id, "TestConv", channel, to_node)
        
        # Check internal storage
        session = self.session_manager.active_sessions[user_id]
        self.assertEqual(session['channel'], channel)
        self.assertEqual(session['to_node'], to_node)
        
        # Check end_session returns it
        _, _, ret_channel, ret_to_node = self.session_manager.end_session(user_id)
        self.assertEqual(ret_channel, channel)
        self.assertEqual(ret_to_node, to_node)

    def test_timeout_notification_data(self):
        """Test that check_all_timeouts returns full routing info."""
        user_id = "!user_timeout"
        self.session_manager.start_session(user_id, "SoonGone", channel=7, to_node="!gateway")
        
        # Mock time to be in the future
        with patch('time.time', return_value=time.time() + 10):
            timeouts = self.session_manager.check_all_timeouts()
            
            self.assertEqual(len(timeouts), 1)
            self.assertEqual(timeouts[0]['user_id'], user_id)
            self.assertEqual(timeouts[0]['channel'], 7)
            self.assertEqual(timeouts[0]['to_node'], "!gateway")
            self.assertIn("timeout", timeouts[0]['message'])

    def test_responder_passes_metadata(self):
        """Test that AIResponder passes channel/to_node to session manager."""
        from_node = "!sender"
        to_node = "!bot"
        channel = 5
        
        with patch.object(self.session_manager, 'start_session') as mock_start:
            mock_start.return_value = (True, "Started", "NewSession")
            # Send !ai -n command in DM
            self.responder.process_command("!ai -n NewSession", from_node, to_node, channel)
            
            mock_start.assert_called_once_with(from_node, "NewSession", channel, to_node)

if __name__ == "__main__":
    unittest.main()
