# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""
MeshCore interface handler.

This module manages the MeshCore connection and message handling,
including sending messages, managing connections (TCP for MeshMonitor Virtual Node
or Serial for Standalone), chunking, rate limiting, and normalizing incoming packets.
"""

import time
import logging
import threading
import asyncio
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Fallback in case meshcore isn't installed yet in some mock contexts
try:
    from meshcore import MeshCore, EventType
    from meshcore.events import Event
    HAS_MESHCORE = True
except ImportError:
    MeshCore = None
    EventType = None
    Event = None
    HAS_MESHCORE = False
    logger.warning("MeshCore package is not installed. MeshCoreHandler will operate in dummy/mock mode.")


class MeshCoreMessageQueue:
    """
    Background message queue processor for reliable sending on MeshCore.
    
    Features:
    - Thread-safe queueing
    - Handling of long messages (chunking)
    - Rate limiting and pacing between all transmissions
    - Sending to channels (send_chan_msg) or direct contacts (send_msg)
    """

    def __init__(self, handler: 'MeshCoreHandler'):
        self.handler = handler
        self.queue = []
        self.lock = threading.Lock()
        self.processing = False
        self.thread = None
        self.last_heartbeat = time.time()
        self.last_send_time = 0.0
        self.start()

    def start(self):
        """Start the processing thread."""
        if self.thread and self.thread.is_alive():
            return
        self.processing = True
        self.thread = threading.Thread(target=self._process_loop, daemon=True, name="MeshCore_MessageQueue")
        self.thread.start()
        logger.info("MeshCore MessageQueue processor started")

    def stop(self):
        """Stop the processing thread."""
        self.processing = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        logger.info("MeshCore MessageQueue processor stopped")

    def enqueue(self, text: str, destination_id: str, channel_index: int, session_indicator: str = "") -> bool:
        """Add a message to the queue."""
        with self.lock:
            import config
            max_size = getattr(config, 'MESH_MAX_QUEUE_SIZE', 100)
            if len(self.queue) >= max_size:
                logger.error(f"[MeshCore] Queue FULL ({max_size} msgs). Dropping message for {destination_id}")
                return False

            self.queue.append({
                'text': text,
                'dest': destination_id,
                'chan': channel_index,
                'sess': session_indicator,
                'time': time.time()
            })
            qsize = len(self.queue)
            if qsize >= (max_size * 0.8):
                logger.warning(f"[MeshCore] Queue nearly full: {qsize}/{max_size} messages pending")
            logger.debug(f"[MeshCore] Message queued for {destination_id} (Queue size: {qsize})")
            return True

    def _process_loop(self):
        """Main processing loop."""
        import config
        while self.processing:
            item = None
            with self.lock:
                if self.queue:
                    item = self.queue.pop(0)

            if item:
                # Enforce pacing between distinct message items
                chunk_delay = getattr(config, 'CHUNK_DELAY', 5)
                elapsed = time.time() - self.last_send_time
                if self.last_send_time > 0 and elapsed < chunk_delay:
                    sleep_time = chunk_delay - elapsed
                    logger.debug(f"[MeshCore] Pacing: Waiting {sleep_time:.1f}s before sending next message item...")
                    time.sleep(sleep_time)

                self._send_item(item)
                self.last_send_time = time.time()
                self.last_heartbeat = time.time()
            else:
                self.last_heartbeat = time.time()
                time.sleep(0.5)

    def _send_item(self, item: Dict[str, Any]):
        """Send a single queued message item, splitting into chunks if needed."""
        text = item['text']
        destination = item['dest']
        channel = item['chan']
        session_indicator = item.get('sess', '')

        # Chunk the text
        import config
        max_chunk_length = getattr(config, 'MESSAGE_CHUNK_SIZE', 160)
        chunks = self.handler._split_message(text, max_length=max_chunk_length)
        total_chunks = len(chunks)

        for i, chunk in enumerate(chunks):
            self.last_heartbeat = time.time()
            if i > 0:
                chunk_delay = getattr(config, 'CHUNK_DELAY', 5)
                time.sleep(chunk_delay)

            # Prepend session indicator to first chunk if present
            if i == 0 and session_indicator:
                chunk = f"{session_indicator} {chunk}"

            # Append chunk counter if multi-part
            if total_chunks > 1:
                chunk = f"{chunk} [{i+1}/{total_chunks}]"

            success = self.handler._send_chunk_sync(chunk, destination, channel)
            self.last_send_time = time.time()
            if not success:
                logger.warning(f"[MeshCore] Failed sending chunk {i+1}/{total_chunks} to {destination}")
                break


class MeshCoreHandler:
    """
    Handles MeshCore interface connections and message transmission.
    
    Supports:
    - TCP mode: Connects to MeshMonitor Virtual Node (default port 5000) or WiFi companion
    - Serial mode: Connects directly to hardware (e.g. /dev/ttyUSB1) in standalone mode
    - Thread-safe asyncio loop running in background
    - Message chunking, queueing, and rate limiting
    - Packet normalization into the format expected by AIResponder.on_receive
    """

    def __init__(
        self,
        interface_type: str = 'tcp',
        serial_port: Optional[str] = None,
        tcp_host: str = '127.0.0.1',
        tcp_port: int = 5000,
        baudrate: int = 115200,
        ack_timeout: int = 30
    ):
        self.interface_type = interface_type.lower()
        self.serial_port = serial_port or '/dev/ttyUSB1'
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.baudrate = baudrate
        self.ack_timeout = ack_timeout

        self.meshcore = None
        self.running = False
        self.connection_healthy = False
        self.last_activity = 0

        # Background thread and asyncio loop
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()

        # Callbacks and caching
        self.on_receive_callback = None
        self.telemetry_cache = {}
        self.telemetry_timestamps = {}
        self.interesting_nodes = set()

        # Outgoing message queue
        self._message_queue = MeshCoreMessageQueue(self)

    def track_node(self, node_id: str):
        """Mark a node as interesting for logging."""
        if node_id:
            self.interesting_nodes.add(str(node_id).lower())

    def connect(self, on_receive_callback=None) -> bool:
        """
        Start the background event loop and establish connection to MeshCore.
        
        Args:
            on_receive_callback: Callable(packet_dict, interface) for incoming packets.
        """
        if self.is_connected():
            return True

        self.disconnect()
        self.on_receive_callback = on_receive_callback
        self._ready_event.clear()
        self._stop_event.clear()

        # Start dedicated asyncio background thread
        self._thread = threading.Thread(
            target=self._run_async_loop,
            daemon=True,
            name="MeshCore_Async_Thread"
        )
        self._thread.start()

        # Wait for connection handshake
        success = self._ready_event.wait(timeout=15.0)
        if success and self.connection_healthy:
            self.running = True
            if hasattr(self, '_message_queue') and self._message_queue:
                self._message_queue.start()
            logger.info("✅ MeshCore connection established and active.")
            return True
        else:
            logger.warning("⚠️ MeshCore connection failed to initialize in time.")
            return False

    def _run_async_loop(self):
        """Dedicated asyncio event loop thread for MeshCore operations."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._async_connect_and_subscribe())
            # Run loop forever until stopped
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"[MeshCore] Event loop encountered error: {e}", exc_info=True)
        finally:
            try:
                # Cancel all remaining tasks
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self._loop.close()
            except Exception:
                pass
            self.connection_healthy = False

    async def _async_connect_and_subscribe(self):
        """Asynchronously connect to MeshCore and set up listeners."""
        if not HAS_MESHCORE:
            logger.warning("[MeshCore] meshcore library unavailable; mock connection set to ready.")
            self.connection_healthy = True
            self.running = True
            self._ready_event.set()
            return

        try:
            if self.interface_type == 'serial':
                logger.info(f"[MeshCore] Connecting via Serial: {self.serial_port} @ {self.baudrate} baud...")
                self.meshcore = await MeshCore.create_serial(
                    port=self.serial_port,
                    baudrate=self.baudrate,
                    auto_reconnect=True,
                    max_reconnect_attempts=5
                )
            else:  # 'tcp'
                logger.info(f"[MeshCore] Connecting via TCP (Virtual Node): {self.tcp_host}:{self.tcp_port}...")
                self.meshcore = await MeshCore.create_tcp(
                    host=self.tcp_host,
                    port=self.tcp_port,
                    auto_reconnect=True,
                    max_reconnect_attempts=5
                )

            # Subscribe to messaging and connection events
            self.meshcore.subscribe(EventType.CONTACT_MSG_RECV, self._handle_contact_msg_event)
            self.meshcore.subscribe(EventType.CHANNEL_MSG_RECV, self._handle_channel_msg_event)
            self.meshcore.subscribe(EventType.CONNECTED, self._handle_connected_event)
            self.meshcore.subscribe(EventType.DISCONNECTED, self._handle_disconnected_event)
            self.meshcore.subscribe(EventType.SELF_INFO, self._handle_self_info_event)

            # Start background message fetching (pulls messages when MESSAGES_WAITING fires)
            try:
                await self.meshcore.start_auto_message_fetching()
                logger.info("✅ MeshCore auto message fetching started.")
            except Exception as e:
                logger.debug(f"[MeshCore] Auto message fetching subscription note: {e}")

            # Sync contacts list
            try:
                await self.meshcore.ensure_contacts()
                logger.info(f"✅ MeshCore contacts synchronized ({len(self.meshcore.contacts)} contacts).")
            except Exception as e:
                logger.warning(f"[MeshCore] Could not sync contacts: {e}")

            self.connection_healthy = True
            self.last_activity = time.time()
            self._ready_event.set()

        except Exception as e:
            logger.error(f"❌ Failed to connect to MeshCore: {e}")
            self.connection_healthy = False
            self._ready_event.set()

    # ==================== Event Callbacks ====================

    async def _handle_connected_event(self, event):
        """Fires when MeshCore connection is confirmed."""
        logger.info("🟢 [MeshCore] Connection event: CONNECTED")
        self.connection_healthy = True
        self.last_activity = time.time()

    async def _handle_disconnected_event(self, event):
        """Fires when MeshCore connection is dropped."""
        logger.warning("🔴 [MeshCore] Connection event: DISCONNECTED")
        self.connection_healthy = False

    async def _handle_self_info_event(self, event):
        """Fires when SELF_INFO is received."""
        self.last_activity = time.time()
        logger.debug(f"[MeshCore] Self info updated: {getattr(event, 'payload', {})}")

    async def _handle_contact_msg_event(self, event):
        """Fires when a private DM from a contact is received."""
        self.last_activity = time.time()
        payload = getattr(event, 'payload', {})
        if not isinstance(payload, dict):
            return

        text = payload.get('text', '').strip()
        pubkey_prefix = payload.get('pubkey_prefix', '')
        if not text:
            return

        # Attempt to map prefix to a known contact name
        sender_id = pubkey_prefix
        if pubkey_prefix and self.meshcore:
            contact = self.meshcore.get_contact_by_key_prefix(pubkey_prefix)
            if contact:
                name = contact.get('adv_name') or contact.get('name')
                if name:
                    sender_id = name

        # Format sender as !<prefix> if it's raw hex, for parity with Meshtastic ID conventions
        display_from = sender_id if not pubkey_prefix or sender_id != pubkey_prefix else f"!{pubkey_prefix}"

        # Determine my local node ID
        my_id = 'bot'
        if self.meshcore and hasattr(self.meshcore, 'self_info'):
            my_info = self.meshcore.self_info or {}
            my_id = my_info.get('name') or my_info.get('public_key', 'bot')[:12]

        normalized_packet = {
            'fromId': display_from,
            'toId': my_id,
            'channel': 0,
            'is_dm': True,
            'network': 'meshcore',
            'raw_pubkey_prefix': pubkey_prefix,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': text
            }
        }

        logger.info(f"📨 [MeshCore DM] from {display_from} ({pubkey_prefix}): {text[:50]}...")
        if self.on_receive_callback:
            try:
                self.on_receive_callback(normalized_packet, self)
            except Exception as e:
                logger.error(f"[MeshCore] Error in on_receive callback: {e}", exc_info=True)

    async def _handle_channel_msg_event(self, event):
        """Fires when a group channel / broadcast message is received."""
        self.last_activity = time.time()
        payload = getattr(event, 'payload', {})
        if not isinstance(payload, dict):
            return

        text = payload.get('text', '').strip()
        channel_idx = payload.get('channel_idx', 0)
        if not text:
            return

        normalized_packet = {
            'fromId': 'channel_sender',
            'toId': '^all',
            'channel': channel_idx,
            'is_dm': False,
            'network': 'meshcore',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': text
            }
        }

        logger.info(f"📨 [MeshCore Ch:{channel_idx}] {text[:50]}...")
        if self.on_receive_callback:
            try:
                self.on_receive_callback(normalized_packet, self)
            except Exception as e:
                logger.error(f"[MeshCore] Error in on_receive callback: {e}", exc_info=True)

    # ==================== Message Sending ====================

    def send_message(self, text: str, destination_id: str, channel_index: int = 0, session_indicator: str = "") -> bool:
        """
        Queue a message to be sent via MeshCore. Non-blocking.
        
        Args:
            text: Message text
            destination_id: Contact name/prefix or '^all' for channel broadcast
            channel_index: Channel index (default: 0)
            session_indicator: Optional session prefix
        """
        if not self.is_connected():
            logger.error("Cannot send message: Not connected to MeshCore")
            return False

        if not hasattr(self, '_message_queue'):
            self._message_queue = MeshCoreMessageQueue(self)
        elif not self._message_queue.thread or not self._message_queue.thread.is_alive():
            self._message_queue.start()

        return self._message_queue.enqueue(text, destination_id, channel_index, session_indicator)

    def _send_chunk_sync(self, text: str, destination_id: str, channel_index: int) -> bool:
        """Synchronously dispatch chunk sending coroutine onto the background event loop."""
        if not self._loop or not self._loop.is_running():
            logger.error("[MeshCore] Cannot send chunk: event loop is not running")
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._async_send_chunk(text, destination_id, channel_index),
            self._loop
        )
        try:
            return future.result(timeout=self.ack_timeout)
        except Exception as e:
            logger.error(f"[MeshCore] Timed out or failed sending chunk: {e}")
            return False

    async def _async_send_chunk(self, text: str, destination_id: str, channel_index: int) -> bool:
        """Asynchronously send a single chunk to the MeshCore radio."""
        if not self.meshcore:
            logger.warning("[MeshCore] Mock send: meshcore instance not active.")
            return True

        try:
            if destination_id == '^all' or destination_id == 'broadcast':
                # Group channel broadcast
                logger.info(f"📤 [MeshCore] Broadcasting to channel {channel_index}: {text[:50]}...")
                result = await self.meshcore.commands.send_chan_msg(channel_index, text)
                if result and result.type == EventType.ERROR:
                    logger.warning(f"[MeshCore] Error sending chan msg: {result.payload}")
                    return False
                return True
            else:
                # Direct message to contact
                target = destination_id
                # Strip leading '!' if present
                if isinstance(target, str) and target.startswith('!'):
                    target = target[1:]

                # Check if target is a contact name or partial key
                contact = None
                if hasattr(self.meshcore, 'contacts'):
                    # Try finding by name first
                    contact = self.find_contact_by_name(target)
                    if not contact and hasattr(self.meshcore, 'get_contact_by_key_prefix'):
                        contact = self.meshcore.get_contact_by_key_prefix(target)

                dest_param = contact if contact else target
                logger.info(f"📤 [MeshCore] Sending DM to {destination_id}: {text[:50]}...")
                result = await self.meshcore.commands.send_msg(dest_param, text)
                if result and result.type == EventType.ERROR:
                    logger.warning(f"[MeshCore] Error sending DM: {result.payload}")
                    return False
                return True

        except Exception as e:
            logger.error(f"[MeshCore] Exception during message transmission: {e}")
            return False

    def _split_message(self, text: str, max_length: int = 160) -> List[str]:
        """Split a long message into readable chunks at newline/sentence/word boundaries."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        remaining_text = text

        while remaining_text:
            if len(remaining_text) <= max_length:
                chunks.append(remaining_text)
                break

            chunk = remaining_text[:max_length]
            split_point = -1

            # 1. Newline (preserve formatting)
            last_newline = chunk.rfind('\n')
            if last_newline > max_length * 0.3:
                split_point = last_newline + 1

            # 2. Sentence boundary
            if split_point == -1:
                last_sentence_end = max(chunk.rfind('. '), chunk.rfind('! '), chunk.rfind('? '))
                if last_sentence_end > max_length * 0.5:
                    split_point = last_sentence_end + 2

            # 3. Word boundary
            if split_point == -1:
                last_space = chunk.rfind(' ')
                split_point = last_space if last_space > 0 else max_length

            chunks.append(remaining_text[:split_point].strip())
            remaining_text = remaining_text[split_point:].strip()

        return chunks

    # ==================== State & Contact Inspection ====================

    def get_node_info(self) -> Dict[str, Any]:
        """Get local bot node information in standard dictionary format."""
        if not self.meshcore:
            return {'user': {'id': '!meshcore', 'longName': 'MeshCore Bot', 'shortName': 'MCB'}}

        self_info = getattr(self.meshcore, 'self_info', {}) or {}
        pubkey = self_info.get('public_key', 'unknown')
        name = self_info.get('name', 'MeshCore Node')
        short_name = name[:4].upper() if name else pubkey[:4].upper()

        return {
            'user': {
                'id': f"!{pubkey[:12]}" if pubkey != 'unknown' else '!unknown',
                'longName': name,
                'shortName': short_name,
                'macaddr': pubkey[:12]
            },
            'position': {
                'latitude': self_info.get('adv_lat'),
                'longitude': self_info.get('adv_lon')
            },
            'radio': {
                'freq': self_info.get('radio_freq'),
                'bw': self_info.get('radio_bw'),
                'sf': self_info.get('radio_sf'),
                'cr': self_info.get('radio_cr'),
                'tx_power': self_info.get('tx_power')
            }
        }

    def find_contact_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a contact dict by matching long/short/adv_name (case-insensitive) or prefix."""
        if not self.meshcore or not hasattr(self.meshcore, 'contacts'):
            return None

        name_lower = name.lower().strip()
        for pubkey, contact in self.meshcore.contacts.items():
            adv_name = (contact.get('adv_name') or contact.get('name', '')).lower()
            if adv_name == name_lower:
                return contact
            # Also check prefix match
            if pubkey.lower().startswith(name_lower):
                return contact
        return None

    def find_node_by_name(self, name: str) -> Optional[str]:
        """
        Find a contact node ID by matching name (case-insensitive).

        Returns:
            str or None: Node ID formatted as '!<pubkey[:12]>' if found, otherwise None.
        """
        contact = self.find_contact_by_name(name)
        if not contact:
            return None
        pubkey = contact.get('public_key', '')
        if pubkey:
            return f"!{pubkey[:12]}"
        return None

    def _get_node_by_id(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve node info dict by ID or prefix."""
        if not node_id:
            return None
        clean_id = node_id.lstrip('!').lower()
        contact = self.find_contact_by_name(clean_id)
        if not contact and self.meshcore and hasattr(self.meshcore, 'get_contact_by_key_prefix'):
            contact = self.meshcore.get_contact_by_key_prefix(clean_id)
        if not contact:
            return None
        name = contact.get('adv_name') or contact.get('name', 'Unknown')
        pubkey = contact.get('public_key', clean_id)
        short_name = name[:4].upper() if name else pubkey[:4].upper()
        return {
            'user': {
                'id': f"!{pubkey[:12]}",
                'longName': name,
                'shortName': short_name
            }
        }

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Get a list of all known contacts formatted like Meshtastic nodes."""
        if not self.meshcore or not hasattr(self.meshcore, 'contacts'):
            return []

        nodes = []
        for pubkey, contact in self.meshcore.contacts.items():
            name = contact.get('adv_name') or contact.get('name', 'Unknown')
            short_name = name[:4].upper()
            nodes.append({
                'id': f"!{pubkey[:12]}",
                'full_id': pubkey,
                'longName': name,
                'shortName': short_name,
                'lat': contact.get('adv_lat'),
                'lon': contact.get('adv_lon'),
                'hops': contact.get('out_path_len', 0),
                'last_advert': contact.get('last_advert', 0)
            })
        return nodes

    def get_node_list_summary(self, max_nodes: int = 8) -> str:
        """Get a concise summary of active contacts for AI prompt context."""
        nodes = self.get_all_nodes()
        if not nodes:
            return "No contacts detected on MeshCore network."

        lines = [f"MeshCore Contacts ({len(nodes)} total):"]
        for node in nodes[:max_nodes]:
            name = node.get('longName', 'Unknown')
            nid = node.get('id', '')
            hops = node.get('hops', 0)
            lines.append(f"- {name} ({nid}): {hops} hops")

        if len(nodes) > max_nodes:
            lines.append(f"... and {len(nodes) - max_nodes} more contacts.")

        return "\n".join(lines)

    def get_node_metadata(self, node_id: str) -> str:
        """Get detailed metadata for a specific contact."""
        if not node_id:
            return ""

        clean_id = node_id[1:] if node_id.startswith('!') else node_id
        contact = self.find_contact_by_name(clean_id)
        if not contact and self.meshcore and hasattr(self.meshcore, 'get_contact_by_key_prefix'):
            contact = self.meshcore.get_contact_by_key_prefix(clean_id)

        if not contact:
            return f"Node {node_id} not found in MeshCore contacts."

        name = contact.get('adv_name') or contact.get('name', 'Unknown')
        pubkey = contact.get('public_key', clean_id)
        hops = contact.get('out_path_len', 'unknown')
        lat = contact.get('adv_lat')
        lon = contact.get('adv_lon')

        pos_str = f"Lat: {lat}, Lon: {lon}" if lat is not None and lon is not None else "Position unknown"
        return f"[Node: {name} ({pubkey[:12]}) | Hops: {hops} | {pos_str}]"

    def get_channels(self) -> List[Dict[str, Any]]:
        """Get list of active channels."""
        # MeshCore companion channels typically range 0-7 with primary at 0
        return [{'index': 0, 'name': 'Primary'}]

    def send_probe(self) -> bool:
        """Send an active probe/advert to verify radio link."""
        if not self.is_connected() or not self._loop:
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.meshcore.commands.send_advert(),
                self._loop
            )
            return future.result(timeout=5) is not None
        except Exception as e:
            logger.debug(f"[MeshCore] Failed sending probe: {e}")
            return False

    def request_telemetry(self, destination_id: str, telemetry_type: str = 'device') -> bool:
        """Request telemetry from contact (MeshCore telemetry request)."""
        if not self.is_connected() or not self._loop:
            return False
        try:
            target = destination_id[1:] if destination_id.startswith('!') else destination_id
            contact = self.find_contact_by_name(target)
            dest = contact if contact else target
            future = asyncio.run_coroutine_threadsafe(
                self.meshcore.commands.req_telemetry(dest),
                self._loop
            )
            return future.result(timeout=10) is not None
        except Exception as e:
            logger.debug(f"[MeshCore] Failed requesting telemetry: {e}")
            return False

    def is_connected(self) -> bool:
        """Check if currently connected to MeshCore."""
        if not self.running or not self.connection_healthy:
            return False
        if not HAS_MESHCORE:
            return True
        if not self.meshcore:
            return False
        if hasattr(self.meshcore, 'is_connected'):
            val = self.meshcore.is_connected
            return val() if callable(val) else bool(val)
        return True

    def disconnect(self):
        """Close connection and clean up asyncio loop thread."""
        self.running = False
        self.connection_healthy = False
        if hasattr(self, '_message_queue') and self._message_queue:
            self._message_queue.stop()

        if self._loop and self._loop.is_running():
            try:
                if self.meshcore and hasattr(self.meshcore, 'disconnect'):
                    future = asyncio.run_coroutine_threadsafe(self.meshcore.disconnect(), self._loop)
                    try:
                        future.result(timeout=3)
                    except Exception:
                        pass
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception as e:
                logger.debug(f"[MeshCore] Error stopping loop: {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        self.meshcore = None
        self._loop = None
        self._thread = None
        logger.info("[MeshCore] Handler disconnected.")
