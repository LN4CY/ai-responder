import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_responder import AIResponder

class TestProactiveIndexing(unittest.TestCase):
    def setUp(self):
        # Setup temporary directories for tests
        self.test_dir = os.path.dirname(os.path.abspath(__file__))
        self.mock_history_dir = os.path.join(self.test_dir, 'proactive_history')
        self.mock_conv_dir = os.path.join(self.test_dir, 'proactive_conversations')
        self.mock_config_file = os.path.join(self.test_dir, 'proactive_config.json')

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
            self.addCleanup(p.stop)

        # Mock dependencies to avoid real network/disk access
        with patch('ai_responder.AIResponder._load_proactive_tasks'), \
             patch('config.Config.load', return_value={'current_provider': 'ollama', 'meshtastic_awareness': True}), \
             patch('providers.get_provider'):
            self.responder = AIResponder(history_dir=self.mock_history_dir)
            
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
        
        # Verify that it used Hub_System_!node1 instead of Chat_!node1_CH0
        # Check call to memory_add_fact where predicate is 'recorded_turn'
        obs_call = [c for c in self.responder.mcp_client.call_tool.call_args_list if c[0][0] == "memory_add_fact" and c[0][1].get("predicate") == "recorded_turn"]
        self.assertTrue(len(obs_call) > 0)
        entity_name = obs_call[0][0][1]['subject']
        self.assertEqual(entity_name, "Hub_System_!node1")
        
        # Verify observation content has 'System Task:'
        content = obs_call[0][0][1]['object']
        self.assertIn("System Task:", content)
        self.assertIn("Check SNR", content)
        self.assertNotIn("CRITICAL INSTRUCTIONS", content) # Should be cleaned up

    def test_index_task_registration(self):
        self.responder.mcp_client.call_tool.reset_mock()
        self.responder.meshtastic.find_node_by_name.return_value = "!target99"

        self.responder._index_task_registration(
            task_id="sched-101",
            task_type="ScheduledTask",
            from_node="!user1234",
            context_note="Monitor battery on Bob",
            targets=["Bob"]
        )

        calls = [c[0][1] for c in self.responder.mcp_client.call_tool.call_args_list if c[0][0] == "memory_add_fact"]
        pred_map = {(c["subject"], c["predicate"]): c["object"] for c in calls}

        self.assertEqual(pred_map.get(("sched-101", "is_a")), "ScheduledTask")
        self.assertEqual(pred_map.get(("sched-101", "scheduled_by")), "!user1234")
        self.assertEqual(pred_map.get(("!user1234", "scheduled_task")), "sched-101")
        self.assertEqual(pred_map.get(("sched-101", "targets_node")), "!target99")
        self.assertEqual(pred_map.get(("!user1234", "interacts_with")), "!target99")
        self.assertEqual(pred_map.get(("sched-101", "has_description")), "Monitor battery on Bob")

    def test_index_task_cancellation(self):
        self.responder.mcp_client.call_tool.reset_mock()
        self.responder._index_task_cancellation("[sched-101]")

        calls = [c[0][1] for c in self.responder.mcp_client.call_tool.call_args_list if c[0][0] == "memory_add_fact"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["subject"], "sched-101")
        self.assertEqual(calls[0]["predicate"], "has_status")
        self.assertEqual(calls[0]["object"], "cancelled")

    def test_bg_index_conversation_node_identity_and_mentions(self):
        self.responder.mcp_client.call_tool.reset_mock()
        self.responder.meshtastic.find_node_by_name.side_effect = lambda name: '!user5678' if str(name).lower() in ('bob', 'beta') else None
        self.responder.meshtastic._get_node_by_id.side_effect = lambda nid: {
            '!user1234': {'user': {'longName': 'User Alpha', 'shortName': 'ALPHA'}},
            '!user5678': {'user': {'longName': 'User Beta', 'shortName': 'BETA'}},
        }.get(nid)

        payload = {
            'node_id': '!user1234',
            'channel': 0,
            'prompt': "Can you ping !user5678 for me?",
            'response': "Pinging !user5678 now.",
            'is_system': False
        }

        self.responder._bg_index_conversation(payload)

        calls = [c[0][1] for c in self.responder.mcp_client.call_tool.call_args_list if c[0][0] == "memory_add_fact"]
        pred_map = {(c["subject"], c["predicate"]): c["object"] for c in calls}

        # Check sender identity
        self.assertEqual(pred_map.get(("!user1234", "has_name")), "User Alpha")
        self.assertEqual(pred_map.get(("!user1234", "has_short_name")), "ALPHA")
        self.assertEqual(pred_map.get(("!user1234", "is_a")), "MeshNode")

        # Check target mention & interaction
        self.assertEqual(pred_map.get(("!user1234", "interacts_with")), "!user5678")
        self.assertEqual(pred_map.get(("Hub_Chat_!user1234", "references_node")), "!user5678")
        self.assertEqual(pred_map.get(("!user5678", "has_name")), "User Beta")
        self.assertEqual(pred_map.get(("!user5678", "has_short_name")), "BETA")

    def test_bg_sync_graph_triples(self):
        self.responder.mcp_client.call_tool.reset_mock()
        self.responder.meshtastic.get_all_nodes.return_value = [
            {'id': '!base0001', 'longName': 'Base Station', 'shortName': 'BASE'},
            {'id': '!mobile02', 'longName': 'Mobile Rover', 'shortName': 'ROVR'}
        ]
        self.responder.scheduled_tasks = [
            {'id': 'sched-202', 'from_node': '!base0001', 'context_note': 'Ping Rover', 'targets': ['!mobile02']}
        ]
        self.responder.condition_watchers = [
            {'id': 'cond-303', 'from_node': '!base0001', 'node_id': '!mobile02', 'metric': 'battery_level', 'operator': '<', 'threshold': 20}
        ]
        self.responder.node_online_watchers = [
            {'id': 'node-404', 'from_node': '!base0001', 'node_id': '!mobile02'}
        ]

        self.responder._bg_sync_graph_triples()

        calls = [c[0][1] for c in self.responder.mcp_client.call_tool.call_args_list if c[0][0] == "memory_add_fact"]
        pred_map = {(c["subject"], c["predicate"]): c["object"] for c in calls}

        # Check nodes
        self.assertEqual(pred_map.get(("!base0001", "is_a")), "MeshNode")
        self.assertEqual(pred_map.get(("!base0001", "has_name")), "Base Station")
        self.assertEqual(pred_map.get(("!base0001", "has_short_name")), "BASE")
        self.assertEqual(pred_map.get(("!mobile02", "has_name")), "Mobile Rover")

        # Check scheduled tasks
        self.assertEqual(pred_map.get(("sched-202", "is_a")), "ScheduledTask")
        self.assertEqual(pred_map.get(("sched-202", "scheduled_by")), "!base0001")
        self.assertEqual(pred_map.get(("sched-202", "targets_node")), "!mobile02")

        # Check watchers
        self.assertEqual(pred_map.get(("cond-303", "is_a")), "ConditionWatcher")
        self.assertEqual(pred_map.get(("cond-303", "targets_node")), "!mobile02")
        self.assertEqual(pred_map.get(("node-404", "is_a")), "NodeOnlineWatcher")
        self.assertEqual(pred_map.get(("node-404", "targets_node")), "!mobile02")


if __name__ == '__main__':
    unittest.main()
