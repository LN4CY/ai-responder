# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

import unittest
from unittest.mock import MagicMock, patch, ANY
import sys
import os
import time
import threading

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from meshtastic_handler.handler import MeshtasticHandler, SafeTCPInterface, MessageQueue
from meshtastic import mesh_pb2

class TestSafeTCPInterface(unittest.TestCase):
    def setUp(self):
        # Patch pubsub to verify messages
        self.patcher_pub = patch('meshtastic_handler.handler.pub')
        self.mock_pub = self.patcher_pub.start()
        
        # Mock socket to prevent actual connection attempt during init
        with patch('socket.socket'):
            self.interface = SafeTCPInterface(hostname='localhost', connectNow=False)
            
        # Mock myNodeInfo/myNodeNum
        self.interface.myNodeNum = 123456789

    def tearDown(self):
        self.patcher_pub.stop()

    def test_implicit_ack_standard(self):
        """Test implicit ACK with valid sender and ID."""
        # Construct a fake routing packet
        # Use kwargs for 'from' because it's a reserved keyword
        packet = mesh_pb2.MeshPacket(**{'from': 987654321})
        packet.decoded.portnum = 5  # ROUTING_APP
        packet.decoded.request_id = 999
        
        from_radio = mesh_pb2.FromRadio()
        from_radio.packet.CopyFrom(packet)
        
        # Call handler with BYTES (simulating TCP stream)
        self.interface._handleFromRadio(from_radio.SerializeToString())
        
        # Verify ACK event fired
        self.mock_pub.sendMessage.assert_any_call("meshtastic.ack", packetId=999, interface=self.interface)

    def test_implicit_ack_error(self):
        """Test implicit ACK with error (should be ignored)."""
        # Mock the entire structure to avoid protobuf woes
        with patch('meshtastic_handler.handler.mesh_pb2.FromRadio') as MockFromRadio:
            mock_pkt = MagicMock()
            MockFromRadio.return_value = mock_pkt
            
            mock_pkt.HasField.return_value = True
            mock_pkt.packet.decoded.portnum = 5
            mock_pkt.packet.decoded.request_id = 888
            mock_pkt.packet.decoded.error_reason = 8 # Non-zero error
            
            # Reset mocks
            self.interface._handleFromRadio(b'rawbytes')
            
            # Should NOT fire ACK
            self.mock_pub.sendMessage.assert_not_called()

    def test_implicit_ack_echo_self(self):
        """Test implicit ACK from self (should be ignored)."""
        packet = mesh_pb2.MeshPacket(**{'from': 123456789}) # Matches myNodeNum
        packet.decoded.portnum = 5
        packet.decoded.request_id = 777
        
        from_radio = mesh_pb2.FromRadio()
        from_radio.packet.CopyFrom(packet)

        self.interface._handleFromRadio(from_radio.SerializeToString())
        
        # Verify NO ACK event
        self.mock_pub.sendMessage.assert_not_called()

    def test_implicit_ack_none_sender(self):
        """Test implicit ACK with None sender (should be ACCEPTED per fix)."""
        packet = mesh_pb2.MeshPacket(**{'from': 0}) # 0/None
        packet.decoded.portnum = 5
        packet.decoded.request_id = 666
        
        from_radio = mesh_pb2.FromRadio()
        from_radio.packet.CopyFrom(packet)
        
        self.interface._handleFromRadio(from_radio.SerializeToString())
        
    # Verify ACK event FIRED
        self.mock_pub.sendMessage.assert_any_call("meshtastic.ack", packetId=666, interface=self.interface)

class TestHandlerMetadata(unittest.TestCase):
    def setUp(self):
        self.handler = MeshtasticHandler()
        self.handler.interface = MagicMock()
        # Mock the reader thread to be alive so is_connected returns True
        self.handler.interface._reader = MagicMock()
        self.handler.interface._reader.is_alive.return_value = True
        self.handler.running = True

    def test_get_node_metadata(self):
        """Test extraction of node metadata (telemetry, location, battery, names)."""
        node_id = "!1234abcd"
        node_int = int("1234abcd", 16)
        self.handler.interface.nodes = {
            node_int: {
                'num': node_int,
                'user': {'id': node_id, 'longName': 'TestNode', 'shortName': 'TN'},
                'position': {'latitude': 40.7, 'longitude': -74.0},
                'snr': 5.5,
                'rssi': -80,
                'deviceMetrics': {'batteryLevel': 85},
                'environmentMetrics': {'temperature': 22.5, 'barometricPressure': 1013.2}
            }
        }
        
        metadata = self.handler.get_node_metadata(node_id)
        self.assertIsNotNone(metadata)
        self.assertIn("Name: TestNode", metadata)
        self.assertIn("ShortName: TN", metadata)
        self.assertIn("SNR: 5.5dB", metadata)
        self.assertIn("RSSI: -80dBm", metadata)
        self.assertIn("Location: 40.7000, -74.0000", metadata)
        self.assertIn("Battery: 85%", metadata)
        self.assertIn("Temp: 22.5C", metadata)
        self.assertIn("Press: 1013.2hPa", metadata)

    def test_get_node_metadata_uses_cache(self):
        """Test that metadata extraction falls back to the internal cache."""
        node_id = "!5678abcd"
        node_int = int("5678abcd", 16)
        
        # Node exists in interface, but has NO environmentMetrics
        self.handler.interface.nodes = {
            node_int: {
                'num': node_int,
                'deviceMetrics': {'batteryLevel': 90}
            }
        }
        
        # But we have it in our cache!
        self.handler.env_telemetry_cache[node_id] = {
            'temperature': 18.5,
            'relativeHumidity': 42.0
        }
        
        metadata = self.handler.get_node_metadata(node_id)
        self.assertIsNotNone(metadata)
        self.assertIn("Temp: 18.5C", metadata)
        self.assertIn("Hum: 42.0%", metadata)

    def test_track_node(self):
        """Test that track_node adds nodes to the interesting set."""
        node_id = "!12345678"
        self.handler.track_node(node_id)
        self.assertIn(node_id, self.handler.interesting_nodes)

    def test_on_telemetry_logging_filtering(self):
        """Test that telemetry logging level depends on interesting_nodes."""
        # 1. Test "uninteresting" node (should be DEBUG)
        node_id_uninteresting = "!uninteresting"
        packet_uninteresting = {
            'fromId': node_id_uninteresting,
            'decoded': {'telemetry': {'environmentMetrics': {'temperature': 20.0}}}
        }
        
        with patch('meshtastic_handler.handler.logger') as mock_logger:
            self.handler._on_telemetry(packet_uninteresting, None)
            # Should call debug, NOT info
            mock_logger.debug.assert_called()
            mock_logger.info.assert_not_called()
            
        # 2. Test "interesting" node (should be INFO)
        node_id_interesting = "!interesting"
        self.handler.track_node(node_id_interesting)
        packet_interesting = {
            'fromId': node_id_interesting,
            'decoded': {'telemetry': {'environmentMetrics': {'temperature': 25.0}}}
        }
        
        with patch('meshtastic_handler.handler.logger') as mock_logger:
            self.handler._on_telemetry(packet_interesting, None)
            # Should call INFO
            mock_logger.info.assert_called()

    def test_on_packet_activity(self):
        """Test that received packets update the last_activity timestamp."""
        self.handler.last_activity = 0
        self.handler._on_packet_activity({}, None)
        self.assertGreater(self.handler.last_activity, 0)

    def test_send_probe(self):
        """Test that send_probe calls interface.sendPosition."""
        self.handler.send_probe()
        self.handler.interface.sendPosition.assert_called_once()

    def test_on_telemetry_caching(self):
        """Test that incoming telemetry packets are correctly cached."""
        node_id = "!99999999"
        
        packet = {
            'fromId': node_id,
            'decoded': {
                'portnum': 'TELEMETRY_APP',
                'telemetry': {
                    'environmentMetrics': {
                        'temperature': 25.0,
                        'lux': 100
                    }
                }
            }
        }
        
        # Trigger the callback
        self.handler._on_telemetry(packet, None)
        
        # Verify cache was updated
        self.assertIn(node_id, self.handler.env_telemetry_cache)
        self.assertEqual(self.handler.env_telemetry_cache[node_id]['temperature'], 25.0)
        self.assertEqual(self.handler.env_telemetry_cache[node_id]['lux'], 100)

    @patch('meshtastic_handler.handler.telemetry_pb2')
    @patch('meshtastic_handler.handler.portnums_pb2')
    def test_request_telemetry(self, mock_portnums_pb2, mock_telemetry_pb2):
        """Test that request_telemetry sends an appropriate data packet."""
        node_id = "!f8d0a80a"
        # self.handler.running = True # Already set in setUp
        
        # Mock the protobuf structures
        mock_env_metrics = MagicMock()
        mock_telemetry_pb2.EnvironmentMetrics.return_value = mock_env_metrics
        
        mock_telemetry = MagicMock()
        mock_telemetry_pb2.Telemetry.return_value = mock_telemetry
        
        # Mock portnum
        mock_portnums_pb2.PortNum.TELEMETRY_APP = 67
        
        self.handler.request_telemetry(node_id)
        
        # Verify sendData was called on the interface
        self.handler.interface.sendData.assert_called_once()
        args, kwargs = self.handler.interface.sendData.call_args
        self.assertEqual(kwargs['destinationId'], int("f8d0a80a", 16))
        self.assertEqual(kwargs['portNum'], 67)
        self.assertTrue(kwargs['wantResponse'])

    def test_get_node_metadata_missing(self):
        """Test metadata extraction with missing fields."""
        node_id = "!missing"
        self.handler.interface.nodes = {node_id: {'num': 999}}
        
        metadata = self.handler.get_node_metadata(node_id)
        self.assertIsNone(metadata)

    def test_find_node_by_name(self):
        """Test finding a node by long name or short name."""
        self.handler.interface.nodes = {
            123: {'user': {'id': '!123', 'longName': 'LongName1', 'shortName': 'SN1'}},
            456: {'user': {'id': '!456', 'longName': 'LongName2', 'shortName': 'SN2'}}
        }
        
        # Test long name match
        self.assertEqual(self.handler.find_node_by_name('LongName1'), '!123')
        self.assertEqual(self.handler.find_node_by_name('longname2'), '!456') # Case insensitive
        
        # Test short name match
        self.assertEqual(self.handler.find_node_by_name('SN1'), '!123')
        self.assertEqual(self.handler.find_node_by_name('sn2'), '!456') # Case insensitive
        
        # Test no match
        self.assertIsNone(self.handler.find_node_by_name('NonExistent'))

    def test_get_all_nodes(self):
        """Test getting all known nodes."""
        self.handler.interface.nodes = {
            123: {'user': {'id': '!123', 'longName': 'Node1', 'shortName': 'N1'}},
            456: {'user': {'id': '!456', 'longName': 'Node2', 'shortName': 'N2'}}
        }
        
        nodes = self.handler.get_all_nodes()
        self.assertEqual(len(nodes), 2)
        self.assertIn({'id': '!123', 'longName': 'Node1', 'shortName': 'N1'}, nodes)
        self.assertIn({'id': '!456', 'longName': 'Node2', 'shortName': 'N2'}, nodes)

    def test_get_node_list_summary(self):
        """Test node list summary formatting."""
        # Test with nodes
        self.handler.interface.nodes = {
            123: {'user': {'id': '!123', 'longName': 'Alpha Node', 'shortName': 'ALPH'}},
            456: {'user': {'id': '!456', 'longName': 'Beta', 'shortName': 'BETA'}}
        }
        
        summary = self.handler.get_node_list_summary()
        self.assertIn("Neighbor nodes on mesh:", summary)
        self.assertIn("!123: Alpha Node (ALPH)", summary)
        self.assertIn("!456: Beta", summary)
        
        # Test with no nodes
        self.handler.interface.nodes = {}
        summary = self.handler.get_node_list_summary()
        self.assertEqual(summary, "No neighbors detected on mesh.")


class TestMessageQueue(unittest.TestCase):
    def setUp(self):
        self.mock_handler = MagicMock()
        self.mock_handler.running = True
        self.mock_handler.interface = MagicMock()
        # Mock _split_message since it's a helper
        self.mock_handler._split_message.return_value = ["chunk1", "chunk2"]
        
        # We need _send_chunk_reliable to return True instantly to test queue processing flow
        # But MessageQueue calls the REAL _send_chunk_reliable if we pass a mock handler?
        # No, it calls self.handler._send_chunk_reliable.
        # If self.handler is a Mock, it calls the Mock.
        # THE ISSUE: In previous run, logs showed it running REAL code.
        # This means I must have imported MessageQueue incorrectly or modified it?
        # Ah! MessageQueue._send_item calls self.handler._send_chunk_reliable?
        # NO! In handler.py, it calls self._send_chunk_reliable (its own method).
        # And that method calls self.handler.interface.sendText.
        
        self.queue = MessageQueue(self.mock_handler)
        self.queue.processing = False
        if self.queue.thread:
            self.queue.thread.join(timeout=1)

    def tearDown(self):
        self.queue.processing = False

    def test_enqueue_and_process(self):
        """Test enqueuing and processing logic."""
        self.queue.enqueue("test msg", "!dest", 0, "")
        
        self.assertEqual(len(self.queue.queue), 1)
        
        # Manually trigger process one item
        item = self.queue.queue.pop(0)
        
        # Mock the internal _send_chunk_reliable to avoid waiting for ACKs/timeouts
        # We patch the method on the INSTANCE of the queue
        with patch.object(self.queue, '_send_chunk_reliable', return_value=True) as mock_send:
            self.queue._send_item(item)
            
            # Verify split called on handler
            self.mock_handler._split_message.assert_called_with("test msg")
            
            # Verify send called 2 times (for 2 chunks)
            self.assertEqual(mock_send.call_count, 2)

if __name__ == "__main__":
    unittest.main()
