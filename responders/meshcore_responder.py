# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""MeshCore protocol-specific AI Responder implementation."""

from core.base_responder import BaseAIResponder
from core._compat import (
    get_config_module,
    get_mcp_client_cls,
    get_logger,
    resolve_attr,
)
from meshcore_handler import MeshCoreHandler as _StdMeshCoreHandler
from mcp_server_meshcore import init_meshcore_mcp


class MeshCoreAIResponder(BaseAIResponder):
    """
    Dedicated AI Responder implementation for the MeshCore protocol.

    Completely isolated from Meshtastic: uses `MeshCoreHandler`, registers
    `mcp_server_meshcore` tools, and manages MeshCore-specific admin identities
    (contact names like `LN4CY_TDeck` and 6-byte hex public key prefixes like
    `12A1B2DD1DE3` / `!12A1B2DD1DE3`) and channels (Channel 0 public broadcast).
    """

    mesh_type = "meshcore"

    def __init__(self, history_dir=None):
        """Initialize MeshCore AI Responder."""
        config = get_config_module()
        MeshCoreHandlerCls = resolve_attr('MeshCoreHandler', _StdMeshCoreHandler)
        UnifiedMCPClientCls = get_mcp_client_cls()

        self._init_common_state(history_dir=history_dir)

        interface_type = getattr(config, 'MESHCORE_INTERFACE', 'tcp')
        serial_port = getattr(config, 'MESHCORE_SERIAL_PORT', '/dev/ttyUSB1')
        tcp_host = getattr(config, 'MESHCORE_HOST', '127.0.0.1')
        tcp_port = getattr(config, 'MESHCORE_PORT', 5000)
        baudrate = getattr(config, 'MESHCORE_BAUDRATE', 115200)

        self.meshcore = MeshCoreHandlerCls(
            interface_type=interface_type,
            serial_port=serial_port,
            tcp_host=tcp_host,
            tcp_port=tcp_port,
            baudrate=baudrate,
        )
        self.radio = self.meshcore
        # Alias for base mixins that reference self.meshtastic duck-typed methods
        self.meshtastic = self.meshcore

        # Initialize MeshCore MCP Server & Client
        init_meshcore_mcp(self)
        self.mcp_client = UnifiedMCPClientCls(self.config)

        # Initialize MeshCore admin nodes & allowed channels from environment
        env_admin = resolve_attr('ENV_ADMIN_NODE_ID', getattr(config, 'ENV_ADMIN_NODE_ID', None))
        mc_admin = resolve_attr('MESHCORE_ADMIN_NODE_ID', getattr(config, 'MESHCORE_ADMIN_NODE_ID', None))
        self._merge_env_list(env_admin, 'meshcore_admin_nodes')
        self._merge_env_list(mc_admin, 'meshcore_admin_nodes')
        self._merge_env_list(env_admin, 'admin_nodes')
        self._merge_env_list(mc_admin, 'admin_nodes')
        self._cleanup_admin_list('meshcore_admin_nodes')
        self._cleanup_admin_list('admin_nodes')

        env_channels = resolve_attr('ALLOWED_CHANNELS', getattr(config, 'ALLOWED_CHANNELS', None))
        mc_channels = resolve_attr('MESHCORE_ALLOWED_CHANNELS', getattr(config, 'MESHCORE_ALLOWED_CHANNELS', None))
        self._merge_env_list(env_channels, 'meshcore_allowed_channels', is_int=True)
        self._merge_env_list(mc_channels, 'meshcore_allowed_channels', is_int=True)
        self._merge_env_list(env_channels, 'allowed_channels', is_int=True)
        self._merge_env_list(mc_channels, 'allowed_channels', is_int=True)

        self._init_provider_from_env()
        self.touch_health()

    # ==================== Admin & Channel Permissions ====================

    def is_admin(self, node_id):
        """
        Check if a MeshCore contact name or public key prefix is an admin.

        Supports:
          - Exact contact name (e.g. 'LN4CY_TDeck', case-insensitive)
          - 6-byte (12-char) hex public key prefix with or without '!'
            (e.g. '12A1B2DD1DE3' or '!12A1B2DD1DE3', case-insensitive)
          - Cross-lookup via MeshCore's contact book (matching name <-> key prefix)

        Args:
            node_id: Contact name or public key prefix from incoming MeshCore packet

        Returns:
            bool: True if contact is admin
        """
        target_admins = (
            self.config.get('meshcore_admin_nodes')
            or self.config.get('admin_nodes', [])
        )
        if not target_admins:
            return False

        node_str = str(node_id).strip()
        node_clean = node_str.lstrip('!').lower()

        # 1. Direct or normalized match against admin list
        for admin in target_admins:
            admin_str = str(admin).strip()
            if admin_str == node_str or admin_str.lower() == node_str.lower():
                return True
            if admin_str.lstrip('!').lower() == node_clean:
                return True

        # 2. Cross-resolve against MeshCore contact book (name <-> public_key prefix)
        if hasattr(self, 'meshcore') and self.meshcore and getattr(self.meshcore, 'meshcore', None):
            try:
                mc = self.meshcore.meshcore
                contact = mc.get_contact_by_name(node_str) or mc.get_contact_by_key_prefix(node_clean)
                if contact:
                    c_name = str(contact.get('adv_name') or contact.get('name') or '').strip().lower()
                    c_prefix = str(contact.get('public_key', ''))[:12].strip().lower()
                    for admin in target_admins:
                        admin_clean = str(admin).strip().lstrip('!').lower()
                        if c_name and admin_clean == c_name:
                            return True
                        if c_prefix and admin_clean == c_prefix:
                            return True
            except Exception:
                pass

        return False

    def is_channel_allowed(self, channel_index):
        """
        Check if a MeshCore channel index is allowed for public AI responses.

        Args:
            channel_index: Channel index (0 is default MeshCore public broadcast)

        Returns:
            bool: True if channel is allowed
        """
        mc_channels = self.config.get('meshcore_allowed_channels')
        if mc_channels is not None:
            return channel_index in mc_channels
        return channel_index in self.config.get('allowed_channels', [0])

    def _is_dm(self, to_node):
        """Determine if a MeshCore message destination is a Direct Message to this bot."""
        if not to_node or str(to_node).lower() in ('^all', '!ffffffff', 'unknown', '4294967295', 'broadcast'):
            return False
        try:
            if self.meshcore and hasattr(self.meshcore, 'get_node_info'):
                mc_info = self.meshcore.get_node_info()
                mc_id = mc_info.get('user', {}).get('id', '') if isinstance(mc_info, dict) else ''
                mc_name = mc_info.get('user', {}).get('longName', '') if isinstance(mc_info, dict) else ''
                if str(to_node).lower() in (mc_id.lower(), mc_name.lower(), 'bot'):
                    return True
        except Exception:
            pass
        return True

    def send_response(self, text, from_node, to_node, channel, is_admin_cmd=False, use_session_indicator=False):
        """Send a response message via MeshCore."""
        logger = get_logger()
        if is_admin_cmd:
            destination = from_node
        elif to_node == '^all':
            if self.is_channel_allowed(channel):
                destination = '^all'
                use_session_indicator = False
            else:
                logger.info(f"Skipping MeshCore response on disabled channel {channel}")
                return
        else:
            destination = from_node

        session_indicator = ""
        if use_session_indicator:
            session_indicator = self.session_manager.get_session_indicator(from_node)

        self.meshcore.send_message(text, destination, channel, session_indicator)

    def _handle_ai_query(self, query, from_node, to_node, channel, is_dm=None, initial_msg="Thinking... 🤖"):
        """
        Handle an AI query for MeshCore.

        By default, suppresses initial 'Thinking... 🤖' transmission over LoRa
        to preserve channel bandwidth and prevent ACK collisions / duplicate retries.
        """
        config = get_config_module()
        send_thinking = getattr(config, 'MESHCORE_SEND_THINKING', False)
        if not send_thinking:
            initial_msg = None
        super()._handle_ai_query(query, from_node, to_node, channel, is_dm=is_dm, initial_msg=initial_msg)

    # ==================== Protocol-Specific Admin Commands ====================

    def _handle_channel_command(self, args, from_node, to_node, channel):
        """Handle MeshCore channel management (!ai -ch)."""
        fallback = self.config.get('allowed_channels', [0])
        allowed = list(self.config.get('meshcore_allowed_channels', fallback))

        parts = args.split(maxsplit=1) if args else []
        action = parts[0].lower() if parts else ""

        if not action or action == 'ls':
            lines = [
                f"📡 MeshCore Allowed Channels: {', '.join(map(str, allowed))}",
                "(Channel 0 is the default broadcast channel)"
            ]
            self.send_response("\n".join(lines), from_node, to_node, channel, is_admin_cmd=True)
            return

        if len(parts) < 2 or action not in ['add', 'rm']:
            self.send_response("Usage: !ai -ch [ls|add <id>|rm <id>] (MeshCore)", from_node, to_node, channel, is_admin_cmd=True)
            return

        channel_id_str = parts[1]
        if not channel_id_str.isdigit():
            self.send_response("Channel ID must be a number", from_node, to_node, channel, is_admin_cmd=True)
            return

        channel_id = int(channel_id_str)

        if action == 'add':
            if channel_id not in allowed:
                allowed.append(channel_id)
                self.config['meshcore_allowed_channels'] = allowed
                self.config['allowed_channels'] = allowed
                self.config.save()
                self.send_response(f"✅ Added MeshCore channel {channel_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"MeshCore channel {channel_id} already allowed", from_node, to_node, channel, is_admin_cmd=True)

        elif action == 'rm':
            if channel_id in allowed:
                allowed.remove(channel_id)
                self.config['meshcore_allowed_channels'] = allowed
                self.config['allowed_channels'] = allowed
                self.config.save()
                self.send_response(f"✅ Removed MeshCore channel {channel_id}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"MeshCore channel {channel_id} not in allowed list", from_node, to_node, channel, is_admin_cmd=True)

        else:
            self.send_response("Usage: !ai -ch add/rm <channel_id> (MeshCore)", from_node, to_node, channel, is_admin_cmd=True)

    def _handle_admin_command(self, args, from_node, to_node, channel):
        """Handle MeshCore admin node management (!ai -a)."""
        fallback = self.config.get('admin_nodes', [])
        admins = list(self.config.get('meshcore_admin_nodes', fallback))

        if not args or args.strip().lower() == 'ls':
            if admins:
                message = "👑 MeshCore Admin Nodes:\n" + "\n".join(admins)
            else:
                message = "No MeshCore admin nodes configured"
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return

        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            self.send_response("Usage: !ai -a [ls|add <id>|rm <id>] (MeshCore)", from_node, to_node, channel, is_admin_cmd=True)
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
                    messages.append(f"✅ Added {nid} to MeshCore")
                    updated = True
                else:
                    messages.append(f"ℹ️ {nid} is already MeshCore admin")

            if updated:
                self.config['meshcore_admin_nodes'] = admins
                self.config['admin_nodes'] = admins
                self.config.save()

            self.send_response("\n".join(messages), from_node, to_node, channel, is_admin_cmd=True)

        elif action == 'rm':
            for nid in nodes_to_process:
                if nid in admins:
                    admins.remove(nid)
                    messages.append(f"✅ Removed {nid} from MeshCore")
                    updated = True
                else:
                    messages.append(f"ℹ️ {nid} not in MeshCore admin list")

            if updated:
                self.config['meshcore_admin_nodes'] = admins
                self.config['admin_nodes'] = admins
                self.config.save()

            self.send_response("\n".join(messages), from_node, to_node, channel, is_admin_cmd=True)

        else:
            self.send_response("Usage: !ai -a add/rm <node_id>", from_node, to_node, channel, is_admin_cmd=True)
