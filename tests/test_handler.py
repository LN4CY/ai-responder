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

    def test_get_node_metadata_exhaustive(self):
        """Test extraction of ALL 7 types of node metadata."""
        node_id = "!77777777"
        node_int = int("77777777", 16)
        
        node_info = {
                'num': node_int,
                'user': {'id': node_id, 'longName': 'FullNode', 'shortName': 'FN'},
                'snr': 10.0,
                'deviceMetrics': {'batteryLevel': 99, 'voltage': 4.2},
                'environmentMetrics': {'temperature': 25.5, 'relativeHumidity': 50.1, 'lux': 150.0},
                'airQualityMetrics': {'pm25_standard': 12.5, 'pm_voc_idx': 100},
                'powerMetrics': {'ch1_voltage': 12.0, 'ch1_current': 500},
                'healthMetrics': {'heart_bpm': 72, 'spO2': 98},
                'localStats': {'num_packets_tx': 1000, 'num_packets_rx': 5000},
                'hostMetrics': {'load1': 0.5, 'uptime': 3600}
        }
        
        self.handler.interface.nodes = {node_int: node_info}
        
        metadata = self.handler.get_node_metadata(node_id)
        self.assertIsNotNone(metadata)
        
        # Verify 1. Device
        self.assertIn("Battery: 99%", metadata)
        self.assertIn("Voltage: 4.20V", metadata)
        
        # Verify 2. Environment
        self.assertIn("Temp: 25.5C", metadata)
        self.assertIn("Hum: 50.1%", metadata)
        self.assertIn("Lux: 150.0", metadata)
        
        # Verify 3. Air Quality
        self.assertIn("PM2.5: 12.5ug/m3", metadata)
        self.assertIn("VOCIdx: 100", metadata)
        
        # Verify 4. Power
        self.assertIn("V1: 12.0V", metadata)
        self.assertIn("A1: 500mA", metadata)
        
        # Verify 5. Health
        self.assertIn("HR: 72bpm", metadata)
        self.assertIn("SpO2: 98%", metadata)
        
        # Verify 6. Local Stats
        self.assertIn("TX_Pkt: 1000", metadata)
        self.assertIn("RX_Pkt: 5000", metadata)
        
        # Verify 7. Host
        self.assertIn("Load1: 0.5", metadata)
        self.assertIn("HostUptime: 3600s", metadata)

    def test_get_node_metadata_uses_cache(self):
        """Test that metadata extraction falls back to the internal cache."""
        node_id = "!5678abcd"
        node_int = int("5678abcd", 16)
        
        self.handler.interface.nodes = {
            node_int: {
                'num': node_int,
                'deviceMetrics': {'batteryLevel': 90}
            }
        }
        
        # New multi-category cache structure
        self.handler.telemetry_cache[node_id] = {
            'environment_metrics': {
                'temperature': 18.5,
                'relative_humidity': 42.0
            }
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
            'decoded': {'telemetry': {'environment_metrics': {'temperature': 20.0}}}
        }
        
        with patch('meshtastic_handler.handler.logger') as mock_logger:
            self.handler._on_telemetry(packet_uninteresting, None)
            mock_logger.debug.assert_called()
            self.assertIn(node_id_uninteresting, self.handler.telemetry_cache)
            mock_logger.info.assert_not_called()
            
        # 2. Test "interesting" node (should be INFO)
        node_id_interesting = "!interesting"
        self.handler.track_node(node_id_interesting)
        packet_interesting = {
            'fromId': node_id_interesting,
            'decoded': {'telemetry': {'environment_metrics': {'temperature': 25.0}}}
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
                    'environment_metrics': {
                        'temperature': 25.0,
                        'lux': 100
                    }
                }
            }
        }
        
        # Trigger the callback
        self.handler._on_telemetry(packet, None)
        
        # Verify cache was updated
        self.assertIn(node_id, self.handler.telemetry_cache)
        self.assertIn('environment_metrics', self.handler.telemetry_cache[node_id])
        self.assertEqual(self.handler.telemetry_cache[node_id]['environment_metrics']['temperature'], 25.0)
        
        # Verify timestamps were captured
        self.assertIn(node_id, self.handler.telemetry_timestamps)
        self.assertIn('environment_metrics', self.handler.telemetry_timestamps[node_id])
        self.assertGreater(self.handler.telemetry_timestamps[node_id]['environment_metrics'], 0)

    @patch('meshtastic_handler.handler.telemetry_pb2')
    @patch('meshtastic_handler.handler.portnums_pb2')
    def test_request_telemetry_types(self, mock_portnums_pb2, mock_telemetry_pb2):
        """Test that request_telemetry sends appropriate packets for various types."""
        node_id = "!f8d0a80a"
        
        # Mock the protobuf structures
        mock_env_metrics = MagicMock()
        mock_telemetry_pb2.EnvironmentMetrics.return_value = mock_env_metrics
        mock_device_metrics = MagicMock()
        mock_telemetry_pb2.DeviceMetrics.return_value = mock_device_metrics
        mock_local_stats = MagicMock()
        mock_telemetry_pb2.LocalStats.return_value = mock_local_stats
        
        mock_telemetry = MagicMock()
        mock_telemetry_pb2.Telemetry.return_value = mock_telemetry
        
        # Mock portnum
        mock_portnums_pb2.PortNum.TELEMETRY_APP = 67
        
        # 1. Test Environment
        self.handler.request_telemetry(node_id, 'environment')
        self.handler.interface.sendData.assert_called()
        args, kwargs = self.handler.interface.sendData.call_args
        self.assertEqual(kwargs['destinationId'], int("f8d0a80a", 16))
        
        # 2. Test Device
        self.handler.interface.sendData.reset_mock()
        self.handler.request_telemetry(node_id, 'device')
        mock_telemetry.device_metrics.CopyFrom.assert_called_with(mock_device_metrics)
        
        # 3. Test Local Stats
        self.handler.interface.sendData.reset_mock()
        self.handler.request_telemetry(node_id, 'local_stats')
        mock_telemetry.local_stats.CopyFrom.assert_called_with(mock_local_stats)

        # 4. Test Air Quality
        self.handler.interface.sendData.reset_mock()
        self.handler.request_telemetry(node_id, 'air_quality')
        mock_telemetry.air_quality_metrics.CopyFrom.assert_called()

        # 5. Test Power
        self.handler.interface.sendData.reset_mock()
        self.handler.request_telemetry(node_id, 'power')
        mock_telemetry.power_metrics.CopyFrom.assert_called()

        # 6. Test Health
        self.handler.interface.sendData.reset_mock()
        self.handler.request_telemetry(node_id, 'health')
        mock_telemetry.health_metrics.CopyFrom.assert_called()

        # 7. Test Host
        self.handler.interface.sendData.reset_mock()
        self.handler.request_telemetry(node_id, 'host')
        mock_telemetry.host_metrics.CopyFrom.assert_called()

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
        self.assertIn({'id': '!123', 'longName': 'Node1', 'shortName': 'N1', 'lat': None, 'lon': None}, nodes)
        self.assertIn({'id': '!456', 'longName': 'Node2', 'shortName': 'N2', 'lat': None, 'lon': None}, nodes)

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

class TestACKResilience(unittest.TestCase):
    def setUp(self):
        self.mock_handler = MagicMock()
        self.mock_handler.running = True
        self.mock_handler.interface = MagicMock()
        self.mock_handler.pending_acks = set()
        self.mock_handler._split_message.return_value = ["chunk1"]
        
        # In real code, _on_ack sets current_ack_event.set()
        # But here we want to test the RACE condition in _send_chunk_reliable
        self.queue = MessageQueue(self.mock_handler)
        self.queue.processing = False # Stop the loop so we can test manually

    def test_ack_race_condition_fix(self):
        """
        Test that an ACK already in the buffer is matched immediately.
        This simulates the Radio/Mesh replying faster than the Python code can 
        register the Packet ID.
        """
        pkt_id = 12345
        mock_packet = MagicMock()
        mock_packet.id = pkt_id
        
        # SIMULATE REAL RACE: The ACK arrives via background thread WHILE sendText is running
        # We use a side_effect to add it to pending_acks at the moment of sending.
        def mock_send_side_effect(*args, **kwargs):
            self.mock_handler.pending_acks.add(pkt_id)
            return mock_packet
            
        self.mock_handler.interface.sendText.side_effect = mock_send_side_effect
        
        # Use precise patching where it is used in handler.py
        with patch('meshtastic_handler.handler.threading.Event') as MockEvent:
            mock_event_instance = MagicMock()
            MockEvent.return_value = mock_event_instance
            
            success = self.queue._send_chunk_reliable("payload", "!dest", 0, False, 1, 1)
            
            self.assertTrue(success)
            # The wait() should NOT have been called because it was a "Fast ACK" caught in the buffer
            mock_event_instance.wait.assert_not_called()

if __name__ == "__main__":
    unittest.main()
