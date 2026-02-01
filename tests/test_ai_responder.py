import json
import pytest
import sys
import os
from unittest.mock import MagicMock, patch, mock_open

# Add parent directory to path to import ai-responder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the module - assuming filename is ai-responder.py, we might need to import using importlib
# or rename the file to have an underscore. For now, let's assume valid import.
# Since 'ai-responder.py' has a hyphen, we use __import__ hack or rename recommendation.
# For this test file, let's pretend we can import it.
# EDIT: The best way to handle 'ai-responder.py' is to rename it to 'ai_responder.py' in the release,
# but for now we import mechanism.
import importlib.util
spec = importlib.util.spec_from_file_location("ai_responder", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-responder.py"))
ai_responder = importlib.util.module_from_spec(spec)
sys.modules["ai_responder"] = ai_responder
spec.loader.exec_module(ai_responder)
AIResponder = ai_responder.AIResponder

@pytest.fixture
def responder():
    with patch('ai_responder.pub.subscribe'), \
         patch('ai_responder.TCPInterface'):
        app = AIResponder()
        app.iface = MagicMock()
        # Mock Default Config
        app.config = {
            'allowed_channels': [0, 3],
            'admin_nodes': ['!admin'],
            'current_provider': 'ollama'
        }
        return app

def test_split_message(responder):
    text = "A" * 300
    chunks = responder.split_message(text, limit=200)
    assert len(chunks) == 2
    assert len(chunks[0]) == 200
    assert len(chunks[1]) == 100

def test_is_admin(responder):
    assert responder.is_admin('!admin') is True
    assert responder.is_admin('!user') is False
    
    # Test Bootstrap Mode (No admins = All admins)
    responder.config['admin_nodes'] = []
    assert responder.is_admin('!user') is True

def test_process_command_help(responder):
    responder.send_response = MagicMock()
    responder.process_command('!ai -h', '!user', '!bot', 0)
    responder.send_response.assert_called()
    assert "Usage" in responder.send_response.call_args[0][0]

    # Admin Help
    responder.process_command('!ai -h', '!admin', '!bot', 0)
    assert "Admin Commands" in responder.send_response.call_args[0][0]

def test_provider_switch_unauthorized(responder):
    responder.send_response = MagicMock()
    responder.process_command('!ai -p gemini', '!user', '!bot', 0)
    responder.send_response.assert_called_with("⛔ Unauthorized: Admin only.", '!user', '!bot', 0, is_admin_cmd=True)

def test_provider_switch_authorized(responder):
    responder.save_config = MagicMock()
    responder.send_response = MagicMock()
    
    responder.process_command('!ai -p gemini', '!admin', '!bot', 0)
    
    assert responder.config['current_provider'] == 'gemini'
    responder.save_config.assert_called()
    assert "Switched to ONLINE" in responder.send_response.call_args[0][0]

def test_channel_management(responder):
    responder.save_config = MagicMock()
    responder.send_response = MagicMock()
    
    # Add Channel 5
    responder.process_command('!ai -c add 5', '!admin', '!bot', 0)
    assert 5 in responder.config['allowed_channels']
    
    # Remove Channel 0 (Should fail)
    responder.process_command('!ai -c rm 0', '!admin', '!bot', 0)
    assert 0 in responder.config['allowed_channels'] # Must remain
    assert "Cannot disable Channel 0" in responder.send_response.call_args[0][0]

@patch('ai_responder.requests.post')
def test_ollama_request(mock_post, responder):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'response': 'Hello World'}
    mock_post.return_value = mock_response

    response = responder.get_ollama_response('Hi')
    assert response == 'Hello World'
    mock_post.assert_called()

def test_unknown_command_is_prompt(responder):
    responder.handle_ai_request = MagicMock()
    responder.process_command('!ai What is Pi?', '!user', '!bot', 0)
    responder.handle_ai_request.assert_called_with('!user', '!bot', 0, 'What is Pi?')
