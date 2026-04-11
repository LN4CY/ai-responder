import unittest
import sys
import os
import json
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from providers.gemini import GeminiProvider
from providers.openai import OpenAIProvider
from providers.anthropic import AnthropicProvider

class MockTextContent:
    """Mock for mcp.types.TextContent"""
    def __init__(self, text):
        self.type = "text"
        self.text = text

class MockCallToolResult:
    """Mock for mcp.types.CallToolResult"""
    def __init__(self, text_list):
        self.content = [MockTextContent(t) for t in text_list]

class TestProviders(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
            'gemini_api_key': 'fake_key',
            'openai_api_key': 'fake_key',
            'anthropic_api_key': 'fake_key',
            'current_provider': 'gemini'
        }
        # Start patches for global config keys
        self.patcher_openai = patch('providers.openai.OPENAI_API_KEY', 'fake_key')
        self.patcher_anthropic = patch('providers.anthropic.ANTHROPIC_API_KEY', 'fake_key')
        self.patcher_gemini = patch('providers.gemini.config.GEMINI_API_KEY', 'fake_key')
        
        self.patcher_openai.start()
        self.patcher_anthropic.start()
        self.patcher_gemini.start()

    def tearDown(self):
        self.patcher_openai.stop()
        self.patcher_anthropic.stop()
        self.patcher_gemini.stop()
        
    @patch('providers.gemini.requests.Session.post')
    def test_gemini_mcp_serialization(self, mock_post):
        """Test that Gemini provider correctly handles MCP TextContent objects."""
        # 1. Setup provider
        provider = GeminiProvider(self.mock_config)
        
        # 2. Setup mock MCP client to return an Object
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool.return_value = MockCallToolResult(["System optimal", "Battery 100%"])
        
        # 3. Setup mock Gemini response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "get_node_details",
                            "args": {"node_id_or_name": "NodeA"}
                        }
                    }]
                }
            }]
        }
        mock_post.return_value = mock_response
        
        # 4. We expect the function loop to hit the limit or break out if we mock endlessly, 
        # so we'll just let it run one tool and then mock a success terminal text response on the next turn.
        mock_response_2 = MagicMock()
        mock_response_2.status_code = 200
        mock_response_2.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Done."}]}}]
        }
        mock_post.side_effect = [mock_response, mock_response_2]
        
        tools = [{"name": "get_node_details", "description": "Gets node details", "inputSchema": {"type": "OBJECT", "properties": {}}}]
        
        # 5. Execute
        # If this doesn't raise a serialization TypeError on `payload["contents"]`, the test passes!
        result = provider.get_response("hello", tools=tools, mcp_client=mock_mcp_client)
        
        self.assertEqual(result, "Done.")
        # Verify the payload appended contained our serialized string
        mock_mcp_client.call_tool.assert_called_once_with("get_node_details", {"node_id_or_name": "NodeA"})
        
    @patch('providers.openai.requests.post')
    def test_openai_mcp_serialization(self, mock_post):
        """Test that OpenAI provider correctly handles MCP TextContent objects."""
        provider = OpenAIProvider(self.mock_config)
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool.return_value = MockCallToolResult(["Node Data"])
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "get_node_details",
                        "arguments": '{"node": "A"}'
                    }
                }]
            }}]
        }
        
        mock_response_2 = MagicMock()
        mock_response_2.status_code = 200
        mock_response_2.json.return_value = {
             "choices": [{"message": {"content": "OpenAI done."}}]
        }
        mock_post.side_effect = [mock_response, mock_response_2]
        
        tools = [{"name": "get_node_details"}]
        
        # Execute
        result = provider.get_response("hello", tools=tools, mcp_client=mock_mcp_client)
        self.assertEqual(result, "OpenAI done.")
        
    @patch('providers.anthropic.requests.post')
    def test_anthropic_mcp_serialization(self, mock_post):
        """Test that Anthropic provider correctly handles MCP TextContent objects."""
        provider = AnthropicProvider(self.mock_config)
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool.return_value = MockCallToolResult(["Graph Data"])
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [{
                "type": "tool_use",
                "id": "tu_1",
                "name": "read_graph",
                "input": {"topic": "A"}
            }]
        }
        
        mock_response_2 = MagicMock()
        mock_response_2.status_code = 200
        mock_response_2.json.return_value = {
             "content": [{"type": "text", "text": "Anthropic done."}]
        }
        mock_post.side_effect = [mock_response, mock_response_2]
        
        tools = [{"name": "read_graph"}]
        
        # Execute
        result = provider.get_response("hello", tools=tools, mcp_client=mock_mcp_client)
        self.assertEqual(result, "Anthropic done.")

if __name__ == '__main__':
    unittest.main()
