import os
import json
import logging
import asyncio
import threading
import concurrent.futures
from typing import Dict, Any, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

# Local tools
from mcp_server_meshtastic import mcp as internal_meshtastic_mcp

logger = logging.getLogger(__name__)

class UnifiedMCPClient:
    """
    Manages connections to multiple MCP servers (both internal and external via stdio).
    Provides synchronous wrappers for async MCP operations so the standard AI threaded
    generation loops can easily use them.
    """
    def __init__(self, config):
        self.config = config
        self.servers = {} # {name: {'session': ClientSession, 'tools': list[Tool]}}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MCP_Client_Loop")
        self._thread.start()
        
        # Load configured servers
        self._initialize_servers()
        
    def _run_loop(self):
        """Run the dedicated asyncio event loop for MCP clients."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _initialize_servers(self):
        """Load and connect to defined MCP servers asynchronously."""
        # 1. First, register the internal Meshtastic MCP Server tools
        # FastMCP tools can be accessed via `internal_meshtastic_mcp._tool_manager.list_tools()` or similar
        # But for simplicity, we mock them into our dictionary. Since it's internal we can just bridge it.
        asyncio.run_coroutine_threadsafe(self._init_internal_server(), self._loop)
        
        # 2. Connect to configured external servers (like MemPalace)
        mcp_servers_file = self.config.get('mcp_servers_file', '/app/data/mcp_servers.json')
        if os.path.exists(mcp_servers_file):
            try:
                with open(mcp_servers_file, 'r') as f:
                    servers = json.load(f)
                    for name, mcp_config in servers.get('mcpServers', {}).items():
                        cmd = mcp_config.get('command')
                        args = mcp_config.get('args', [])
                        env = mcp_config.get('env', None)
                        if cmd:
                            # Schedule connection in the background event loop
                            asyncio.run_coroutine_threadsafe(self._connect_stdio_server(name, cmd, args, env), self._loop)
            except Exception as e:
                logger.error(f"Failed to load MCP servers from {mcp_servers_file}: {e}")

    async def _init_internal_server(self):
        """Initialize the in-process tools."""
        try:
            tools = await internal_meshtastic_mcp.list_tools()
            self.servers['meshtastic'] = {
                'type': 'internal',
                'tools': tools,
                'session': None # internal uses direct execution
            }
            logger.info("Internal Meshtastic MCP tools loaded.")
        except Exception as e:
            logger.error(f"Failed to load internal Meshtastic tools: {e}")

    async def _connect_stdio_server(self, name: str, command: str, args: List[str], env: Dict[str, str] = None):
        """Connect to an external MCP server via stdio."""
        server_env = os.environ.copy()
        if env:
            server_env.update(env)
            
        params = StdioServerParameters(
            command=command,
            args=args,
            env=server_env
        )
        while True:
            try:
                # We must keep context managers alive; so we store them
                # Note: This is an infinite lifetime task in the loop for the duration of the app
                logger.info(f"Connecting to MCP server '{name}' via stdio...")
                async with stdio_client(params) as (read_ctx, write_ctx):
                    async with ClientSession(read_ctx, write_ctx) as session:
                        await session.initialize()
                        
                        # Store session and tools
                        tools = (await session.list_tools()).tools
                        self.servers[name] = {
                            'type': 'stdio',
                            'session': session,
                            'tools': tools
                        }
                        logger.info(f"Connected to MCP server '{name}' successfully.")
                        
                        # Keep connection alive - if this exits, the server is removed in finally
                        while True:
                            # Periodic health check/keepalive if needed
                            await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"MCP server '{name}' connection error: {e}")
            finally:
                # Ensure server is removed if connection died
                if name in self.servers and self.servers[name]['type'] == 'stdio':
                    self.servers.pop(name, None)
                    logger.warning(f"MCP server '{name}' removed from active list.")
                
            # Wait before attempting to reconnect
            await asyncio.sleep(10)
            
    # --- Sync Wrappers for Provider Usage ---
    
    def has_server(self, name: str) -> bool:
        """Return True if a named MCP server is connected and has tools loaded."""
        server = self.servers.get(name)
        return bool(server and server.get('tools'))

    def get_all_tools(self) -> List[Dict]:
        """
        Synchronously return a unified list of all tools in a standard generic dict format
        ready to be parsed by Gemini/OpenAI/Ollama.
        """
        future = asyncio.run_coroutine_threadsafe(self._async_get_all_tools(), self._loop)
        return future.result(timeout=10)
        
    async def _async_get_all_tools(self) -> List[Dict]:
        all_tools = []
        for s_name, server in self.servers.items():
            if not server['tools']: continue
            for t in server['tools']:
                # t is an mcp.types.Tool object with name, description, inputSchema
                tool_dict = {
                    "name": getattr(t, "name", "unknown"),
                    "description": getattr(t, "description", ""),
                    "inputSchema": getattr(t, "inputSchema", {}),
                    "_server": s_name
                }
                all_tools.append(tool_dict)
        return all_tools
        
    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """
        Synchronously route and execute a tool call string.
        """
        future = asyncio.run_coroutine_threadsafe(self._async_call_tool(tool_name, arguments), self._loop)
        return future.result(timeout=60) # Some tools might take time
        
    async def _async_call_tool(self, tool_name: str, arguments: dict) -> Any:
        # Find which server owns this tool
        target_server_name = None
        target_server = None
        for s_name, server in self.servers.items():
            if not server['tools']: continue
            for t in server['tools']:
                if getattr(t, 'name') == tool_name:
                    target_server_name = s_name
                    target_server = server
                    break
            if target_server: break
            
        if not target_server:
            return f"Error: Tool '{tool_name}' not found on any active MCP server."
            
        try:
            if target_server['type'] == 'internal':
                # Execute native sync/async fastmcp tool
                result = await internal_meshtastic_mcp.call_tool(tool_name, arguments)
                return result
            elif target_server['type'] == 'stdio':
                # Execute via MCP session
                result = await target_server['session'].call_tool(tool_name, arguments)
                # Parse CallToolResult
                content_list = []
                for content in result.content:
                    if getattr(content, 'type', '') == 'text':
                        content_list.append(content.text)
                return "\n".join(content_list)
        except Exception as e:
            logger.error(f"Error calling MCP tool {tool_name}: {e}")
            return f"Tool execution failed: {e}"
