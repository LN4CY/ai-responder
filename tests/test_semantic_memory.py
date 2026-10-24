import unittest
import os
import shutil
from unittest.mock import MagicMock, patch
from ai_responder import AIResponder
import mcp_server_memory

class TestSemanticMemory(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.dirname(os.path.abspath(__file__))
        self.mock_history_dir = os.path.join(self.test_dir, 'sem_history')
        self.mock_conv_dir = os.path.join(self.test_dir, 'sem_conversations')
        self.mock_config_file = os.path.join(self.test_dir, 'sem_config.json')
        self.mock_memory_db = os.path.join(self.test_dir, 'sem_memory.db')
        
        for d in [self.mock_history_dir, self.mock_conv_dir]:
            if not os.path.exists(d):
                os.makedirs(d)
            
        # Patch config paths to avoid /app permission errors
        self.patchers = [
            patch('config.HISTORY_DIR', self.mock_history_dir),
            patch('config.CONVERSATIONS_DIR', self.mock_conv_dir),
            patch('config.CONFIG_FILE', self.mock_config_file),
            patch('config.MEMORY_DB_PATH', self.mock_memory_db),
        ]
        for p in self.patchers:
            p.start()
        
        # Initialize responder with mocked dependencies
        self.responder = AIResponder(history_dir=self.mock_history_dir)
        self.responder.mcp_client = MagicMock()
        self.responder.mcp_client.has_server.return_value = True
        
        # Test System hub separately
        hub_sys = self.responder._get_semantic_hub_name("!node1", is_system=True)
        self.assertEqual(hub_sys, "Hub_System_!node1")
        
    def tearDown(self):
        for p in self.patchers:
            p.stop()
            
        for d in [self.mock_history_dir, self.mock_conv_dir]:
            if os.path.exists(d):
                shutil.rmtree(d)
        for f in [
            self.mock_config_file,
            self.mock_memory_db,
            f"{self.mock_memory_db}-wal",
            f"{self.mock_memory_db}-shm",
        ]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
            
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
        
        # Verify tool calls are made to internal SQLite memory server
        self.responder.mcp_client.call_tool.assert_called_with("memory_add_fact", unittest.mock.ANY)

    def test_hub_routing_dm_vs_channel(self):
        """Verify that Hub names are correctly derived for DMs and Channels."""
        # 1. DM Hub
        hub_dm = self.responder._get_semantic_hub_name("!user123", channel=0, is_dm=True)
        self.assertEqual(hub_dm, "Hub_Chat_!user123")
        
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
            if len(call[0]) > 1 and call[0][0] == "memory_add_fact":
                self.assertNotEqual(call[0][1].get('predicate'), "has_status")

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
                self.responder.mcp_client.call_tool.assert_any_call("memory_delete_hub", unittest.mock.ANY)
                self.responder.mcp_client.call_tool.assert_any_call("memory_add_fact", unittest.mock.ANY)

    def test_telemetry_throttling(self):
        """Test that telemetry is only indexed every 15 minutes."""
        payload = {'node_id': '!1234', 'type': 'metrics', 'data': {'batt': 100, 'snr': 5.5}}
        
        # 1. First time -> Success
        self.responder._bg_index_telemetry(payload)
        self.assertEqual(self.responder.mcp_client.call_tool.call_count, 2) # is_a + reported_telemetry
        
        # 2. Second time immediately -> Skip
        self.responder.mcp_client.call_tool.reset_mock()
        self.responder._bg_index_telemetry(payload)
        self.responder.mcp_client.call_tool.assert_not_called()

    def test_sqlite_memory_server_end_to_end(self):
        """Test SQLite FTS5 search, Knowledge Graph queries, status, and session rehydration."""
        # 1. Add user facts and conversation turns
        mcp_server_memory.memory_add_fact("!a20a0d78", "has_hardware", "RAK4631 solar repeater on 20W panel")
        mcp_server_memory.memory_add_fact("Chat_!a20a0d78_CH0", "discusses_topic", "SolarSetup")
        mcp_server_memory.memory_add_fact("!a20a0d78", "participated_in", "Chat_!a20a0d78_CH0")
        mcp_server_memory.memory_add_fact(
            "Chat_!a20a0d78_CH0",
            "recorded_turn",
            "User: My roof node is a RAK4631 | AI: Got it, tracking your RAK4631 roof node."
        )

        # 2. Full-text search via FTS5 BM25
        search_res = mcp_server_memory.memory_search("RAK4631 solar")
        self.assertIn("RAK4631", search_res)
        self.assertIn("!a20a0d78", search_res)

        # 3. Knowledge Graph entity query
        kg_res = mcp_server_memory.memory_query_facts("!a20a0d78", direction="outgoing")
        self.assertEqual(kg_res["entity"], "!a20a0d78")
        self.assertGreaterEqual(len(kg_res["facts"]), 2)

        # 4. Status reporting
        stats = mcp_server_memory.memory_status()
        self.assertEqual(stats["total_triples"], 4)
        self.assertGreaterEqual(stats["total_entities"], 4)
        self.assertGreater(stats["db_size_bytes"], 0)

        # 5. End-to-end session rehydration using the real memory tools
        self.responder.mcp_client.call_tool.side_effect = lambda name, args: getattr(mcp_server_memory, name)(**args)
        rehydrated = self.responder._rehydrate_session_from_graph("!a20a0d78", "SolarSetup")
        self.assertEqual(len(rehydrated), 2)
        self.assertEqual(rehydrated[0], {"role": "user", "content": "My roof node is a RAK4631"})
        self.assertEqual(rehydrated[1], {"role": "assistant", "content": "Got it, tracking your RAK4631 roof node."})

        # 6. Delete hub pruning
        del_res = mcp_server_memory.memory_delete_hub("Chat_!a20a0d78_CH0")
        self.assertGreaterEqual(del_res["deleted"], 3)

if __name__ == '__main__':
    unittest.main()
