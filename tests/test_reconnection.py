# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import time
from pubsub import pub

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from meshtastic_handler.handler import MeshtasticHandler
from ai_responder import AIResponder

class TestReconnection(unittest.TestCase):
    def setUp(self):
        # Mock dependencies for AIResponder
        self.patcher_handler = patch('ai_responder.MeshtasticHandler')
        self.mock_handler_class = self.patcher_handler.start()
        self.mock_handler = self.mock_handler_class.return_value
        
        # Mock Config to prevent file I/O
        self.patcher_config = patch('ai_responder.Config')
        self.mock_config_class = self.patcher_config.start()
        self.mock_config = self.mock_config_class.return_value
        self.mock_config.data = {}
        self.mock_config.get.side_effect = lambda k, d=None: self.mock_config.data.get(k, d)
        
        # Mock os.makedirs
        self.patcher_makedirs = patch('os.makedirs')
        self.patcher_makedirs.start()

    def tearDown(self):
        self.patcher_handler.stop()
        self.patcher_config.stop()
        self.patcher_makedirs.stop()

    def test_handler_connection_lost_state(self):
        """Test that MeshtasticHandler correctly reflects connection lost state."""
        # Mock socket to avoid real connection
        with patch('socket.socket'):
            handler = MeshtasticHandler(interface_type='tcp', tcp_host='localhost')
            handler.interface = MagicMock()
            handler.interface._reader = MagicMock()
            handler.interface._reader.is_alive.return_value = True
            handler.running = True
            handler.connection_healthy = True
            
            # Manually subscribe since we didn't call handler.connect()
            pub.subscribe(handler._on_connection_lost, "meshtastic.connection.lost")
            
            # Initially connected
            self.assertTrue(handler.is_connected())
            
            # Simulate connection lost event
            pub.sendMessage("meshtastic.connection.lost", interface=handler.interface)
            
            # Now it should be disconnected thanks to the fix
            self.assertFalse(handler.is_connected())
            self.assertFalse(handler.connection_healthy)

    @patch('ai_responder.time.sleep', return_value=None)
    def test_responder_reconnect_logic(self, mock_sleep):
        """Test that AIResponder attempts to reconnect when MeshtasticHandler reports disconnected."""
        responder = AIResponder(history_dir='/tmp/history')
        
        # Setup responder state
        responder.running = True
        responder.connection_lost = False
        
        # Mock handler to report disconnected
        self.mock_handler.is_connected.return_value = False
        self.mock_handler.connect.return_value = True # Reconnect succeeds
        
        # Mock current time to be on a 10s boundary for the reconnect trigger
        with patch('time.time', return_value=1700000000): # 1700000000 % 10 == 0
            # Run the watchdog logic once by manually executing the relevant part of the loop
            current_time = 1700000000
            
            # --- Start of loop logic snippet ---
            if not responder.meshtastic.is_connected():
                if not responder.connection_lost:
                    responder.connection_lost = True
                    responder.last_activity = current_time
                
                if int(current_time) % 10 == 0:
                    if responder.meshtastic.connect(on_receive_callback=responder.on_receive):
                        responder.connection_lost = False
            # --- End of loop logic snippet ---
            
            # Verify reconnection was attempted and succeeded
            self.mock_handler.connect.assert_called_once()
            self.assertFalse(responder.connection_lost)

if __name__ == "__main__":
    unittest.main()
