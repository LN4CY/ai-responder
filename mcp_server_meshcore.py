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

@mcp.tool()
def schedule_message(delay_seconds: float = None, context_note: str = None, 
                     recur_interval_seconds: float = None, max_duration_seconds: float = None, 
                     notify_targets: str = None, absolute_time: str = None) -> str:
    """
    Schedule a reminder or recurring status update.
    - delay_seconds: seconds from now to send the first alert (required for one-shot reminders).
    - context_note: instruction/context for your future self (e.g. "Remind Bob to check antenna").
    - recur_interval_seconds: interval between recurring alerts (e.g. 300 for every 5 min).
    - max_duration_seconds: total duration to keep repeating (default: 3600s / 1hr).
    - notify_targets: comma-separated list of target contacts/nodes or 'ch:0'/'ch:4'. Defaults to caller.
    - absolute_time: target time in HH:MM format (system runs UTC). Prefer delay_seconds when possible.
    """
    if not responder_app:
        return "Error: AI Responder unavailable"
    ctx = _get_caller_context()
    return responder_app._schedule_mcp_task(
        delay_seconds=delay_seconds, context_note=context_note, 
        recur_interval_seconds=recur_interval_seconds, max_duration_seconds=max_duration_seconds, 
        notify_targets=notify_targets, absolute_time=absolute_time,
        from_node=ctx['from_node'], to_node=ctx['to_node'], channel=ctx['channel']
    )

@mcp.tool()
def watch_condition(node_id_or_name: str, metric: str, operator: str, threshold: float, context_note: str, notify_targets: str = None, is_persistent: bool = False) -> str:
    """
    Set an alert when a contact's telemetry threshold is met (metrics: battery_level, voltage, snr; operators: <, >, <=, >=, ==).
    notify_targets can be 'requester' (default), a contact name/ID, or 'ch:0'/'ch:4'.
    """
    if not responder_app:
        return "Error: AI Responder unavailable"
    ctx = _get_caller_context()
    return responder_app._watch_condition_mcp(
        node_id_or_name=node_id_or_name, metric=metric, operator=operator, threshold=threshold,
        context_note=context_note, notify_targets=notify_targets, is_persistent=is_persistent,
        from_node=ctx['from_node'], to_node=ctx['to_node'], channel=ctx['channel']
    )

@mcp.tool()
def watch_node_online(node_id_or_name: str, context_note: str, notify_targets: str = None, is_persistent: bool = False) -> str:
    """
    Alert when a specific contact/node comes online or sends any packet.
    notify_targets can be 'requester' (default), a contact name/ID, or 'ch:0'/'ch:4'.
    """
    if not responder_app:
        return "Error: AI Responder unavailable"
    ctx = _get_caller_context()
    return responder_app._watch_node_online_mcp(
        node_id_or_name=node_id_or_name, context_note=context_note, 
        notify_targets=notify_targets, is_persistent=is_persistent,
        from_node=ctx['from_node'], to_node=ctx['to_node'], channel=ctx['channel']
    )

@mcp.tool()
def list_proactive_tasks() -> str:
    """List all active proactive tasks for the current user."""
    if not responder_app:
        return "Error: AI Responder unavailable"
    ctx = _get_caller_context()
    return responder_app._list_proactive_tasks_mcp(from_node=ctx['from_node'])

@mcp.tool()
def cancel_proactive_task(task_id: str) -> str:
    """Cancel and remove a scheduled task or watcher (supply ID or 'all' to delete/cancel all active tasks). Note: tasks that reach their duration or finish all runs are automatically deleted upon completion."""
    if not responder_app:
        return "Error: AI Responder unavailable"
    ctx = _get_caller_context()
    return responder_app._cancel_proactive_task_mcp(task_id=task_id, from_node=ctx['from_node'])

@mcp.tool()
def send_message(target: str, message: str) -> str:
    """Send a message to another contact ID/name or channel (like 'ch:0' or 'ch:4') immediately."""
    if not responder_app:
        return "Error: AI Responder unavailable"
    return responder_app._send_message_mcp(target=target, message=message)
