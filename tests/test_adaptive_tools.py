import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_responder import AIResponder

class TestAdaptiveTools(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
            'current_provider': 'ollama',
            'ollama_model': 'llama3'  # Heuristic should return False for tool support
        }
        with patch('ai_responder.MeshtasticHandler'), \
             patch('ai_responder.ConversationManager'), \
             patch('ai_responder.SessionManager'):
            self.responder = AIResponder()
            self.responder.config = self.mock_config

    @patch('ai_responder.get_provider')
    def test_adaptive_injection_no_tools(self, mock_get_provider):
        # Mock a provider that DOES NOT support tools (e.g. llama3)
        mock_provider = MagicMock()
        mock_provider.supports_tools = False
        mock_provider.name = "Ollama"
        mock_get_provider.return_value = mock_provider
        
        # Mock meshtastic responses
        self.responder.meshtastic.get_node_info.return_value = {'user': {'id': '!bot'}}
        self.responder.meshtastic.get_node_metadata.return_value = "Battery: 90%"
        self.responder.meshtastic.get_node_list_summary.return_value = "Neighbors: [NodeA]"
        
        query = "What is my status?"
        from_node = "!user123"
        
        # Process query
        with patch.object(self.responder, 'send_response'), \
             patch.object(self.responder, 'add_to_history'):
            self.responder._process_ai_query_thread(query, from_node, "!bot", 0)
            
            # Verify provider call received the metadata block
            call_args = mock_provider.get_response.call_args[0]
            final_query = call_args[0]
            
            self.assertIn("[RADIO CONTEXT]", final_query)
            self.assertIn("Self: Battery: 90%", final_query)
            self.assertIn("Neighbors: [NodeA]", final_query)
            self.assertIn(query, final_query)
            
            # Verify tools was None
            kwargs = mock_provider.get_response.call_args[1]
            self.assertIsNone(kwargs['tools'])

    @patch('ai_responder.get_provider')
    def test_adaptive_injection_with_tools(self, mock_get_provider):
        # Mock a provider that DOES support tools (e.g. Gemini)
        mock_provider = MagicMock()
        mock_provider.supports_tools = True
        mock_provider.name = "Gemini"
        mock_get_provider.return_value = mock_provider
        
        query = "What is my status?"
        from_node = "!user123"
        
        # Process query
        with patch.object(self.responder, 'send_response'), \
             patch.object(self.responder, 'add_to_history'):
            self.responder._process_ai_query_thread(query, from_node, "!bot", 0)
            
            # Verify provider call received the clean query
            call_args = mock_provider.get_response.call_args[0]
            final_query = call_args[0]
            
            self.assertEqual(final_query, query)
            self.assertNotIn("[RADIO CONTEXT]", final_query)
            
            # Verify tools was passed
            kwargs = mock_provider.get_response.call_args[1]
            self.assertIsNotNone(kwargs['tools'])
            self.assertIn('get_my_info', kwargs['tools'])

    @patch('ai_responder.get_provider')
    def test_awareness_disabled(self, mock_get_provider):
        # Mock provider (Capability doesn't matter if awareness is OFF)
        mock_provider = MagicMock()
        mock_provider.supports_tools = True
        mock_get_provider.return_value = mock_provider
        
        # Override config to disable awareness
        self.responder.config['meshtastic_awareness'] = False
        
        query = "What is my status?"
        from_node = "!user123"
        
        # Process query
        with patch.object(self.responder, 'send_response'), \
             patch.object(self.responder, 'add_to_history'):
            self.responder._process_ai_query_thread(query, from_node, "!bot", 0)
            
            # Verify provider call received clean query (no tools, no meta)
            call_args = mock_provider.get_response.call_args[0]
            final_query = call_args[0]
            
            self.assertEqual(final_query, query)
            self.assertNotIn("[RADIO CONTEXT]", final_query)
            
            # Verify tools and location were both None (total isolation)
            kwargs = mock_provider.get_response.call_args[1]
            self.assertIsNone(kwargs['tools'])
            self.assertIsNone(kwargs['location'])

if __name__ == '__main__':
    unittest.main()
