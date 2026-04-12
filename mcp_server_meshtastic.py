import logging
import threading
from typing import Dict, Any
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# The FastMCP server instance
mcp = FastMCP("Meshtastic Server")

# Global reference to AIResponder instance for accessing meshtastic handler and session manager
responder_app = None

def init_meshtastic_mcp(app_instance):
    """Initialize the MCP server by providing the AIResponder instance for state access."""
    global responder_app
    responder_app = app_instance
    logger.info("Meshtastic MCP Server initialized with AI Responder state.")

def _get_caller_context():
    """Retrieve the caller context injected by AIResponder."""
    thread_local = getattr(threading.current_thread(), "ai_context", {})
    return {
        'from_node': thread_local.get('from_node', '!unknown'),
        'channel': thread_local.get('channel', 0),
        'to_node': thread_local.get('to_node', '^all')
    }

@mcp.tool()
def get_my_info() -> Dict[str, Any]:
    """Get information about the local bot/node."""
    if not responder_app or not responder_app.meshtastic:
        return {"error": "Meshtastic handler unavailable"}
    info = responder_app.meshtastic.get_node_info()
    return info if info else {"error": "Failed to get node info"}

@mcp.tool()
def get_mesh_nodes() -> str:
    """Get summarized list of active mesh nodes with their distances from the bot."""
    if not responder_app or not responder_app.meshtastic:
        return "Error: Meshtastic handler unavailable"
    return responder_app.meshtastic.get_node_list_summary()

@mcp.tool()
def get_node_details(node_id_or_name: str) -> str:
    """Get cached Meshtastic data for a node including SNR, Battery, Temp, Hum, Air Quality, etc."""
    if not responder_app or not responder_app.meshtastic:
        return "Error: Meshtastic handler unavailable"
        
    id_format = node_id_or_name
    if not str(id_format).startswith('!') and not str(id_format).isdigit():
        found_id = responder_app.meshtastic.find_node_by_name(str(id_format))
        if found_id:
            id_format = found_id
            
    meta = responder_app.meshtastic.get_node_metadata(id_format)
    return meta if meta else "No cached data found for this node. Use request_node_telemetry if needed."

@mcp.tool()
def request_node_telemetry(node_id_or_name: str, telemetry_type: str) -> str:
    """
    Trigger an active refresh of telemetry from a specific node.
    telemetry_type can be: device, environment, local_stats, air_quality, power, health, host.
    """
    if not responder_app:
        return "Error"
    ctx = _get_caller_context()
    return responder_app._request_node_telemetry_mcp(
        node_id_or_name=node_id_or_name, 
        telemetry_type=telemetry_type,
        from_node=ctx['from_node'],
        to_node=ctx['to_node'],
        channel=ctx['channel']
    )

@mcp.tool()
def schedule_message(delay_seconds: float = None, context_note: str = "", recur_interval_seconds: int = None, max_duration_seconds: int = None, notify_targets: str = None, absolute_time: str = None) -> str:
    """Schedule a reminder or recurring proactive task. Returns the task ID."""
    if not responder_app:
        return "Error"
    ctx = _get_caller_context()
    return responder_app._schedule_mcp_task(
        delay_seconds=delay_seconds, context_note=context_note, 
        recur_interval_seconds=recur_interval_seconds, max_duration_seconds=max_duration_seconds, 
        notify_targets=notify_targets, absolute_time=absolute_time,
        from_node=ctx['from_node'], to_node=ctx['to_node'], channel=ctx['channel']
    )

@mcp.tool()
def watch_condition(node_id_or_name: str, metric: str, operator: str, threshold: float, context_note: str, notify_targets: str = None, is_persistent: bool = False) -> str:
    """Set an alert when a node's telemetry threshold is met."""
    if not responder_app:
        return "Error"
    ctx = _get_caller_context()
    return responder_app._watch_condition_mcp(
        node_id_or_name=node_id_or_name, metric=metric, operator=operator, threshold=threshold,
        context_note=context_note, notify_targets=notify_targets, is_persistent=is_persistent,
        from_node=ctx['from_node'], to_node=ctx['to_node'], channel=ctx['channel']
    )

@mcp.tool()
def watch_node_online(node_id_or_name: str, context_note: str, notify_targets: str = None, is_persistent: bool = False) -> str:
    """Alert when a specific node comes online or sends any packet."""
    if not responder_app:
        return "Error"
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
        return "Error"
    ctx = _get_caller_context()
    return responder_app._list_proactive_tasks_mcp(from_node=ctx['from_node'])

@mcp.tool()
def cancel_proactive_task(task_id: str) -> str:
    """Cancel a scheduled task or watcher (supply ID or 'all')."""
    if not responder_app:
        return "Error"
    ctx = _get_caller_context()
    return responder_app._cancel_proactive_task_mcp(task_id=task_id, from_node=ctx['from_node'])

@mcp.tool()
def send_message(target: str, message: str) -> str:
    """Send a message to another node ID or channel (like 'ch:0') immediately."""
    if not responder_app:
        return "Error"
    return responder_app._send_message_mcp(target=target, message=message)

@mcp.tool()
def get_location_address(lat: float, lon: float) -> str:
    """Convert latitude and longitude coordinates into a real-world address."""
    if not responder_app:
        return "Error"
    return responder_app._get_location_address_mcp(lat=lat, lon=lon)
