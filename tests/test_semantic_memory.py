import unittest
import os
import shutil
import time
from unittest.mock import MagicMock, patch
from ai_responder import AIResponder
import config

class TestSemanticMemory(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.dirname(os.path.abspath(__file__))
        self.mock_history_dir = os.path.join(self.test_dir, 'sem_history')
        self.mock_conv_dir = os.path.join(self.test_dir, 'sem_conversations')
        self.mock_config_file = os.path.join(self.test_dir, 'sem_config.json')
        
        for d in [self.mock_history_dir, self.mock_conv_dir]:
            if not os.path.exists(d):
                os.makedirs(d)
            
        # Patch config paths to avoid /app permission errors
        self.patchers = [
            patch('config.HISTORY_DIR', self.mock_history_dir),
            patch('config.CONVERSATIONS_DIR', self.mock_conv_dir),
            patch('config.CONFIG_FILE', self.mock_config_file)
        ]
        for p in self.patchers:
            p.start()
        
        # Initialize responder with mocked dependencies
        self.responder = AIResponder(history_dir=self.mock_history_dir)
        self.responder.mcp_client = MagicMock()
        self.responder.mcp_client.has_server.return_value = True
        
    def tearDown(self):
        for p in self.patchers:
            p.stop()
            
        for d in [self.mock_history_dir, self.mock_conv_dir]:
            if os.path.exists(d):
                shutil.rmtree(d)
        if os.path.exists(self.mock_config_file):
            os.remove(self.mock_config_file)
            
        # Use correct private attribute name for the executor
        if hasattr(self.responder, '_bg_executor'):
            self.responder._bg_executor.shutdown(wait=True)

    def test_background_indexing_dispatch(self):
        """Test that indexing is dispatched to the background thread pool."""
        # Use sync call to verify the tool routing logic directly
        self.responder._index_task_wrapper('conversation', {
            'node_id': '!1234',
            'prompt': 'Hello',
            'response': 'Hi',
            'is_dm': True
        })
        
        # Verify tool calls are made to MemPalace
        # Note: _bg_index_conversation calls create_entities and then add_observations
        self.responder.mcp_client.call_tool.assert_any_call("create_entities", unittest.mock.ANY)
        self.responder.mcp_client.call_tool.assert_any_call("add_observations", unittest.mock.ANY)

    def test_hub_routing_dm_vs_channel(self):
        """Verify that Hub names are correctly derived for DMs and Channels."""
        # 1. DM Hub
        hub_dm = self.responder._get_semantic_hub_name("!user123", channel=0, is_dm=True)
        self.assertEqual(hub_dm, "Hub_Default_!user123")
        
        # 2. Channel Hub
        hub_ch = self.responder._get_semantic_hub_name("!user123", channel=3, is_dm=False)
        self.assertEqual(hub_ch, "Hub_CH3")

    def test_safe_reset_vs_nuclear_wipe(self):
        """Verify that !ai -n is safe and !ai -n rm all is destructive."""
        from_node = "!tester"
        to_node = "!bot"
        dm_key = f"DM:{from_node}"
        
        # Populate history
        self.responder.history[dm_key] = [{"role": "user", "content": "Keep me"}]
        
        # 1. Test SAFE RESET: !ai -n
        self.responder.process_command("!ai -n", from_node, to_node, 0)
        # Buffer should be cleared
        self.assertEqual(len(self.responder.history.get(dm_key, [])), 0)
        # BUT Disk/Graph should NOT be wiped (mcp_client NOT called for deletion)
        for call in self.responder.mcp_client.call_args_list:
            self.assertNotEqual(call[0][0], "delete_entities")

        # 2. Test NUCLEAR WIPE: !ai -n rm all
        self.responder.history[dm_key] = [{"role": "user", "content": "Delete me"}]
        with patch.object(self.responder.conversation_manager, 'delete_all_conversations') as mock_del:
            # We mock the wrapper to avoid threading race in test
            with patch.object(self.responder, '_index_to_mcp') as mock_bg:
                self.responder.process_command("!ai -n rm all", from_node, to_node, 0)
                
                # Disk wipe called
                mock_del.assert_called_with(from_node)
                # Semantic wipe dispatched
                mock_bg.assert_called_with('delete_history', unittest.mock.ANY)
                
                # Now test the wipe logic directly
                self.responder._bg_delete_semantic_history({'node_id': from_node, 'topic': 'all', 'channel': 0})
                self.responder.mcp_client.call_tool.assert_any_call("delete_entities", unittest.mock.ANY)

    def test_telemetry_throttling(self):
        """Test that telemetry is only indexed every 15 minutes."""
        payload = {'node_id': '!1234', 'type': 'metrics', 'data': {'batt': 100, 'snr': 5.5}}
        
        # 1. First time -> Success
        self.responder._bg_index_telemetry(payload)
        self.assertEqual(self.responder.mcp_client.call_tool.call_count, 2) # create_entities + add_observations
        
        # 2. Second time immediately -> Skip
        self.responder.mcp_client.call_tool.reset_mock()
        self.responder._bg_index_telemetry(payload)
        self.responder.mcp_client.call_tool.assert_not_called()

if __name__ == '__main__':
    unittest.main()
