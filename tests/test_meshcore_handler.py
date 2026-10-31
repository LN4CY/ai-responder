# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Unit tests for MeshCore interface handler and MCP server."""

import unittest
from unittest.mock import MagicMock, patch
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

    def test_prepare_chunks_utf8_byte_limit_with_emojis(self):
        """Test that prepare_chunks never exceeds max_bytes in UTF-8 even with multi-byte emojis and session indicator."""
        long_text = (
            "🌐 In Wadamesh (Settings > GPS), you can set a public displacement offset. "
            "When sharing location with specific nodes over LoRa, use encrypted contact channels or direct messages. "
            "For precision tracking, configure interval reporting."
        )
        sess = "[🟢 moonlight_chat]"
        chunks = self.handler.prepare_chunks(long_text, session_indicator=sess, max_bytes=152)
        
        self.assertGreater(len(chunks), 1)
        # First chunk must contain session indicator
        self.assertTrue(chunks[0].startswith(sess))

        total = len(chunks)
        for i, chunk in enumerate(chunks):
            byte_len = len(chunk.encode('utf-8'))
            self.assertLessEqual(byte_len, 152, f"Chunk {i+1} has {byte_len} bytes (> 152 limit)")
            self.assertTrue(chunk.endswith(f"[{i+1}/{total}]"))

    def test_send_probe_updates_last_activity(self):
        """Test that send_probe updates last_activity when radio acknowledges advert."""
        self.handler.running = True
        self.handler.connection_healthy = True
        self.handler.last_activity = 100.0

        mock_meshcore = MagicMock()
        mock_result = MagicMock()
        mock_result.type = "OK"
        
        async def fake_advert():
            return mock_result

        mock_meshcore.commands.send_advert.side_effect = fake_advert
        self.handler.meshcore = mock_meshcore

        loop = asyncio.new_event_loop()
        self.handler._loop = loop
        import threading
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            success = self.handler.send_probe()
            self.assertTrue(success)
            self.assertGreater(self.handler.last_activity, 100.0)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=1.0)
            loop.close()

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

        # Look up contact dict by name
        contact = self.handler.find_contact_by_name("AliceRadio")
        self.assertIsNotNone(contact)
        self.assertEqual(contact["adv_name"], "AliceRadio")

        # Case-insensitive contact lookup
        contact_bob = self.handler.find_contact_by_name("bobnode")
        self.assertIsNotNone(contact_bob)
        self.assertEqual(contact_bob["adv_name"], "BobNode")

        # Look up node ID string (duck-typed with MeshtasticHandler contract)
        node_id_alice = self.handler.find_node_by_name("AliceRadio")
        self.assertEqual(node_id_alice, "!a1b2c3d4e5f6")

        node_id_bob = self.handler.find_node_by_name("bobnode")
        self.assertEqual(node_id_bob, "!b2c3d4e5f6a1")

        self.assertIsNone(self.handler.find_node_by_name("UnknownNode"))

        # Look up node dict by ID
        node_info = self.handler._get_node_by_id("!a1b2c3d4e5f6")
        self.assertIsNotNone(node_info)
        self.assertEqual(node_info['user']['longName'], 'AliceRadio')
        self.assertEqual(node_info['user']['id'], '!a1b2c3d4e5f6')

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

    def test_repeaters_and_rooms(self):
        """Test node type detection, repeater filtering, and room filtering."""
        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {
            "1111111111110000000000000000000000000000000000000000000000000000": {
                "public_key": "1111111111110000000000000000000000000000000000000000000000000000",
                "adv_name": "LN4CY Repeater",
                "type": 2,
                "out_path_len": 0
            },
            "2222222222220000000000000000000000000000000000000000000000000000": {
                "public_key": "2222222222220000000000000000000000000000000000000000000000000000",
                "adv_name": "East Raleigh Room",
                "type": 3,
                "out_path_len": -1
            },
            "3333333333330000000000000000000000000000000000000000000000000000": {
                "public_key": "3333333333330000000000000000000000000000000000000000000000000000",
                "adv_name": "AliceChat",
                "type": 1,
                "out_path_len": 1
            }
        }
        self.handler.meshcore = mock_meshcore

        nodes = self.handler.get_all_nodes()
        self.assertEqual(len(nodes), 3)
        self.assertEqual(nodes[0]['type'], 'Repeater')
        self.assertEqual(nodes[1]['type'], 'Room')
        self.assertEqual(nodes[2]['type'], 'Chat')

        repeaters = self.handler.get_repeaters()
        self.assertIn("LN4CY Repeater", repeaters)
        self.assertNotIn("East Raleigh Room", repeaters)

        rooms = self.handler.get_rooms()
        self.assertIn("East Raleigh Room", rooms)
        self.assertNotIn("LN4CY Repeater", rooms)

        meta = self.handler.get_node_metadata("LN4CY Repeater")
        self.assertIn("Type: Repeater", meta)

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
        self.assertEqual(pkt['fromId'], 'channel_sender')
        self.assertEqual(pkt['network'], 'meshcore')
        self.assertFalse(pkt['is_dm'])

    def test_incoming_channel_message_with_sender_prefix(self):
        """Test parsing `<SenderName>: <Message>` on group channels."""
        received_packets = []
        def mock_callback(packet, interface):
            received_packets.append(packet)

        self.handler.on_receive_callback = mock_callback

        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {
            "12a1b2dd1de30000000000000000000000000000000000000000000000000000": {
                "public_key": "12a1b2dd1de30000000000000000000000000000000000000000000000000000",
                "adv_name": "LN4CY_TDeck"
            }
        }
        self.handler.meshcore = mock_meshcore

        mock_event = MagicMock()
        mock_event.payload = {
            'type': 'CHAN',
            'channel_idx': 4,
            'text': 'LN4CY_TDeck: !ai what do you know of LN4CY nodes...'
        }

        asyncio.run(self.handler._handle_channel_msg_event(mock_event))

        self.assertEqual(len(received_packets), 1)
        pkt = received_packets[0]
        self.assertEqual(pkt['fromId'], 'LN4CY_TDeck')
        self.assertEqual(pkt['toId'], '^all')
        self.assertEqual(pkt['channel'], 4)
        self.assertEqual(pkt['decoded']['text'], '!ai what do you know of LN4CY nodes...')
        self.assertEqual(pkt['raw_pubkey_prefix'], '12a1b2dd1de3')
        self.assertEqual(pkt['network'], 'meshcore')
        self.assertFalse(pkt['is_dm'])

    def test_incoming_channel_message_url_not_treated_as_sender(self):
        """Test that URL schemes like http:// are not parsed as sender names."""
        received_packets = []
        def mock_callback(packet, interface):
            received_packets.append(packet)

        self.handler.on_receive_callback = mock_callback
        mock_event = MagicMock()
        mock_event.payload = {
            'type': 'CHAN',
            'channel_idx': 0,
            'text': 'https://example.com/test'
        }

        asyncio.run(self.handler._handle_channel_msg_event(mock_event))

        self.assertEqual(len(received_packets), 1)
        pkt = received_packets[0]
        self.assertEqual(pkt['fromId'], 'channel_sender')
        self.assertEqual(pkt['decoded']['text'], 'https://example.com/test')

    def test_get_channels_dynamic(self):
        """Test that get_channels reflects MESHCORE_ALLOWED_CHANNELS configuration."""
        import config
        with patch.object(config, 'MESHCORE_ALLOWED_CHANNELS', '4'):
            channels = self.handler.get_channels()
            self.assertEqual(len(channels), 1)
            self.assertEqual(channels[0]['index'], 4)
            self.assertEqual(channels[0]['name'], 'Channel 4')

    def test_handle_ack_event(self):
        """Test that incoming RF ACK events set the corresponding pending event."""
        pending_event = asyncio.Event()
        self.handler._pending_acks["1234abcd"] = pending_event

        mock_event = MagicMock()
        mock_event.payload = {
            "code": "1234abcd",
            "trip_time": 850
        }

        asyncio.run(self.handler._handle_ack_event(mock_event))

        self.assertTrue(pending_event.is_set())
        self.assertIn("1234abcd", self.handler._ack_results)
        self.assertEqual(self.handler._ack_results["1234abcd"]["trip_time"], 850)

    def test_async_send_chunk_with_ack(self):
        """Test sending a DM chunk and receiving the expected RF ACK."""
        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {}

        mock_send_result = MagicMock()
        mock_send_result.type = "MSG_SENT"
        mock_send_result.payload = {
            "expected_ack": b"\xab\xcd\xef\x01",
            "suggested_timeout": 2000
        }

        async def fake_send_msg(dest, text):
            asyncio.create_task(self._fire_delayed_ack("abcdef01", 350))
            return mock_send_result

        mock_meshcore.commands.send_msg.side_effect = fake_send_msg
        self.handler.meshcore = mock_meshcore

        res = asyncio.run(self.handler._async_send_chunk("Hello", "Alice", 0))
        self.assertTrue(res)

    async def _fire_delayed_ack(self, code, trip_time):
        await asyncio.sleep(0.05)
        mock_ack = MagicMock()
        mock_ack.payload = {"code": code, "trip_time": trip_time}
        await self.handler._handle_ack_event(mock_ack)

    def test_async_send_chunk_ack_timeout(self):
        """Test that DM chunk send gracefully times out when no RF ACK is received."""
        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {}

        mock_send_result = MagicMock()
        mock_send_result.type = "MSG_SENT"
        mock_send_result.payload = {
            "expected_ack": b"\x99\x88\x77\x66",
            "suggested_timeout": 100
        }

        async def fake_send_msg(dest, text):
            return mock_send_result

        mock_meshcore.commands.send_msg.side_effect = fake_send_msg
        self.handler.meshcore = mock_meshcore
        self.handler.ack_timeout = 0.2  # fast test timeout

        res = asyncio.run(self.handler._async_send_chunk("Hello", "Alice", 0))
        self.assertFalse(res)

    def test_async_send_chunk_broadcast_skips_ack(self):
        """Test that channel broadcasts do not wait for ACKs."""
        mock_meshcore = MagicMock()
        mock_send_chan_result = MagicMock()
        mock_send_chan_result.type = "OK"

        async def fake_send_chan(chan, text):
            return mock_send_chan_result

        mock_meshcore.commands.send_chan_msg.side_effect = fake_send_chan
        self.handler.meshcore = mock_meshcore

        res = asyncio.run(self.handler._async_send_chunk("Broadcast ping", "^all", 0))
        self.assertTrue(res)
        self.assertEqual(len(self.handler._pending_acks), 0)

    def test_async_send_chunk_retries_on_bad_state(self):
        """Test that send_msg retries automatically when radio reports ERR_CODE_BAD_STATE."""
        from meshcore_handler.handler import EventType
        mock_meshcore = MagicMock()
        mock_meshcore.contacts = {}

        mock_err_result = MagicMock()
        mock_err_result.type = EventType.ERROR
        mock_err_result.payload = {'error_code': 4, 'code_string': 'ERR_CODE_BAD_STATE'}

        mock_ok_result = MagicMock()
        mock_ok_result.type = getattr(EventType, 'MSG_SENT', 'MSG_SENT')
        mock_ok_result.payload = {
            'expected_ack': b'\x11\x22\x33\x44',
            'suggested_timeout': 1000
        }

        call_count = 0
        async def fake_send_msg(dest, text):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_err_result
            asyncio.create_task(self._fire_delayed_ack("11223344", 400))
            return mock_ok_result

        mock_meshcore.commands.send_msg.side_effect = fake_send_msg
        self.handler.meshcore = mock_meshcore

        with patch('asyncio.sleep') as mock_sleep:
            mock_sleep.return_value = None
            res = asyncio.run(self.handler._async_send_chunk("Retry test", "Alice", 0))
            self.assertTrue(res)
            self.assertEqual(call_count, 2)
            mock_sleep.assert_any_call(1.5)


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

    def test_mcp_proactive_schedule_message(self):
        self.mock_app._schedule_mcp_task.return_value = "✅ [sched-1] Reminder scheduled"
        res = mcp_server_meshcore.schedule_message(delay_seconds=60, context_note="Test note")
        self.assertIn("Reminder scheduled", res)
        self.mock_app._schedule_mcp_task.assert_called()

    def test_mcp_proactive_watch_node_online(self):
        self.mock_app._watch_node_online_mcp.return_value = "✅ [node-1] Watching for Alice"
        res = mcp_server_meshcore.watch_node_online(node_id_or_name="Alice", context_note="Notify when seen")
        self.assertIn("Watching for Alice", res)
        self.mock_app._watch_node_online_mcp.assert_called()

    def test_mcp_proactive_list_tasks(self):
        self.mock_app._list_proactive_tasks_mcp.return_value = "📋 Your active tasks: none"
        res = mcp_server_meshcore.list_proactive_tasks()
        self.assertIn("Your active tasks", res)

    def test_mcp_proactive_cancel_task(self):
        self.mock_app._cancel_proactive_task_mcp.return_value = "✅ Cancelled: [sched-1]"
        res = mcp_server_meshcore.cancel_proactive_task("sched-1")
        self.assertIn("Cancelled", res)

    def test_mcp_proactive_send_message(self):
        self.mock_app._send_message_mcp.return_value = "✅ Message queued for ch:4."
        res = mcp_server_meshcore.send_message(target="ch:4", message="Hello Channel 4")
        self.assertIn("Message queued", res)
        self.mock_app._send_message_mcp.assert_called_with(target="ch:4", message="Hello Channel 4")

    def test_mcp_get_repeaters_and_rooms(self):
        self.mock_handler.get_repeaters.return_value = "MeshCore Contacts (1, filter: repeater):\n- LN4CY Repeater [Repeater]"
        self.mock_handler.get_rooms.return_value = "MeshCore Contacts (1, filter: room):\n- East Raleigh Room [Room]"

        rep_res = mcp_server_meshcore.get_meshcore_repeaters()
        self.assertIn("LN4CY Repeater", rep_res)

        room_res = mcp_server_meshcore.get_meshcore_rooms()
        self.assertIn("East Raleigh Room", room_res)

    def test_mcp_get_status(self):
        self.mock_handler.request_status.return_value = {
            'bat': 4150,
            'uptime': 86400,
            'noise_floor': -115,
            'last_snr': 9.5,
            'nb_sent': 42,
            'nb_recv': 105
        }
        res = mcp_server_meshcore.get_meshcore_status("LN4CY Repeater")
        self.assertIn("4.15V", res)
        self.assertIn("24h 0m", res)
        self.assertIn("9.5 dB", res)


class TestMeshCoreAdminResolution(unittest.TestCase):
    """Test admin node resolution and isolation for MeshCoreAIResponder and MeshtasticAIResponder."""

    def test_meshcore_admin_checks(self):
        from responders import MeshCoreAIResponder
        with patch('ai_responder.Config') as MockConfig, \
             patch('ai_responder.UnifiedMCPClient'):
            cfg = {
                'meshcore_admin_nodes': ['LN4CY_TDeck', '!12A1B2DD1DE3'],
                'meshcore_allowed_channels': [0]
            }
            MockConfig.return_value.get.side_effect = lambda k, default=None: cfg.get(k, default)
            app = MeshCoreAIResponder.__new__(MeshCoreAIResponder)
            app.config = MockConfig.return_value
            app.meshcore = MagicMock()
            app.meshcore.meshcore = MagicMock()

            # Mock contact lookup
            mock_contact = {
                'adv_name': 'LN4CY_TDeck',
                'public_key': '12a1b2dd1de3456789abcdef1234567890abcdef1234567890abcdef12345678'
            }
            app.meshcore.meshcore.get_contact_by_name.side_effect = lambda name: mock_contact if name.lower() == 'ln4cy_tdeck' else None
            app.meshcore.meshcore.get_contact_by_key_prefix.side_effect = lambda p: mock_contact if p.lower().startswith('12a1b2dd1de3') else None

            # 1. MeshCore exact name match
            self.assertTrue(app.is_admin('LN4CY_TDeck'))
            self.assertTrue(app.is_admin('ln4cy_tdeck'))

            # 2. MeshCore prefix match (case-insensitive, with/without '!')
            self.assertTrue(app.is_admin('!12A1B2DD1DE3'))
            self.assertTrue(app.is_admin('!12a1b2dd1de3'))
            self.assertTrue(app.is_admin('12a1b2dd1de3'))

            # 3. Unknown node returns False
            self.assertFalse(app.is_admin('!unknown999'))
            self.assertFalse(app.is_admin('OtherUser'))

    def test_protocol_isolated_responders(self):
        import tempfile
        from responders import MeshtasticAIResponder, MeshCoreAIResponder
        from ai_responder import create_responder
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch('ai_responder.Config') as MockConfig, \
             patch('ai_responder.MeshtasticHandler'), \
             patch('ai_responder.MeshCoreHandler'), \
             patch('ai_responder.ConversationManager'), \
             patch('ai_responder.SessionManager'), \
             patch('ai_responder.UnifiedMCPClient'):
            cfg = {
                'meshtastic_admin_nodes': ['!9e044360'],
                'meshcore_admin_nodes': ['LN4CY_TDeck', '!12A1B2DD1DE3'],
                'meshtastic_allowed_channels': [3],
                'meshcore_allowed_channels': [0],
            }
            MockConfig.return_value.get.side_effect = lambda k, default=None: cfg.get(k, default)
            MockConfig.return_value.data = cfg

            msh_app = create_responder('meshtastic', history_dir=tmpdir)
            mc_app = create_responder('meshcore', history_dir=tmpdir)

            self.assertIsInstance(msh_app, MeshtasticAIResponder)
            self.assertIsInstance(mc_app, MeshCoreAIResponder)

            # Meshtastic admin is admin on MeshtasticAIResponder, NOT on MeshCoreAIResponder
            self.assertTrue(msh_app.is_admin('!9e044360'))
            self.assertFalse(mc_app.is_admin('!9e044360'))

            # MeshCore admin is admin on MeshCoreAIResponder, NOT on MeshtasticAIResponder
            self.assertTrue(mc_app.is_admin('LN4CY_TDeck'))
            self.assertFalse(msh_app.is_admin('LN4CY_TDeck'))

            # Channel isolation
            self.assertTrue(msh_app.is_channel_allowed(3))
            self.assertFalse(msh_app.is_channel_allowed(0))
            self.assertTrue(mc_app.is_channel_allowed(0))
            self.assertFalse(mc_app.is_channel_allowed(3))

    def test_meshcore_handle_ai_query_suppresses_thinking(self):
        from responders import MeshCoreAIResponder
        with patch('ai_responder.Config') as MockConfig, \
             patch('ai_responder.UnifiedMCPClient'):
            cfg = {
                'meshcore_admin_nodes': ['LN4CY_TDeck'],
                'meshcore_allowed_channels': [0]
            }
            MockConfig.return_value.get.side_effect = lambda k, default=None: cfg.get(k, default)
            app = MeshCoreAIResponder.__new__(MeshCoreAIResponder)
            app.config = MockConfig.return_value
            app.meshcore = MagicMock()

            # By default MESHCORE_SEND_THINKING is False -> initial_msg suppressed
            with patch.object(MeshCoreAIResponder, 'send_response') as mock_send, \
                 patch('threading.Thread') as mock_thread, \
                 patch('responders.meshcore_responder.get_config_module') as mock_conf:
                mock_conf.return_value.MESHCORE_SEND_THINKING = False
                app._handle_ai_query("tell me a joke", "LN4CY_TDeck", "LN4CY_T1000E", 0)
                mock_send.assert_not_called()
                mock_thread.assert_called_once()

            # When MESHCORE_SEND_THINKING is True -> sends thinking message
            with patch.object(MeshCoreAIResponder, 'send_response') as mock_send, \
                 patch('threading.Thread') as mock_thread, \
                 patch('responders.meshcore_responder.get_config_module') as mock_conf:
                mock_conf.return_value.MESHCORE_SEND_THINKING = True
                app._handle_ai_query("tell me a joke", "LN4CY_TDeck", "LN4CY_T1000E", 0)
                mock_send.assert_called_once_with("Thinking... 🤖", "LN4CY_TDeck", "LN4CY_T1000E", 0, is_admin_cmd=False)


class TestPacketTelemetryAndRouteAwareness(unittest.TestCase):
    """Test suite for live packet telemetry preservation and hop/route awareness."""

    def setUp(self):
        self.handler = MeshCoreHandler(
            interface_type='tcp',
            tcp_host='127.0.0.1',
            tcp_port=5000,
            ack_timeout=5
        )
        self.handler.meshcore = MagicMock()
        self.handler.meshcore.contacts = {
            '4161abcdef012345': {'adv_name': 'Wolf Repeater', 'name': 'Wolf Repeater', 'public_key': '4161abcdef012345'},
            'f3ed998877665544': {'adv_name': 'LN4CY Repeater', 'name': 'LN4CY Repeater', 'public_key': 'f3ed998877665544'},
            '12a1b2dd1de34455': {'adv_name': 'LN4CY_TDeck', 'name': 'LN4CY_TDeck', 'public_key': '12a1b2dd1de34455', 'out_path_len': 0, 'out_path': ''}
        }
        self.handler.meshcore.self_info = {
            'public_key': '27f0984d5035abcdef',
            'name': 'LN4CY_T1000E',
            'radio_freq': 906.875,
            'radio_bw': 250,
            'radio_sf': 11,
            'tx_power': 22,
            'adv_lat': 35.123,
            'adv_lon': -80.456
        }

    def tearDown(self):
        self.handler.disconnect()

    def test_resolve_repeater_hash(self):
        # Matching 2-byte prefix
        resolved = self.handler.resolve_repeater_hash('4161')
        self.assertEqual(resolved, '4161 [Wolf Repeater]')

        # Unknown hash returns as-is
        self.assertEqual(self.handler.resolve_repeater_hash('9999'), '9999')

    def test_format_path_hashes(self):
        # Concatenated 2-byte hashes (hash_mode=1)
        res = self.handler.format_path_hashes('4161f3ed', hash_mode=1)
        self.assertEqual(res, '4161 [Wolf Repeater] → f3ed [LN4CY Repeater]')

        # List of hashes
        res_list = self.handler.format_path_hashes(['4161', 'f3ed'])
        self.assertEqual(res_list, '4161 [Wolf Repeater] → f3ed [LN4CY Repeater]')

        # Empty or None
        self.assertIsNone(self.handler.format_path_hashes(None))
        self.assertIsNone(self.handler.format_path_hashes(''))

    def test_get_node_metadata_self(self):
        # Querying the bot itself via !bot, bot name, or public key prefix
        meta_bot = self.handler.get_node_metadata('!bot')
        self.assertIn('LN4CY_T1000E', meta_bot)
        self.assertIn('Role: Local Bot', meta_bot)
        self.assertIn('906.875MHz', meta_bot)
        self.assertIn('Lat: 35.123, Lon: -80.456', meta_bot)

        meta_name = self.handler.get_node_metadata('LN4CY_T1000E')
        self.assertIn('Role: Local Bot', meta_name)

        meta_key = self.handler.get_node_metadata('!27f0984d5035')
        self.assertIn('Role: Local Bot', meta_key)

    def test_get_node_metadata_stored_outbound_route(self):
        meta = self.handler.get_node_metadata('LN4CY_TDeck')
        self.assertIn('Stored Outbound Route: direct (0 hops)', meta)

    def test_contact_msg_telemetry_extraction(self):
        received_packets = []
        self.handler.on_receive_callback = lambda pkt, h: received_packets.append(pkt)

        event = MagicMock()
        event.payload = {
            'pubkey_prefix': '12a1b2dd1de3',
            'text': 'Hello from 2 hops away',
            'path_len': 2,
            'path': '4161f3ed',
            'path_hash_mode': 1,
            'SNR': 6.5,
            'RSSI': -88,
            'sender_timestamp': 1730000000
        }

        asyncio.run(self.handler._handle_contact_msg_event(event))

        self.assertEqual(len(received_packets), 1)
        pkt = received_packets[0]
        self.assertEqual(pkt['hops'], 2)
        self.assertEqual(pkt['snr'], 6.5)
        self.assertEqual(pkt['rssi'], -88)
        self.assertEqual(pkt['via_mqtt'], False)
        self.assertIn('4161 [Wolf Repeater] → f3ed [LN4CY Repeater]', pkt['path'])

    def test_contact_msg_direct_plen_255(self):
        received_packets = []
        self.handler.on_receive_callback = lambda pkt, h: received_packets.append(pkt)

        event = MagicMock()
        event.payload = {
            'pubkey_prefix': '12a1b2dd1de3',
            'text': 'Direct message',
            'path_len': 255,
            'SNR': 10.0,
            'RSSI': -65
        }

        asyncio.run(self.handler._handle_contact_msg_event(event))

        self.assertEqual(len(received_packets), 1)
        pkt = received_packets[0]
        self.assertEqual(pkt['hops'], 0)
        self.assertEqual(pkt['snr'], 10.0)

    def test_mcp_get_meshcore_packet_info(self):
        import threading
        thread = threading.current_thread()
        thread.ai_context = {
            'from_node': 'LN4CY_TDeck',
            'packet_metadata': {
                'hops': 2,
                'path': '4161 [Wolf Repeater] → f3ed [LN4CY Repeater]',
                'snr': 7.5,
                'rssi': -85,
                'via_mqtt': False
            }
        }
        info = mcp_server_meshcore.get_meshcore_packet_info()
        self.assertEqual(info['hops'], 2)
        self.assertEqual(info['snr'], 7.5)
        self.assertEqual(info['rssi'], -85)
        self.assertIn('Wolf Repeater', info['path'])

    def test_mcp_get_packet_info_meshtastic(self):
        import mcp_server_meshtastic
        import threading
        thread = threading.current_thread()
        thread.ai_context = {
            'from_node': '!12345678',
            'packet_metadata': {
                'hops': 1,
                'snr': 9.2,
                'rssi': -70,
                'via_mqtt': False,
                'relay_node': '!abcdef12'
            }
        }
        info = mcp_server_meshtastic.get_packet_info()
        self.assertEqual(info['hops'], 1)
        self.assertEqual(info['relay_node'], '!abcdef12')
        self.assertEqual(info['via_mqtt'], False)


if __name__ == '__main__':
    unittest.main()
