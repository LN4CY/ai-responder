import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from mcp_client import UnifiedMCPClient

class TestMCPNetworking(unittest.TestCase):
    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.get.return_value = "/tmp/mcp_servers.json"

    def test_lazy_initialization_logic(self):
        """Verify that usage methods trigger the lazy start logic."""
        client = UnifiedMCPClient(self.mock_config)
        
        # Patch BOTH ensuring started AND the async execution
        # to avoid any side effects from the loop being None
        with patch.object(client, '_ensure_started') as mock_ensure:
            with patch('mcp_client.asyncio.run_coroutine_threadsafe') as mock_run:
                # Mock result() to act like a future
                mock_res = MagicMock()
                mock_res.result.return_value = []
                mock_run.return_value = mock_res
                
                # Usage 1
                try:
                    client.get_all_tools()
                except Exception as e:
                    print(f"DEBUG: get_all_tools error: {e}")
                
                self.assertTrue(mock_ensure.called, "get_all_tools should call _ensure_started")
                
                # Usage 2
                mock_ensure.reset_mock()
                try:
                    client.call_tool("test", {})
                except Exception as e:
                    print(f"DEBUG: call_tool error: {e}")
                
                self.assertTrue(mock_ensure.called, "call_tool should call _ensure_started")

    @patch('mcp_client.sse_client')
    @patch('mcp_client.ClientSession')
    def test_sse_registration_state(self, mock_session_class, mock_sse_client):
        """Verify that the SSE connection sequence correctly updates server state."""
        client = UnifiedMCPClient(self.mock_config)
        url = "http://mempalace:8000/sse"
        name = "mempalace"
        
        # Setup Mocks
        mock_sse_cm = AsyncMock()
        mock_sse_cm.__aenter__.return_value = (MagicMock(), MagicMock())
        mock_sse_client.return_value = mock_sse_cm
        
        mock_session = AsyncMock()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_session
        mock_session_class.return_value = mock_session_cm
        
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        tools_result = MagicMock()
        tools_result.tools = [mock_tool]
        
        # Ensure they return properly when awaited
        mock_session.initialize = AsyncMock(return_value=None)
        mock_session.list_tools = AsyncMock(return_value=tools_result)
        
        # Capture server state at the moment the keepalive sleep is reached
        # (i.e. after registration succeeds). The sleep then raises CancelledError
        # to stop the infinite loop. After the loop exits the server is correctly
        # removed from client.servers (disconnect cleanup), so we verify against
        # the snapshot taken during the live connection, not after it ends.
        captured_servers = {}
        first_sleep = [True]

        async def stop_after_registration(seconds):
            if first_sleep[0]:
                captured_servers.update(client.servers)
                first_sleep[0] = False
            raise asyncio.CancelledError()

        loop = asyncio.new_event_loop()
        try:
            with patch('mcp_client.asyncio.sleep', new=stop_after_registration):
                coro = client._connect_sse_server(name, url)
                try:
                    loop.run_until_complete(coro)
                except asyncio.CancelledError:
                    pass

            self.assertIn(name, captured_servers)
            self.assertEqual(captured_servers[name]['tools'][0].name, "test_tool")
            # Verify cleanup: server must be gone after disconnect
            self.assertNotIn(name, client.servers)
        finally:
            loop.close()

if __name__ == '__main__':
    unittest.main()
