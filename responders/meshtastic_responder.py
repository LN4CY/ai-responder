# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Meshtastic protocol-specific AI Responder implementation."""

from core.base_responder import BaseAIResponder
from core._compat import (
    get_time,
    get_config_module,
    get_mcp_client_cls,
    get_logger,
    resolve_attr,
)
from meshtastic_handler import MeshtasticHandler as _StdMeshtasticHandler
from mcp_server_meshtastic import init_meshtastic_mcp


class MeshtasticAIResponder(BaseAIResponder):
    """
    Dedicated AI Responder implementation for the Meshtastic protocol.

    Completely isolated from MeshCore: uses `MeshtasticHandler`, registers
    `mcp_server_meshtastic` tools, and manages Meshtastic-specific admin node IDs
    (`!xxxxxxxx`) and channels (`0-7`).
    """

    mesh_type = "meshtastic"

    def __init__(self, history_dir=None):
        """Initialize Meshtastic AI Responder."""
        config = get_config_module()
        MeshtasticHandlerCls = resolve_attr('MeshtasticHandler', _StdMeshtasticHandler)
        UnifiedMCPClientCls = get_mcp_client_cls()

        self._init_common_state(history_dir=history_dir)

        interface_type = resolve_attr('INTERFACE_TYPE', getattr(config, 'INTERFACE_TYPE', 'tcp'))
        serial_port = resolve_attr('SERIAL_PORT', getattr(config, 'SERIAL_PORT', '/dev/ttyUSB0'))
        tcp_host = resolve_attr('MESHTASTIC_HOST', getattr(config, 'MESHTASTIC_HOST', 'localhost'))
        tcp_port = resolve_attr('MESHTASTIC_PORT', getattr(config, 'MESHTASTIC_PORT', 4403))

        self.meshtastic = MeshtasticHandlerCls(
            interface_type=interface_type,
            serial_port=serial_port,
            tcp_host=tcp_host,
            tcp_port=tcp_port,
        )
        self.radio = self.meshtastic

        # Initialize Meshtastic MCP Server & Client
        init_meshtastic_mcp(self)
        self.mcp_client = UnifiedMCPClientCls(self.config)

        # Initialize Meshtastic admin nodes & allowed channels from environment
        env_admin = resolve_attr('ENV_ADMIN_NODE_ID', getattr(config, 'ENV_ADMIN_NODE_ID', None))
        msh_admin = resolve_attr('MESHTASTIC_ADMIN_NODE_ID', getattr(config, 'MESHTASTIC_ADMIN_NODE_ID', None))
        self._merge_env_list(env_admin, 'admin_nodes')
        self._merge_env_list(msh_admin, 'admin_nodes')
        self._cleanup_admin_list('admin_nodes')

        env_channels = resolve_attr('ALLOWED_CHANNELS', getattr(config, 'ALLOWED_CHANNELS', None))
        msh_channels = resolve_attr('MESHTASTIC_ALLOWED_CHANNELS', getattr(config, 'MESHTASTIC_ALLOWED_CHANNELS', None))
        self._merge_env_list(env_channels, 'allowed_channels', is_int=True)
        self._merge_env_list(msh_channels, 'allowed_channels', is_int=True)

        self._init_provider_from_env()
        self.touch_health()

    # ==================== Admin & Channel Permissions ====================

    def is_admin(self, node_id):
        """
        Check if a Meshtastic node ID is an admin.

        Args:
            node_id: Meshtastic Node ID (e.g. '!9e044360')

        Returns:
            bool: True if node is admin
        """
        admin_nodes = self.config.get('meshtastic_admin_nodes') or self.config.get('admin_nodes', [])
        if not admin_nodes:
            return False

        node_str = str(node_id).strip()
        node_clean = node_str.lstrip('!').lower()

        for admin in admin_nodes:
            admin_str = str(admin).strip()
            if admin_str == node_str or admin_str.lower() == node_str.lower():
                return True
            if admin_str.lstrip('!').lower() == node_clean:
                return True
        return False

    def is_channel_allowed(self, channel_index):
        """
        Check if a Meshtastic channel index is allowed for public AI responses.

        Args:
            channel_index: Channel index (0-7)

        Returns:
            bool: True if channel is allowed
        """
        allowed_channels = self.config.get('meshtastic_allowed_channels')
        if allowed_channels is None:
            allowed_channels = self.config.get('allowed_channels', [0])
        return channel_index in allowed_channels

    def _is_dm(self, to_node):
        """Determine if a Meshtastic packet destination is a Direct Message to this bot."""
        if not to_node or str(to_node).lower() in ('^all', '!ffffffff', 'unknown', '4294967295', 'broadcast'):
            return False
        try:
            my_node_info = self.meshtastic.get_node_info() if self.meshtastic else None
            my_id = my_node_info.get('user', {}).get('id', '') if isinstance(my_node_info, dict) else ''
            if isinstance(my_id, str) and my_id.startswith('!'):
                return str(to_node).lower() == my_id.lower()
        except Exception:
            pass
        return True

    def send_response(self, text, from_node, to_node, channel, is_admin_cmd=False, use_session_indicator=False):
        """Send a response message via Meshtastic."""
        logger = get_logger()
        if is_admin_cmd:
            destination = from_node
        elif to_node == '^all':
            if self.is_channel_allowed(channel):
                destination = '^all'
                use_session_indicator = False
            else:
                logger.info(f"Skipping response on disabled channel {channel}")
                return
        else:
            destination = from_node

        session_indicator = ""
        if use_session_indicator:
            session_indicator = self.session_manager.get_session_indicator(from_node)

        self.meshtastic.send_message(text, destination, channel, session_indicator)

    # ==================== Protocol-Specific Admin Commands ====================

    def _handle_channel_command(self, args, from_node, to_node, channel):
        """Handle Meshtastic channel management (!ai -ch)."""
        allowed = list(self.config.get('allowed_channels', [0]))

        parts = args.split(maxsplit=1) if args else []
        action = parts[0].lower() if parts else ""

        if not action or action == 'ls':
            available_channels = self.meshtastic.get_channels() if self.meshtastic else []
            if not available_channels:
                message = f"📡 Allowed Channels: {', '.join(map(str, allowed))}\n(Could not retrieve available channels from node)"
            else:
                lines = ["📡 Channels:"]
                for ch in available_channels:
                    idx = ch['index']
                    name = ch.get('name') or "Unnamed"
                    status = "✅" if idx in allowed else "❌"
                    lines.append(f"{status} [{idx}] {name}")
                message = "\n".join(lines)

            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return

        if len(parts) < 2 or action not in ['add', 'rm']:
            self.send_response("Usage: !ai -ch [ls|add <id>|rm <id>]", from_node, to_node, channel, is_admin_cmd=True)
            return

        channel_id_str = parts[1]
        if not channel_id_str.isdigit():
            self.send_response("Channel ID must be a number", from_node, to_node, channel, is_admin_cmd=True)
            return

        channel_id = int(channel_id_str)

        if action == 'add':
            if channel_id not in allowed:
                allowed.append(channel_id)
                self.config['allowed_channels'] = allowed
                self.config.save()
                self.send_response(f"✅ Added channel {channel_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"Channel {channel_id} already allowed", from_node, to_node, channel, is_admin_cmd=True)

        elif action == 'rm':
            if channel_id in allowed:
                allowed.remove(channel_id)
                self.config['allowed_channels'] = allowed
                self.config.save()
                self.send_response(f"✅ Removed channel {channel_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"Channel {channel_id} not in allowed list", from_node, to_node, channel, is_admin_cmd=True)

        else:
            self.send_response("Usage: !ai -ch add/rm <channel_id>", from_node, to_node, channel, is_admin_cmd=True)

    def _handle_admin_command(self, args, from_node, to_node, channel):
        """Handle Meshtastic admin node management (!ai -a)."""
        admins = list(self.config.get('admin_nodes', []))

        if not args or args.strip().lower() == 'ls':
            if admins:
                message = "👑 Admin Nodes:\n" + "\n".join(admins)
            else:
                message = "No admin nodes configured"
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return

        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            self.send_response("Usage: !ai -a [ls|add <id>|rm <id>]", from_node, to_node, channel, is_admin_cmd=True)
            return

        action = parts[0].lower()
        node_input = parts[1]

        nodes_to_process = [n.strip() for n in node_input.split(',') if n.strip()]
        if not nodes_to_process:
            self.send_response("No valid node IDs provided.", from_node, to_node, channel, is_admin_cmd=True)
            return

        messages = []
        updated = False

        if action == 'add':
            for nid in nodes_to_process:
                if nid not in admins:
                    admins.append(nid)
                    messages.append(f"✅ Added {nid}")
                    updated = True
                else:
                    messages.append(f"ℹ️ {nid} is already admin")

            if updated:
                self.config['admin_nodes'] = admins
                self.config.save()

            self.send_response("\n".join(messages), from_node, to_node, channel, is_admin_cmd=True)

        elif action == 'rm':
            for nid in nodes_to_process:
                if nid in admins:
                    admins.remove(nid)
                    messages.append(f"✅ Removed {nid}")
                    updated = True
                else:
                    messages.append(f"ℹ️ {nid} not in admin list")

            if updated:
                self.config['admin_nodes'] = admins
                self.config.save()

            self.send_response("\n".join(messages), from_node, to_node, channel, is_admin_cmd=True)

        else:
            self.send_response("Usage: !ai -a add/rm <node_id>", from_node, to_node, channel, is_admin_cmd=True)

    # ==================== Meshtastic MCP Tool Handlers ====================

    def _get_node_details_tool(self, node_id_or_name):
        """Internal handler for get_node_details tool."""
        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found."

        metadata = self.meshtastic.get_node_metadata(node_id)
        if not metadata:
            return f"Error: No information available for {node_id}."
        return metadata

    def _request_node_telemetry_mcp(self, node_id_or_name, telemetry_type, from_node=None, to_node=None, channel=0):
        """Internal handler for request_node_telemetry tool with short polling."""
        logger = get_logger()
        time = get_time()
        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found."

        node_id = node_id.lower()

        type_map = {
            'device': 'device_metrics',
            'environment': 'environment_metrics',
            'local_stats': 'local_stats',
            'air_quality': 'air_quality_metrics',
            'power': 'power_metrics',
            'health': 'health_metrics',
            'host': 'host_metrics'
        }
        metric_key = type_map.get(telemetry_type, 'environment_metrics')

        self.pending_telemetry_requests[node_id] = {
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'context_note': f'{telemetry_type} telemetry for {node_id_or_name}',
            'metric_key': metric_key,
            'polling': True,
            'registered_at': time.time()
        }
        logger.info(f"⏳ Registered deferred telemetry callback for {node_id} (type={telemetry_type})")

        request_time = time.time()
        logger.info(f"📡 AI triggering telemetry refresh ({telemetry_type}) for {node_id}")
        self.meshtastic.request_telemetry(node_id, telemetry_type)

        poll_start = time.time()
        poll_timeout = 15

        while time.time() - poll_start < poll_timeout:
            self._touch_worker()

            node_timestamps = self.meshtastic.telemetry_timestamps.get(node_id.lower(), {})
            last_received = node_timestamps.get(metric_key, 0)

            if last_received > request_time:
                self.pending_telemetry_requests.pop(node_id, None)

                aggregated_notes = ""
                with self._collector_lock:
                    collector_entry = self._proactive_event_collector.pop(node_id, None)
                    if collector_entry:
                        if collector_entry['timer']:
                            collector_entry['timer'].cancel()
                        other_events = [e for e in collector_entry['events'] if "Requested" not in e]
                        if other_events:
                            aggregated_notes = "\n".join(other_events) + "\n\n"

                elapsed = int(last_received - request_time)
                logger.info(f"⚡ Fresh telemetry for {node_id} arrived in {elapsed}s and was consumed synchronously.")
                metadata = self.meshtastic.get_node_metadata(node_id)
                return f"Success! New telemetry received in {elapsed}s:\n{aggregated_notes}{metadata}"

            time.sleep(0.5)

        if node_id not in self.pending_telemetry_requests:
            logger.info(f"🔇 Telemetry for {node_id} was handled by proactive callback — suppressing AI reply.")
            return "__SILENT_ACK__"

        self.pending_telemetry_requests[node_id]['polling'] = False
        return (f"Refresh request for {telemetry_type} sent to {node_id_or_name}. "
                "The mesh is slow—I'm watching for the response. I will send it as soon as the data arrives!")

    def _on_connected_setup(self):
        """Subscribe to Meshtastic pubsub telemetry events for proactive watchers."""
        logger = get_logger()
        try:
            from pubsub import pub
            try:
                pub.unsubscribe(self._on_telemetry_proactive, "meshtastic.receive.telemetry")
            except Exception:
                pass
            pub.subscribe(self._on_telemetry_proactive, "meshtastic.receive.telemetry")
            logger.info("✅ Subscribed to meshtastic.receive.telemetry for proactive agents")
        except Exception as e:
            logger.warning(f"⚠️ Could not subscribe to telemetry for proactive agents: {e}")
