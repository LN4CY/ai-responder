import unittest
import time
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Add parent directory to path to import components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshtastic_handler.handler import MessageQueue

class TestMessageQueueLimit(unittest.TestCase):
    def setUp(self):
        self.mock_handler = MagicMock()
        self.mock_handler.running = True
        self.mock_handler.interface = MagicMock()
        self.mock_handler._split_message.side_effect = lambda x: [x]
        
        # Patch config.MESH_MAX_QUEUE_SIZE
        self.max_size = 5
        self.config_patcher = patch('config.MESH_MAX_QUEUE_SIZE', self.max_size)
        self.config_patcher.start()
        
        self.mq = MessageQueue(self.mock_handler)

    def tearDown(self):
        self.mq.processing = False
        if self.mq.thread:
            self.mq.thread.join(timeout=1)
        self.config_patcher.stop()

    def test_queue_limit_enforced(self):
        """Test that enqueue returns False when the queue is full."""
        # Fill the queue
        for i in range(self.max_size):
            success = self.mq.enqueue(f"msg {i}", "!12345678", 0, "")
            self.assertTrue(success)
        
        # Verify next message is dropped
        with self.assertLogs('meshtastic_handler.handler', level='ERROR') as cm:
            success = self.mq.enqueue("dropped msg", "!12345678", 0, "")
            self.assertFalse(success)
            self.assertIn(f"Queue FULL ({self.max_size} msgs)", cm.output[0])
        
        self.assertEqual(len(self.mq.queue), self.max_size)

    def test_queue_warning_near_full(self):
        """Test that a warning is logged when queue reaches 80%."""
        # Threshold is 80% of 5, which is 4.
        # We need to reach >= 4.
        
        with self.assertLogs('meshtastic_handler.handler', level='WARNING') as cm:
            # Add messages until we hit 4
            for i in range(4):
                self.mq.enqueue(f"msg {i}", "!12345678", 0, "")
            
            self.assertTrue(any("Queue nearly full: 4/5" in msg for msg in cm.output))

    def test_processing_continues_after_drop(self):
        """Test that the queue still processes messages after a drop."""
        # Fill it
        for i in range(self.max_size):
            self.mq.enqueue(f"msg {i}", "!12345678", 0, "")
            
        # Drop one
        self.mq.enqueue("dropped", "!12345678", 0, "")
        
        # Mock sendText to return a fake packet
        mock_packet = MagicMock()
        mock_packet.id = 123
        self.mock_handler.interface.sendText.return_value = mock_packet
        
        # Successive pops should work
        # We'll just wait a bit for the loop to run
        time.sleep(1.0)
        
        with self.mq.lock:
            # Some messages should have been popped
            self.assertLess(len(self.mq.queue), self.max_size)

if __name__ == '__main__':
    unittest.main()
