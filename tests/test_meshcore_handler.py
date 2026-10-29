# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Unit tests for MeshCore interface handler and MCP server."""

import unittest
from unittest.mock import MagicMock
import sys
import os
import asyncio

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from meshcore_handler.handler import MeshCoreHandler
import mcp_server_meshcore


class TestMeshCoreHandler(unittest.TestCase):
    """Test suite for MeshCoreHandler logic and helper methods."""

    def setUp(self):
        self.handler = MeshCoreHandler(
            interface_type='tcp',
            tcp_host='127.0.0.1',
            tcp_port=5000,
            ack_timeout=5
        )

    def tearDown(self):
        self.handler.disconnect()

    def test_init_defaults(self):
        """Test default handler parameters."""
        self.assertEqual(self.handler.interface_type, 'tcp')
        self.assertEqual(self.handler.tcp_host, '127.0.0.1')
        self.assertEqual(self.handler.tcp_port, 5000)
        self.assertFalse(self.handler.is_connected())

    def test_split_message_short(self):
        """Test that short message is not split."""
        text = "Hello mesh!"
        chunks = self.handler._split_message(text, max_length=150)
        self.assertEqual(chunks, [text])

    def test_split_message_sentences(self):
        """Test splitting message cleanly across sentence boundaries."""
        sentence1 = "This is the first sentence that is relatively long."
        sentence2 = "This is the second sentence that will be pushed to the next chunk."
        full_text = f"{sentence1} {sentence2}"
        chunks = self.handler._split_message(full_text, max_length=80)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], sentence1)
        self.assertEqual(chunks[1], sentence2)

    def test_split_message_newlines(self):
        """Test splitting message cleanly on newlines."""
        line1 = "Line 1: Command overview"
        line2 = "Line 2: Another detailed command item"
        full_text = f"{line1}\n{line2}"
        chunks = self.handler._split_message(full_text, max_length=50)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], line1)
        self.assertEqual(chunks[1], line2)

    def test_contact_lookup(self):
        """Test finding nodes by name or prefix in contacts cache."""
        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {
            "a1b2c3d4e5f60000000000000000000000000000000000000000000000000000": {
                "public_key": "a1b2c3d4e5f60000000000000000000000000000000000000000000000000000",
                "adv_name": "AliceRadio",
                "adv_lat": 37.7749,
                "adv_lon": -122.4194,
                "out_path_len": 1
            },
            "b2c3d4e5f6a10000000000000000000000000000000000000000000000000000": {
                "public_key": "b2c3d4e5f6a10000000000000000000000000000000000000000000000000000",
                "adv_name": "BobNode",
                "adv_lat": 37.7833,
                "adv_lon": -122.4167,
                "out_path_len": 2
            }
        }
        self.handler.meshcore = mock_meshcore

        # Look up by name
        contact = self.handler.find_node_by_name("AliceRadio")
        self.assertIsNotNone(contact)
        self.assertEqual(contact["adv_name"], "AliceRadio")

        # Case-insensitive lookup
        contact_bob = self.handler.find_node_by_name("bobnode")
        self.assertIsNotNone(contact_bob)
        self.assertEqual(contact_bob["adv_name"], "BobNode")

        # Lookup summary list
        summary = self.handler.get_node_list_summary()
        self.assertIn("AliceRadio", summary)
        self.assertIn("BobNode", summary)
        self.assertIn("hops", summary)

        # Lookup metadata
        meta = self.handler.get_node_metadata("AliceRadio")
        self.assertIn("AliceRadio", meta)
        self.assertIn("Lat: 37.7749", meta)

    def test_get_all_nodes(self):
        """Test formatting contacts into node list."""
        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {
            "a1b2c3d4e5f60000000000000000000000000000000000000000000000000000": {
                "public_key": "a1b2c3d4e5f60000000000000000000000000000000000000000000000000000",
                "adv_name": "AliceRadio",
                "adv_lat": 10.0,
                "adv_lon": 20.0,
                "out_path_len": 0
            }
        }
        self.handler.meshcore = mock_meshcore
        nodes = self.handler.get_all_nodes()
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['longName'], 'AliceRadio')
        self.assertEqual(nodes[0]['id'], '!a1b2c3d4e5f6')

    def test_incoming_contact_message_normalization(self):
        """Test normalizing an incoming CONTACT_MSG_RECV event into AIResponder packet."""
        received_packets = []
        def mock_callback(packet, interface):
            received_packets.append(packet)

        self.handler.on_receive_callback = mock_callback

        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {}
        mock_meshcore.get_contact_by_key_prefix.return_value = {
            'adv_name': 'Charlie',
            'public_key': 'c1d2e3f4a5b60000000000000000000000000000000000000000000000000000'
        }
        mock_meshcore.self_info = {'name': 'BotRadio', 'public_key': '0000111122223333444455556666777788889999aaaabbbbccccddddeeeeffff'}
        self.handler.meshcore = mock_meshcore

        mock_event = MagicMock()
        mock_event.payload = {
            'type': 'PRIV',
            'pubkey_prefix': 'c1d2e3f4a5b6',
            'text': '!ai What is the weather?'
        }

        # Run async handler
        asyncio.run(self.handler._handle_contact_msg_event(mock_event))

        self.assertEqual(len(received_packets), 1)
        pkt = received_packets[0]
        self.assertEqual(pkt['fromId'], 'Charlie')
        self.assertEqual(pkt['decoded']['text'], '!ai What is the weather?')
        self.assertEqual(pkt['decoded']['portnum'], 'TEXT_MESSAGE_APP')
        self.assertEqual(pkt['network'], 'meshcore')
        self.assertTrue(pkt['is_dm'])

    def test_incoming_channel_message_normalization(self):
        """Test normalizing an incoming CHANNEL_MSG_RECV event."""
        received_packets = []
        def mock_callback(packet, interface):
            received_packets.append(packet)

        self.handler.on_receive_callback = mock_callback

        mock_event = MagicMock()
        mock_event.payload = {
            'type': 'CHAN',
            'channel_idx': 0,
            'text': '!ai ping'
        }

        asyncio.run(self.handler._handle_channel_msg_event(mock_event))

        self.assertEqual(len(received_packets), 1)
        pkt = received_packets[0]
        self.assertEqual(pkt['toId'], '^all')
        self.assertEqual(pkt['channel'], 0)
        self.assertEqual(pkt['decoded']['text'], '!ai ping')
        self.assertEqual(pkt['network'], 'meshcore')
        self.assertFalse(pkt['is_dm'])


class TestMeshCoreMCP(unittest.TestCase):
    """Test suite for FastMCP MeshCore tool functions."""

    def setUp(self):
        self.mock_app = MagicMock()
        self.mock_handler = MagicMock()
        self.mock_app.meshcore = self.mock_handler
        mcp_server_meshcore.init_meshcore_mcp(self.mock_app)

    def test_mcp_get_self_info(self):
        self.mock_handler.get_node_info.return_value = {
            'user': {'id': '!mesh1234', 'longName': 'MyBot'}
        }
        res = mcp_server_meshcore.get_meshcore_self_info()
        self.assertEqual(res['user']['longName'], 'MyBot')

    def test_mcp_get_contacts(self):
        self.mock_handler.get_node_list_summary.return_value = "MeshCore Contacts (1): NodeA"
        summary = mcp_server_meshcore.get_meshcore_contacts()
        self.assertEqual(summary, "MeshCore Contacts (1): NodeA")

    def test_mcp_send_message(self):
        self.mock_handler.send_message.return_value = True
        res = mcp_server_meshcore.send_meshcore_message("Alice", "Hello", 0)
        self.assertIn("Message queued", res)
        self.mock_handler.send_message.assert_called_with(text="Hello", destination_id="Alice", channel_index=0)


if __name__ == '__main__':
    unittest.main()
