import os
import json
import logging
import asyncio
import threading
from typing import Dict, Any, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

# Local tools
from mcp_server_meshtastic import mcp as internal_meshtastic_mcp

logger = logging.getLogger(__name__)

class UnifiedMCPClient:
    """
    Manages connections to multiple MCP servers (both internal and external via sse/stdio).
    Provides synchronous wrappers for async MCP operations that lazily initialize.
    """

    def __init__(self, config):
        self.config = config
        self.servers = {} # {name: {'session': ClientSession, 'tools': list[Tool]}}
        self._loop = None
        self._thread = None
        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._started = False

    def _ensure_started(self):
        """Lazily starts the background event loop thread if not already running."""
        if self._started and self._ready_event.is_set():
            return
            
        with self._lock:
            if self._started:
                # Still wait if it is starting but not ready
                self._ready_event.wait(timeout=10.0)
                return
            
            print("[MCP] Handshaking background thread...", flush=True)
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MCP_Client_Loop")
            self._thread.start()
            self._started = True
            
            # Critical: Wait for the loop to be running before returning
            # This prevents run_coroutine_threadsafe from hanging
            if not self._ready_event.wait(timeout=10.0):
                logger.error("MCP background thread failed to signal ready in time.")

    def _run_loop(self):
        """Run the dedicated asyncio event loop for MCP clients."""
        asyncio.set_event_loop(self._loop)
        
        # Schedule initialization immediately on the loop's first tick
        self._loop.call_soon_threadsafe(self._initialize_servers)
        
        # Signal that the loop is officially running
        self._ready_event.set()
        
        print("[MCP] Event loop healthy and ready.", flush=True)
        self._loop.run_forever()

    def _initialize_servers(self):
        """Internal wrapper to schedule initialization on the loop."""
        # Parallel tasks for isolation
        asyncio.create_task(self._init_internal_server())
        
        from config import MEMPALACE_URL
        if MEMPALACE_URL:
            asyncio.create_task(self._connect_sse_server('mempalace', MEMPALACE_URL))
        
        mcp_servers_file = self.config.get('mcp_servers_file', '/app/data/mcp_servers.json')
        if os.path.exists(mcp_servers_file):
            asyncio.create_task(self._load_json_servers(mcp_servers_file))

    async def _load_json_servers(self, file_path: str):
        """Load external servers from JSON and launch their tasks."""
        try:
            with open(file_path, 'r') as f:
                servers_config = json.load(f)
                from config import MEMPALACE_URL
                for name, cfg in servers_config.items():
                    if name == 'mempalace' and MEMPALACE_URL:
                        continue
                    url = cfg.get('url')
                    cmd = cfg.get('command')
                    if url:
                        asyncio.create_task(self._connect_sse_server(name, url))
                    elif cmd:
                        args = cfg.get('args', [])
                        env = cfg.get('env')
                        asyncio.create_task(self._connect_stdio_server(name, cmd, args, env))
        except Exception as e:
            logger.error(f"Failed to load JSON servers: {e}")

    async def _init_internal_server(self):
        """Initialize the in-process tools."""
        print("[MCP] Initializing internal meshtastic tools...", flush=True)
        try:
            tools = await internal_meshtastic_mcp.list_tools()
            self.servers['meshtastic'] = {
                'type': 'internal',
                'tools': tools,
                'session': None
            }
            print(f"[MCP] Internal tools loaded: {len(tools)} items.", flush=True)
            logger.info("Internal Meshtastic MCP tools loaded.")
        except Exception as e:
            print(f"[MCP] Internal tools FAILED: {e}", flush=True)
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
            
    async def _connect_sse_server(self, name: str, url: str):
        """Connect to an external MCP server via SSE with exponential backoff."""
        base_delay = 5
        max_delay = 300
        attempt = 0

        while True:
            # Calculate delay at the top so it's always defined when we reach the sleep.
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay) if attempt > 0 else 0
            try:
                print(f"[MCP] Connecting to SSE {name} at {url}...", flush=True)
                async with sse_client(url) as (read_ctx, write_ctx):
                    print(f"[MCP] SSE transport established for {name}.", flush=True)
                    async with ClientSession(read_ctx, write_ctx) as session:
                        print(f"[MCP] Initializing session {name}...", flush=True)
                        await asyncio.wait_for(session.initialize(), timeout=15.0)

                        print(f"[MCP] Fetching tools for {name}...", flush=True)
                        tools_result = await asyncio.wait_for(session.list_tools(), timeout=15.0)
                        tools = tools_result.tools

                        self.servers[name] = {
                            'type': 'sse',
                            'session': session,
                            'tools': tools,
                            'url': url
                        }
                        print(f"[MCP] {name} CONNECTED with {len(tools)} tools.", flush=True)
                        attempt = 0

                        # Keep alive as long as the context manager is open and the connection is healthy
                        while True:
                            await asyncio.sleep(30)
                            try:
                                await asyncio.wait_for(session.send_ping(), timeout=10.0)
                            except Exception as e:
                                logger.warning(f"MCP server '{name}' ping failed or timed out: {e}")
                                break
            except asyncio.CancelledError:
                # Bug fix: anyio's TaskGroup cleanup can leak CancelledError into our
                # outer coroutine even though our asyncio Task was not externally cancelled.
                # Only propagate if this task truly has a pending cancel() from the outside;
                # otherwise treat it as a transient connection failure and keep retrying.
                self.servers.pop(name, None)
                # task.cancelling() is Python 3.12+; fall back to 0 on older runtimes.
                task = asyncio.current_task()
                if task is not None and getattr(task, 'cancelling', lambda: 0)() > 0:
                    raise
                attempt += 1
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                print(f"[MCP] {name} disconnected (CancelledError). Retrying in {delay}s...", flush=True)
                logger.warning(f"Remote MCP {name}: CancelledError during cleanup, retrying in {delay}s")
            except (KeyboardInterrupt, SystemExit):
                self.servers.pop(name, None)
                raise
            except BaseException as e:
                # Catching BaseException to handle TaskGroup ExceptionGroups
                # Bug fix: always remove the stale session so callers don't keep
                # trying to use a dead ClientSession.
                self.servers.pop(name, None)
                attempt += 1
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                msg = f"Remote MCP {name} error: {e}"
                print(f"[MCP] {msg}. Retrying in {delay}s...", flush=True)
                logger.error(msg)

            await asyncio.sleep(delay)
            
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
        self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(self._async_get_all_tools(), self._loop)
        return future.result(timeout=20)
        
    async def _async_get_all_tools(self) -> List[Dict]:
        all_tools = []
        for s_name, server in self.servers.items():
            if not server['tools']:
                continue
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
        """Synchronously route and execute a tool call string."""
        self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(self._async_call_tool(tool_name, arguments), self._loop)
        return future.result(timeout=60) # Some tools might take time
        
    async def _async_call_tool(self, tool_name: str, arguments: dict) -> Any:
        # Find which server owns this tool
        target_server = None
        for s_name, server in self.servers.items():
            if not server['tools']:
                continue
            for t in server['tools']:
                if getattr(t, 'name') == tool_name:
                    target_server = server
                    break
            if target_server:
                break
            
        if not target_server:
            return f"Error: Tool '{tool_name}' not found on any active MCP server."

        # Log the operation being performed with a human-readable summary
        server_name = next(
            (n for n, s in self.servers.items() if s is target_server), "unknown"
        )
        # Build a concise argument summary (truncate long values)
        arg_summary = ", ".join(
            f"{k}={repr(v)[:60]}" for k, v in (arguments or {}).items()
        )
        logger.info(f"MCP call [{server_name}] {tool_name}({arg_summary})")

        try:
            if target_server['type'] == 'internal':
                # Execute native sync/async fastmcp tool
                result = await internal_meshtastic_mcp.call_tool(tool_name, arguments)
                return result
            elif target_server['type'] in ('stdio', 'sse'):
                # Execute via MCP session
                result = await target_server['session'].call_tool(tool_name, arguments)
                # Parse CallToolResult
                content_list = []
                for content in result.content:
                    if getattr(content, 'type', '') == 'text':
                        content_list.append(content.text)
                return "\n".join(content_list)
        except Exception as e:
            logger.exception(f"Error calling MCP tool {tool_name}")
            return f"Tool execution failed: {e}"
