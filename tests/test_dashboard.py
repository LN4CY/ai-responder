import unittest
from unittest.mock import MagicMock, patch
import json
import os
import sys
from fastapi.testclient import TestClient

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dashboard.backend.manager import app, CONFIG_PATH

class TestDashboardAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
        # Ensure temporary config side-effects don't persist
        if os.path.exists(CONFIG_PATH + ".test"):
             os.remove(CONFIG_PATH + ".test")

    def test_get_config_empty(self):
        """Test fetching config when file doesn't exist."""
        with patch('dashboard.backend.manager.CONFIG_PATH', CONFIG_PATH + ".test"):
            response = self.client.get("/api/config")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {})

    def test_update_config(self):
        """Test updating configuration."""
        test_config = {"current_provider": "gemini", "ollama_model": "llama3.2:3b"}
        with patch('dashboard.backend.manager.CONFIG_PATH', CONFIG_PATH + ".test"):
            response = self.client.post("/api/config", json=test_config)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["current_provider"], "gemini")
            
            # Verify persistence
            response = self.client.get("/api/config")
            self.assertEqual(response.json()["current_provider"], "gemini")

    def test_status_stopped(self):
        """Test status when responder is not running."""
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"running": False})

    @patch('subprocess.Popen')
    def test_start_responder(self, mock_popen):
        """Test starting the responder process."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None # Running
        mock_popen.return_value = mock_process
        
        response = self.client.post("/api/start")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "started"})

    @patch('subprocess.run')
    def test_discover_models_mock(self, mock_run):
        """Test model discovery API with mock script output."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps(["model1", "model2"])
        
        response = self.client.get("/api/discover/models?provider=ollama")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["model1", "model2"])

if __name__ == "__main__":
    unittest.main()
