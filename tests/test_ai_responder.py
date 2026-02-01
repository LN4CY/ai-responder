import unittest
from unittest.mock import MagicMock, patch, ANY
import sys
import os
import threading
import time

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import module to test
import importlib.util
spec = importlib.util.spec_from_file_location("ai_responder", os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'ai-responder.py')))
mod = importlib.util.module_from_spec(spec)
sys.modules["ai_responder"] = mod
spec.loader.exec_module(mod)
AIResponder = mod.AIResponder

class TestAIResponder(unittest.TestCase):
    def setUp(self):
        self.responder = AIResponder()
        self.responder.iface = MagicMock()
        self.responder.config = {
            'allowed_channels': [0],
            'admin_nodes': ['!admin'],
            'current_provider': 'ollama'
        }
        # Silence logging
        mod.logger.setLevel('CRITICAL')

    def test_provider_list(self):
        """Test listing providers."""
        self.responder.send_response = MagicMock()
        self.responder.config['current_provider'] = 'gemini'
        
        self.responder.process_command("!ai -p", "!admin", "!bot", 0)
        
        args = self.responder.send_response.call_args
        self.assertIsNotNone(args)
        msg = args[0][0]
        self.assertIn("Providers:", msg)
        self.assertIn("✅ gemini", msg)
        self.assertIn("❌ ollama", msg)
        self.assertIn("❌ openai", msg)

    def test_provider_switching(self):
        """Test switching providers using commands."""
        # !ai -p gemini
        self.responder.process_command("!ai -p gemini", "!admin", "^all", 0)
        self.assertEqual(self.responder.config['current_provider'], 'gemini')
        
        # !ai -p openai
        self.responder.process_command("!ai -p openai", "!admin", "^all", 0)
        self.assertEqual(self.responder.config['current_provider'], 'openai')

        # Invalid provider
        self.responder.process_command("!ai -p invalid", "!admin", "^all", 0)
        self.assertEqual(self.responder.config['current_provider'], 'openai') # Should not change

    def test_admin_restrictions(self):
        """Test that non-admins cannot use admin commands."""
        # Non-admin trying to switch provider
        self.responder.process_command("!ai -p gemini", "!user", "^all", 0)
        self.responder.iface.sendText.assert_called_with("⛔ Unauthorized: Admin only.", destinationId="!user", channelIndex=0)

    def test_admin_dm_command(self):
        """Test admin sending command via Direct Message (DM)."""
        self.responder.save_config = MagicMock()
        # Do NOT mock send_response, we want to test its logic
        
        # !admin sends DM to !bot
        self.responder.process_command('!ai -p gemini', '!admin', '!bot', 0)
        
        # Verify what send_response DID (via iface.sendText)
        args = self.responder.iface.sendText.call_args
        self.assertIsNotNone(args)
        # Check that it replied to !admin privately
        self.assertEqual(args.kwargs['destinationId'], '!admin')
        self.assertIn("✅ Switched to ONLINE", args.args[0])

    def test_admin_broadcast_command(self):
        """Test admin sending command via Broadcast."""
        self.responder.save_config = MagicMock()
        # Do NOT mock send_response
        
        # !admin sends Broadcast to ^all
        self.responder.process_command('!ai -p ollama', '!admin', '^all', 0)
        
        # Even though it was broadcast, admin confirmation should be private
        args = self.responder.iface.sendText.call_args
        self.assertIsNotNone(args)
        self.assertEqual(args.kwargs['destinationId'], '!admin')
        self.assertIn("✅ Switched to LOCAL", args.args[0])

    @patch('threading.Thread')
    def test_threading_model(self, mock_thread):
        """Test that AI requests spawn a new thread."""
        self.responder.on_receive({
            'decoded': {'text': '!ai hello'},
            'fromId': '!user',
            'toId': '^all',
            'channel': 0
        }, None)
        
        # Should start a thread
        mock_thread.assert_called_once()
        args = mock_thread.call_args[1]
        self.assertEqual(args['target'], self.responder.handle_ai_request)

    @patch('requests.post')
    def test_ollama_provider(self, mock_post):
        """Test Ollama provider logic."""
        self.responder.config['current_provider'] = 'ollama'
        mod.OLLAMA_HOST = 'test-ollama'
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'response': 'Ollama says hi'}
        mock_post.return_value = mock_response

        response = self.responder.get_ai_response("hi")
        self.assertEqual(response, "Ollama says hi")
        
        # Check URL
        args, _ = mock_post.call_args
        self.assertIn('test-ollama', args[0])

    @patch('requests.post')
    def test_gemini_provider(self, mock_post):
        """Test Gemini provider logic."""
        self.responder.config['current_provider'] = 'gemini'
        mod.GEMINI_API_KEY = 'test-key'
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Gemini says hi"}]}}]
        }
        mock_post.return_value = mock_response

        response = self.responder.get_ai_response("hi")
        self.assertEqual(response, "Gemini says hi")
        
        # Check URL
        args, _ = mock_post.call_args
        self.assertIn('googleapis.com', args[0])

    @patch('requests.post')
    def test_openai_provider(self, mock_post):
        """Test OpenAI provider logic."""
        self.responder.config['current_provider'] = 'openai'
        mod.OPENAI_API_KEY = 'sk-test'
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OpenAI says hi"}}]
        }
        mock_post.return_value = mock_response

        response = self.responder.get_ai_response("hi")
        self.assertEqual(response, "OpenAI says hi")
        
        # Check URL
        args, _ = mock_post.call_args
        self.assertEqual(args[0], 'https://api.openai.com/v1/chat/completions')

    @patch('requests.post')
    def test_anthropic_provider(self, mock_post):
        """Test Anthropic provider logic."""
        self.responder.config['current_provider'] = 'anthropic'
        mod.ANTHROPIC_API_KEY = 'sk-ant'
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [{"text": "Claude says hi"}]
        }
        mock_post.return_value = mock_response

        response = self.responder.get_ai_response("hi")
        self.assertEqual(response, "Claude says hi")
        
        # Check URL
        args, _ = mock_post.call_args
        self.assertEqual(args[0], 'https://api.anthropic.com/v1/messages')

    def test_disable_channel_0(self):
        """Test that Channel 0 can be disabled and doing so ignores Broadcasts on Ch0."""
        # Setup: Ensure ch0 is initially enabled
        self.responder.config['allowed_channels'] = [0, 3]
        
        # 1. Verify response on Channel 0 (enabled)
        with patch.object(self.responder, 'process_command') as mock_process:
            # BROADCAST packet (explicit)
            pkt = {'decoded': {'text': '!ai hi'}, 'fromId': '!tester', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt, None)
            mock_process.assert_called()
        
        # 2. Disable Channel 0 via Admin command (Call REAL method)
        self.responder.config['admin_nodes'] = ['!admin']
        self.responder.process_command("!ai -c rm 0", "!admin", "!bot", 3)
        
        # Check config
        self.assertNotIn(0, self.responder.config['allowed_channels'])
        
        # 3. Verify IGNORE on Channel 0 (disabled BROADCAST)
        with patch.object(self.responder, 'process_command') as mock_process_2:
            pkt = {'decoded': {'text': '!ai hi'}, 'fromId': '!tester', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt, None)
            mock_process_2.assert_not_called()
            
            # 4. Verify ALLOW on Channel 3 (still enabled)
            pkt_ch3 = {'decoded': {'text': '!ai hi'}, 'fromId': '!tester', 'toId': '^all', 'channel': 3}
            self.responder.on_receive(pkt_ch3, None)
            mock_process_2.assert_called()

    def test_dm_standalone(self):
        """Test that DMs work even if all channels are disabled."""
        # 1. Disable ALL channels
        self.responder.config['allowed_channels'] = []
        
        with patch.object(self.responder, 'process_command') as mock_process:
            # 2. Send Broadcast (Should be IGNORED)
            pkt_bc = {'decoded': {'text': '!ai hi'}, 'fromId': '!tester', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt_bc, None)
            mock_process.assert_not_called()
            
            # 3. Send DM (Should be ACCEPTED)
            # DM implies toId != '^all'
            pkt_dm = {'decoded': {'text': '!ai hi'}, 'fromId': '!tester', 'toId': '!ai_bot', 'channel': 0}
            self.responder.on_receive(pkt_dm, None)
            mock_process.assert_called()

    def test_help_privacy_logic(self):
        """Test !ai -h reply privacy and channel restrictions."""
        
        # Case 1: Channel 0 Disabled -> Should be IGNORED (Broadcast)
        self.responder.config['allowed_channels'] = []
        with patch.object(self.responder, 'process_command') as mock_process:
            pkt = {'decoded': {'text': '!ai -h'}, 'fromId': '!tester', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt, None)
            mock_process.assert_not_called()

        # Case 2: Channel 0 Enabled -> Should reply Public (User)
        self.responder.config['allowed_channels'] = [0]
        # self.responder.is_admin is standard logic (default everyone admin? NO, check config)
        self.responder.config['admin_nodes'] = ['!admin'] 
        
        with patch.object(self.responder, 'send_response') as mock_send_pub:
            # Send as Non-Admin (!tester)
            pkt = {'decoded': {'text': '!ai -h'}, 'fromId': '!tester', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt, None)
            mock_send_pub.assert_called()
            # Check is_admin_cmd=False (Public)
            kwargs = mock_send_pub.call_args.kwargs
            self.assertFalse(kwargs.get('is_admin_cmd', False), "Help from User on enabled ch should be Public")

        # Case 3: Channel 0 Enabled -> Should reply Private (Admin)
        with patch.object(self.responder, 'send_response') as mock_send_priv:
            # Send as Admin (!admin)
            pkt = {'decoded': {'text': '!ai -h'}, 'fromId': '!admin', 'toId': '^all', 'channel': 0}
            self.responder.on_receive(pkt, None)
            mock_send_priv.assert_called()
            # Check is_admin_cmd=True (Private)
            kwargs = mock_send_priv.call_args.kwargs
            self.assertTrue(kwargs.get('is_admin_cmd', False), "Help from Admin should be Private")

    def test_connection_logic(self):
        """Test interface selection logic."""
        with patch('ai_responder.SerialInterface') as mock_serial, \
             patch('ai_responder.TCPInterface') as mock_tcp:
            
            # Helper to stop loop
            def stop_running(*args, **kwargs):
                self.responder.running = False
                return MagicMock()

            # Test TCP (Default)
            mod.INTERFACE_TYPE = 'tcp'
            self.responder.running = True
            mock_tcp.side_effect = stop_running # Stop after init
            
            self.responder.connect()
            mock_tcp.assert_called()
            mock_serial.assert_not_called()
            
            mock_tcp.reset_mock()
            
            # Test Serial
            mod.INTERFACE_TYPE = 'serial'
            mod.SERIAL_PORT = 'COM3'
            self.responder.running = True
            mock_serial.side_effect = stop_running # Stop after init
            
            self.responder.connect()
            
            mock_serial.assert_called_with(devPath='COM3')
            mock_tcp.assert_not_called()

if __name__ == "__main__":
    unittest.main()
