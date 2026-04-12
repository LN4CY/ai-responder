import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_responder import AIResponder

class TestProactiveIndexing(unittest.TestCase):
    def setUp(self):
        # Mock dependencies to avoid real network/disk access
        with patch('ai_responder.AIResponder._load_proactive_tasks'), \
             patch('config.Config.load', return_value={'current_provider': 'ollama', 'meshtastic_awareness': True}), \
             patch('providers.get_provider'):
            self.responder = AIResponder(history_dir='/tmp/history')
            
        self.responder.mcp_client = MagicMock()
        self.responder.mcp_client.has_server.return_value = True
        self.responder.meshtastic = MagicMock()
        self.responder.send_response = MagicMock()
        self.responder._index_to_mcp = MagicMock()
        self.responder.session_manager = MagicMock()
        self.responder.session_manager.is_active.return_value = False
        self.responder.session_manager.get_session_name.return_value = None

    @patch('providers.get_provider')
    @patch('ai_responder.datetime')
    def test_proactive_indexing_is_called(self, mock_datetime, mock_get_provider):
        # Setup mock provider
        mock_provider = MagicMock()
        mock_provider.name = "mock_provider"
        mock_provider.supports_tools = True
        mock_provider.get_response.return_value = "Alert: Temperature is 25C"
        mock_get_provider.return_value = mock_provider
        
        # Simulate a proactive trigger
        prompt = "[SYSTEM WAKEUP] COMMAND/CONTEXT: Watch temperature at 10:00"
        self.responder._process_ai_query_thread(
            prompt, "!node1", "!node1", 0, is_dm=True, is_system_trigger=True
        )
        
        # Verify indexing was called with is_system=True
        # We check the call args to see if it passed is_system=True
        found = False
        for call in self.responder._index_to_mcp.call_args_list:
            if call[0][0] == 'conversation' and call[0][1].get('is_system') is True:
                found = True
                break
        self.assertTrue(found, "Indexing should be called with is_system=True")

    def test_bg_index_conversation_system_hub_mapping(self):
        # Setup: Proactive turn with is_system=True, while a session is active
        self.responder.session_manager.get_session_name.return_value = "ActiveSession"
        payload = {
            'node_id': '!node1',
            'channel': 0,
            'prompt': "[SYSTEM WAKEUP]\nCOMMAND/CONTEXT: Check SNR\nCRITICAL INSTRUCTIONS: xyz",
            'response': "SNR is 5.0",
            'is_system': True
        }
        
        self.responder._bg_index_conversation(payload)
        
        # Verify that it used Hub_Default_!node1 instead of Chat_!node1_CH0
        # Check call to add_observations
        obs_call = [c for c in self.responder.mcp_client.call_tool.call_args_list if c[0][0] == "add_observations"]
        self.assertTrue(len(obs_call) > 0)
        entity_name = obs_call[0][0][1]['observations'][0]['entityName']
        self.assertEqual(entity_name, "Hub_Default_!node1")
        
        # Verify observation content has [SYSTEM ACTION]
        content = obs_call[0][0][1]['observations'][0]['contents'][0]
        self.assertIn("[SYSTEM ACTION]", content)
        self.assertIn("Task: Check SNR", content)
        self.assertNotIn("CRITICAL INSTRUCTIONS", content) # Should be cleaned up


if __name__ == '__main__':
    unittest.main()
