# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""FastMCP server exposing MeshCore tools to the AI providers."""

import logging
import threading
from typing import Dict, Any
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("MeshCore Server")
responder_app = None

def init_meshcore_mcp(app_instance):
    """Initialize the MCP server by providing the AIResponder instance for state access."""
    global responder_app
    responder_app = app_instance
    logger.info("MeshCore MCP Server initialized with AI Responder state.")

def _get_caller_context():
    """Retrieve the caller context injected by AIResponder."""
    thread_local = getattr(threading.current_thread(), "ai_context", {})
    return {
        'from_node': thread_local.get('from_node', '!unknown'),
        'channel': thread_local.get('channel', 0),
        'to_node': thread_local.get('to_node', '^all')
    }

def _sanitize_for_json(obj: Any) -> Any:
    """Recursively converts non-serializable objects to JSON-friendly structures."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items() if not k.startswith('_')}
    elif isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)

@mcp.tool()
def get_meshcore_self_info() -> Dict[str, Any]:
    """Get information about the local MeshCore node/bot (public key, name, radio settings)."""
    if not responder_app or not getattr(responder_app, 'meshcore', None):
        return {"error": "MeshCore handler unavailable"}
    info = responder_app.meshcore.get_node_info()
    return _sanitize_for_json(info) if info else {"error": "Failed to get MeshCore node info"}

@mcp.tool()
def get_meshcore_contacts() -> str:
    """Get the authoritative list of active MeshCore contacts and neighbors, including names and hop distances."""
    if not responder_app or not getattr(responder_app, 'meshcore', None):
        return "Error: MeshCore handler unavailable"
    return responder_app.meshcore.get_node_list_summary()

@mcp.tool()
def get_meshcore_contact_details(contact_id_or_name: str) -> str:
    """Get detailed telemetry, public key, hops, and coordinates for a specific MeshCore contact."""
    if not responder_app or not getattr(responder_app, 'meshcore', None):
        return "Error: MeshCore handler unavailable"
    return responder_app.meshcore.get_node_metadata(contact_id_or_name)

@mcp.tool()
def send_meshcore_message(destination: str, text: str, channel: int = 0) -> str:
    """
    Send a message over the MeshCore network.
    - destination: Contact name/prefix for DM, or '^all' for channel broadcast.
    - text: Message content to send.
    - channel: Channel index (default: 0).
    """
    if not responder_app or not getattr(responder_app, 'meshcore', None):
        return "Error: MeshCore handler unavailable"
    success = responder_app.meshcore.send_message(text=text, destination_id=destination, channel_index=channel)
    return "Message queued for transmission on MeshCore" if success else "Failed to queue message on MeshCore"
