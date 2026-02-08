
# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

import unittest
import os
import sys
import tempfile
import shutil

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config

class TestConfig(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        self.original_local_file = config.SYSTEM_PROMPT_LOCAL_FILE
        self.original_online_file = config.SYSTEM_PROMPT_ONLINE_FILE
        
    def tearDown(self):
        # Restore original config
        config.SYSTEM_PROMPT_LOCAL_FILE = self.original_local_file
        config.SYSTEM_PROMPT_ONLINE_FILE = self.original_online_file
        # Remove temporary directory
        shutil.rmtree(self.test_dir)

    def test_load_system_prompt_default(self):
        """Test that missing file returns default prompt."""
        # Point to non-existent file
        config.SYSTEM_PROMPT_LOCAL_FILE = os.path.join(self.test_dir, "nonexistent.txt")
        
        prompt = config.load_system_prompt('ollama', context_id="TEST")
        self.assertIn("CONTEXT ISOLATION", prompt)
        self.assertIn("TEST", prompt) 
        # Should be the default prompt from config.py

    def test_load_system_prompt_from_file(self):
        """Test that existing file overrides default."""
        # Create a custom prompt file
        custom_prompt_path = os.path.join(self.test_dir, "custom_prompt.txt")
        custom_content = "Custom Prompt: {context_id}"
        with open(custom_prompt_path, 'w', encoding='utf-8') as f:
            f.write(custom_content)
            
        # Point config to it
        config.SYSTEM_PROMPT_LOCAL_FILE = custom_prompt_path
        
        prompt = config.load_system_prompt('ollama', context_id="CTX123")
        self.assertEqual(prompt, "Custom Prompt: CTX123")

    def test_load_system_prompt_online_file(self):
        """Test online provider file loading."""
        custom_prompt_path = os.path.join(self.test_dir, "online_prompt.txt")
        custom_content = "Online Prompt: {context_id}"
        with open(custom_prompt_path, 'w', encoding='utf-8') as f:
            f.write(custom_content)
            
        config.SYSTEM_PROMPT_ONLINE_FILE = custom_prompt_path
        
        prompt = config.load_system_prompt('gemini', context_id="GEMINI_CTX")
        self.assertEqual(prompt, "Online Prompt: GEMINI_CTX")

if __name__ == '__main__':
    unittest.main()
