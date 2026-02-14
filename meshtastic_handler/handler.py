# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""
Meshtastic interface handler.

This module manages the Meshtastic connection and message handling,
including sending messages, managing connections, and processing incoming packets.
"""

import time
import logging
import sys
import threading
from pubsub import pub
from meshtastic.serial_interface import SerialInterface
from meshtastic.tcp_interface import TCPInterface
from meshtastic import mesh_pb2, portnums_pb2
from meshtastic.protobuf import telemetry_pb2
from google.protobuf.message import DecodeError

logger = logging.getLogger(__name__)


class SafeTCPInterface(TCPInterface):
    """
    TCPInterface helper that suppresses protobuf DecodeErrors and manually handles packets.
    """
    def _handleFromRadio(self, fromRadio):
        """
        Custom packet handler for safe decoding and debug logging.
        
        Detailed features:
        - Logs raw packet contents for debugging.
        - Detects "Implicit ACKs" (ROUTING_APP packets) to support reliable messaging.
        - Filters out false positives:
          - Errors (error_reason != 0)
          - Self-echoes (sender == myNodeNum)
        - Accepts "Ghost ACKs" (sender is None/0) as valid confirmations.
        """
        # 0. DEBUG: Inspect what we are receiving
        try:
            if isinstance(fromRadio, bytes):
                logger.debug(f"RAW Bytes received: {len(fromRadio)} bytes")
                # Try to decode just for logging purposes
                try:
                    debug_decoded = mesh_pb2.FromRadio()
                    debug_decoded.ParseFromString(fromRadio)
                    logger.debug(f"Decoded Fields: {debug_decoded.ListFields()}")
                    if debug_decoded.HasField("packet"):
                        logger.debug(f"Content Is: MeshPacket (to: {debug_decoded.packet.to})")
                        
                        # Manual ACK detection for events wrapped in MeshPackets
                        try:
                            # 5 is ROUTING_APP
                            pnum = debug_decoded.packet.decoded.portnum
                            # logger.debug(f"Inspect PortNum: {pnum} (type: {type(pnum)})")
                            if pnum == 5:
                                routing = debug_decoded.packet.decoded
                                rid = routing.request_id
                                error = getattr(routing, 'error_reason', 0)
                                sender = getattr(debug_decoded.packet, 'from', None)
                                
                                # Get own ID
                                my_id = getattr(self, 'myNodeNum', None)
                                
                                if rid:
                                    # Ignore echoes (only if sender matches my_id explicitly)
                                    # We MUST accept sender=None because legitimate ACKs are arriving without source ID
                                    if my_id and sender and sender == my_id:
                                         logger.debug(f"⚡ Ignored implicit ACK for ID {rid} (Source: {sender} - is self)")
                                    elif error == 0:
                                        logger.debug(f"⚡ Found implicit ACK/Routing in MeshPacket for ID {rid} from {sender} - Forcing event")
                                        pub.sendMessage("meshtastic.ack", packetId=rid, interface=self)
                                    else:
                                        logger.warning(f"⚠️ Ignored implicit ACK for ID {rid} because error_reason={error}")
                        except Exception as e:
                            logger.debug(f"Failed to check for implicit ACK: {e}")

                    elif debug_decoded.HasField("mqttClientProxyMessage"):
                        logger.debug(f"Content Is: MQTT Proxy Message (topic: {debug_decoded.mqttClientProxyMessage.topic})")
                    elif debug_decoded.HasField("routing"):
                        logger.debug(f"Content Is: Routing/ACK (error_reason: {debug_decoded.routing.error_reason}, request_id: {debug_decoded.routing.request_id})")
                        # FORCE emit the ACK event because standard lib seems to consume it silently
                        # This ensures our event-driven waiter gets notified
                        rid = debug_decoded.routing.request_id
                        logger.debug(f"⚡ forcing manual ACK event for ID {rid}")
                        pub.sendMessage("meshtastic.ack", packetId=rid, interface=self)

                except:
                    logger.debug("Failed to decode raw bytes for debug log")
        except Exception as e:
            logger.error(f"Error in debug logger: {e}")

        # 1. Try standard lib processing first
        try:
            super()._handleFromRadio(fromRadio)
            return  # Success, handled by standard lib
        except DecodeError:
            # 2. If it fails, log and try manual salvage
            logger.debug("Protobuf Decode Error in standard lib. Attempting manual salvage...")
        except Exception as e:
            logger.warning(f"Unexpected stream error: {e}")
            return

        # 3. Manual Salvage
        try:
            decoded = None
            if isinstance(fromRadio, bytes):
                decoded = mesh_pb2.FromRadio()
                decoded.ParseFromString(fromRadio)
            elif hasattr(fromRadio, 'packet'):  # Already an object
                decoded = fromRadio

            if decoded and decoded.HasField("packet"):
                # Manually trigger packet handling since super() failed
                # Note: We can't easily call _handlePacket because it might simpler to just publish
                logger.debug("✅ Packet salvaged manually - Publishing to meshtastic.receive")
                pub.sendMessage("meshtastic.receive", packet=decoded.packet, interface=self)
            
            # Use 'getattr' to safely check for 'routing' field, 
            # as it might not be available in all firmware/protobuf versions
            elif decoded and decoded.HasField("routing"):
                 # This block might be redundant now if we force it above, but keeps salvage logic intact
                 logger.debug("✅ ACK/Routing salvaged manually - Publishing to meshtastic.ack")
                 # We need to extract the original packet ID this ACK is for.
                 # Usually routing.request_id matches the sent packet packet.id?
                 # Actually, meshtastic.ack expects packetId kwarg.
                 # Let's try to assume request_id is the one.
                 rid = decoded.routing.request_id
                 pub.sendMessage("meshtastic.ack", packetId=rid, interface=self)

        except Exception as e:
            logger.debug(f"Manual salvage failed: {e}")


class MeshtasticHandler:
    """
    Handles Meshtastic interface connections and message transmission.
    
    Features:
    - Automatic connection management (TCP or Serial)
    - Message chunking for long responses
    - Rate limiting to prevent flooding
    - ACK waiting for reliable delivery
    """
    
    def __init__(self, interface_type='tcp', serial_port=None, tcp_host=None, tcp_port=4403, ack_timeout=60):
        """
        Initialize Meshtastic handler.
        
        Args:
            interface_type: 'tcp' or 'serial'
            serial_port: Serial port path (e.g., '/dev/ttyUSB0')
            tcp_host: TCP hostname or IP (e.g., 'meshtastic.local')
            tcp_port: TCP port (default: 4403)
            ack_timeout: Seconds to wait for message acknowledgment
        """
        self.interface_type = interface_type
        self.serial_port = serial_port
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.ack_timeout = ack_timeout
        self.interface = None
        self.running = False
        
        self.env_telemetry_cache = {}  # {node_id: {temperature, humidity, ...}}
        self.interesting_nodes = set() # Nodes to log telemetry for (e.g. active conversations)
        self.last_activity = 0

    def track_node(self, node_id):
        """Mark a node as interesting for logging."""
        if node_id:
            self.interesting_nodes.add(node_id)

    def connect(self, on_receive_callback=None):
        """
        Establish connection to Meshtastic device.
        
        Args:
            on_receive_callback: Function to call when messages are received
                                Signature: callback(packet, interface)
        
        Returns:
            bool: True if connection successful
        """
        # If already connected, do nothing
        if self.is_connected():
            return True
            
        # Ensure clean state
        self.disconnect()
        
        # Import config here to avoid circular dependencies if any
        import config
        max_retries = config.CONNECTION_MAX_RETRIES
        retry_delay = config.CONNECTION_RETRY_INTERVAL
        
        for attempt in range(max_retries):
            try:
                if self.interface_type == 'serial':
                    logger.info(f"Connecting to Meshtastic via Serial: {self.serial_port} (Attempt {attempt+1}/{max_retries})")
                    self.interface = SerialInterface(devPath=self.serial_port)
                else:  # TCP
                    logger.info(f"Connecting to Meshtastic via TCP: {self.tcp_host}:{self.tcp_port} (Attempt {attempt+1}/{max_retries})")
                    # Use SafeTCPInterface to handle potential stream errors from proxy
                    self.interface = SafeTCPInterface(hostname=self.tcp_host, portNumber=self.tcp_port)
                
                # Register receive callback if provided
                if on_receive_callback:
                    # Use pubsub instead of direct assignment for better compatibility
                    # Unsubscribe first to ensure no duplicates if reconnecting
                    try:
                        pub.unsubscribe(on_receive_callback, "meshtastic.receive")
                    except:
                        pass
                    pub.subscribe(on_receive_callback, "meshtastic.receive")
                    logger.info("✅ Subscribed to meshtastic.receive")
                
                # Subscribe to telemetry specifically to populate our internal cache
                try:
                    pub.unsubscribe(self._on_telemetry, "meshtastic.receive.telemetry")
                except:
                    pass
                pub.subscribe(self._on_telemetry, "meshtastic.receive.telemetry")
                logger.debug("✅ Subscribed to meshtastic.receive.telemetry for caching")
                
                # Subscribe to ACKs for reliable sending
                try:
                    pub.unsubscribe(self._on_ack, "meshtastic.ack")
                except:
                    pass
                pub.subscribe(self._on_ack, "meshtastic.ack")
                logger.info("✅ Subscribed to meshtastic.ack")

                # Subscribe to general packets for activity tracking
                try:
                    pub.unsubscribe(self._on_packet_activity, "meshtastic.receive")
                except:
                    pass
                pub.subscribe(self._on_packet_activity, "meshtastic.receive")
                
                # Subscribe to connection lost
                try:
                    pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")
                except:
                    pass
                pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")

                self.running = True
                self.last_activity = time.time()
                logger.info("✅ Connected to Meshtastic")
                return True
                
            except Exception as e:
                logger.error(f"❌ Connection failed (Attempt {attempt+1}/{max_retries}): {e}")
                
                # Clean up failed interface if partially created
                if self.interface:
                    try:
                        self.interface.close()
                    except: pass
                    self.interface = None
                    
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    self.running = False
                    return False
    
    def disconnect(self):
        """Close the Meshtastic connection."""
        if self.interface:
            try:
                self.interface.close()
                logger.info("Disconnected from Meshtastic")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
        
        # We don't unsubscribe here because we don't have the callback reference easily available
        # But connect() handles cleanup of previous subscriptions
        
        self.running = False
        self.interface = None
    
    def is_connected(self):
        """
        Check if currently connected to Meshtastic.
        
        Returns:
            bool: True if connected and interface is healthy
        """
        if not self.interface or not self.running:
            return False
            
        # Check if serial/tcp interface is actually alive
        # For TCPInterface/SerialInterface, they usually have a reader thread
        if hasattr(self.interface, '_reader') and self.interface._reader:
            if not self.interface._reader.is_alive():
                logger.warning("⚠️ Meshtastic interface reader thread is dead")
                return False
                
        return True
    def _on_ack(self, packetId, interface):
        """Handle incoming ACK events."""
        if getattr(self, 'current_ack_event', None) and getattr(self, 'expected_ack_id', None) == packetId:
            logger.debug(f"⚡ Event-driven ACK received for ID {packetId}")
            self.current_ack_event.set()

    def _on_telemetry(self, packet, interface):
        """Handle incoming telemetry packets specifically to populate the cache."""
        try:
            from_id_raw = packet.get('fromId')
            # Handle int/str mix
            from_id = from_id_raw
            if isinstance(from_id_raw, int):
                from_id = f"!{from_id_raw:08x}"
                
            decoded = packet.get('decoded', {})
            
            # telemetry data is usually inside 'telemetry' key in decoded dictionary
            telemetry = decoded.get('telemetry', {})
            env_data = telemetry.get('environmentMetrics')
            
            if env_data and from_id:
                self.env_telemetry_cache[from_id] = env_data
                
                # Only log INFO if we care about this node, otherwise DEBUG
                if from_id in self.interesting_nodes:
                    logger.info(f"📊 Cached telemetry for {from_id}: {env_data}")
                else:
                    logger.debug(f"📊 Cached telemetry for {from_id}: {env_data}")
        except Exception as e:
            logger.warning(f"Error caching telemetry: {e}")

    def _on_packet_activity(self, packet, interface):
        """Update last activity on any received packet."""
        self.last_activity = time.time()

    def _on_connection_lost(self, interface):
        """Handle connection lost event."""
        logger.warning("Meshtastic connection reported LOST!")
        # We don't set running=False here to allow watchdog in ai_responder to handle it
        # but we can update a state or log it.

    def send_probe(self):
        """Send an active probe to the radio to verify connection."""
        if not self.interface or not self.running:
            return False
        
        try:
            logger.info("📡 Sending active radio probe (position query)...")
            self.interface.sendPosition()
            return True
        except Exception as e:
            logger.warning(f"Failed to send probe: {e}")
            return False

    def request_telemetry(self, destination_id):
        """
        Request telemetry from a specific node.
        
        This sends an empty telemetry packet with wantResponse=True
        to trigger an asynchronous update from the remote node.
        """
        if not self.interface or not self.running:
            logger.warning(f"Cannot request telemetry: Not connected")
            return False
            
        try:
            # Create an empty environmental telemetry packet
            # MeshMonitor uses an empty EnvironmentMetrics packet to request a refresh
            env_metrics = telemetry_pb2.EnvironmentMetrics()
            telemetry = telemetry_pb2.Telemetry()
            telemetry.environment_metrics.CopyFrom(env_metrics)
            
            payload = telemetry.SerializeToString()
            
            # destination_id can be node ID string like "!12345678" or integer
            dest = destination_id
            if isinstance(destination_id, str) and destination_id.startswith('!'):
                dest = int(destination_id[1:], 16)
            
            logger.info(f"📊 Requesting environmental telemetry from {destination_id} ({dest})")
            self.interface.sendData(
                payload,
                destinationId=dest,
                portNum=portnums_pb2.PortNum.TELEMETRY_APP,
                wantResponse=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to request telemetry from {destination_id}: {e}")
            return False

    def send_message(self, text, destination_id, channel_index=0, session_indicator=""):
        """
        Queue a message to be sent via Meshtastic.
        
        This method is non-blocking. It adds the message to a background queue
        and returns immediately.
        
        Args:
            text: Message text to send
            destination_id: Target node ID (e.g., '!abc123') or '^all' for broadcast
            channel_index: Meshtastic channel index (default: 0)
            session_indicator: Optional prefix for session messages
        
        Returns:
            bool: True if queued successfully
        """
        if not self.interface:
            logger.error("Cannot send message: Not connected to Meshtastic")
            return False
            
        # Initialize queue if needed
        if not hasattr(self, '_message_queue'):
            self._message_queue = MessageQueue(self)
            
        self._message_queue.enqueue(text, destination_id, channel_index, session_indicator)
        return True

    def _split_message(self, text, max_length=200):
        """
        Split a long message into chunks.
        
        Attempts to split at sentence boundaries when possible to maintain
        readability.
        
        Args:
            text: Text to split
            max_length: Maximum characters per chunk
        """
        if len(text) <= max_length:
            return [text]
        
        chunks = []
        remaining_text = text
        
        while remaining_text:
            if len(remaining_text) <= max_length:
                chunks.append(remaining_text)
                break
            
            # Try to split at sentence boundary
            chunk = remaining_text[:max_length]
            
            # Look for sentence endings (., !, ?)
            last_sentence_end = max(
                chunk.rfind('. '),
                chunk.rfind('! '),
                chunk.rfind('? ')
            )
            
            if last_sentence_end > max_length * 0.5:  # Only split if we're past halfway
                split_point = last_sentence_end + 2  # Include the punctuation and space
            else:
                # Fall back to word boundary
                last_space = chunk.rfind(' ')
                split_point = last_space if last_space > 0 else max_length
            
            chunks.append(remaining_text[:split_point].strip())
            remaining_text = remaining_text[split_point:].strip()
        
        return chunks
    
    def _get_node_by_id(self, node_id):
        """
        Helper to find a node in the interface.nodes dictionary by various ID formats.
        
        Args:
            node_id: Node ID (int, hex string '!1234abcd', or decimal string)
            
        Returns:
            dict or None: Node information if found
        """
        if not self.interface or not self.interface.nodes:
            return None

        # 1. Try direct lookup (works if type matches exactly)
        info = self.interface.nodes.get(node_id)
        if info:
            return info

        # 2. Normalize to Int and Hex
        node_int = None
        node_hex = None

        if isinstance(node_id, str):
            if node_id.startswith('!'):
                try:
                    node_int = int(node_id[1:], 16)
                    node_hex = node_id
                except: pass
            elif node_id.isdigit():
                node_int = int(node_id)
                node_hex = f"!{node_int:08x}"
        elif isinstance(node_id, int):
            node_int = node_id
            node_hex = f"!{node_int:08x}"

        # 3. Try lookup by normalized forms
        if node_int is not None:
            info = self.interface.nodes.get(node_int)
            if info: return info
            
        if node_hex is not None:
            info = self.interface.nodes.get(node_hex)
            if info: return info

        return None

    def find_node_by_name(self, name):
        """
        Find a node by matching its long name or short name.
        
        Args:
            name: Name to search for (case-insensitive)
            
        Returns:
            str or None: Node ID if found, otherwise None
        """
        if not self.interface or not self.interface.nodes:
            return None
            
        name_lower = name.lower().strip()
        
        for n_id, node in self.interface.nodes.items():
            user = node.get('user', {})
            long_name = user.get('longName', '').lower()
            short_name = user.get('shortName', '').lower()
            
            if name_lower == long_name or name_lower == short_name:
                return user.get('id')
                
        return None

    def get_all_nodes(self):
        """
        Get a list of all known nodes with their names.
        
        Returns:
            list: List of dicts with node info
        """
        if not self.interface or not self.interface.nodes:
            return []
            
        nodes = []
        for n_id, node in self.interface.nodes.items():
            user = node.get('user', {})
            nodes.append({
                'id': user.get('id'),
                'longName': user.get('longName'),
                'shortName': user.get('shortName')
            })
        return nodes

    def get_node_list_summary(self):
        """
        Get a concise summary of the node list for AI context.
        
        Returns:
            str: Formatted string of known nodes
        """
        nodes = self.get_all_nodes()
        if not nodes:
            return "No neighbors detected on mesh."
            
        lines = ["Neighbor nodes on mesh:"]
        for n in nodes:
            if not n['id']: continue
            name = n['longName'] or n['shortName'] or "Unknown"
            short = f" ({n['shortName']})" if n['shortName'] and n['shortName'] != name else ""
            lines.append(f"- {n['id']}: {name}{short}")
            
        return "\n".join(lines)
    def get_node_metadata(self, node_id):
        """
        Get metadata (location, battery, environment) for a node.
        
        Args:
            node_id: Node ID (e.g., '!1234abcd')
            
        Returns:
            str: Formatted metadata string or None
        """
        if not self.interface:
            return None
            
        try:
            node_info = self._get_node_by_id(node_id)
            if not node_info:
                return None
        
            # DEBUG: Log raw node structure to diagnose missing environmentMetrics
            logger.debug(f"Raw node_info for {node_id}: deviceMetrics={node_info.get('deviceMetrics')}, environmentMetrics={node_info.get('environmentMetrics')}")
                
            metadata_parts = []
            
            # 0. Identification
            user = node_info.get('user', {})
            long_name = user.get('longName')
            short_name = user.get('shortName')
            if long_name:
                metadata_parts.append(f"Name: {long_name}")
            if short_name:
                metadata_parts.append(f"ShortName: {short_name}")
            
            # 1. Location
            pos = node_info.get('position', {})
            lat = pos.get('latitude')
            lon = pos.get('longitude')
            if lat is not None and lon is not None:
                metadata_parts.append(f"Location: {lat:.4f}, {lon:.4f}")
            
            # 2. Device Metrics
            metrics = node_info.get('deviceMetrics', {})
            battery = metrics.get('batteryLevel')
            voltage = metrics.get('voltage')
            chan_util = metrics.get('channelUtilization')
            air_util = metrics.get('airUtilTx')
            uptime = metrics.get('uptimeSeconds')
            
            if battery is not None:
                metadata_parts.append(f"Battery: {battery}%")
            if voltage is not None:
                metadata_parts.append(f"Voltage: {voltage:.2f}V")
            if chan_util is not None:
                metadata_parts.append(f"ChUtil: {chan_util:.1f}%")
            if air_util is not None:
                metadata_parts.append(f"AirUtil: {air_util:.1f}%")
            if uptime is not None:
                # Convert seconds to simpler format if needed, but seconds is fine for AI
                metadata_parts.append(f"Uptime: {uptime}s")

            # 3. Signal Strength
            snr = node_info.get('snr')
            rssi = node_info.get('rssi')
            if snr is not None:
                metadata_parts.append(f"SNR: {snr:.1f}dB")
            if rssi is not None:
                metadata_parts.append(f"RSSI: {rssi}dBm")

            # 4. Environment Metrics
            # First check direct node info, then fall back to our internal cache
            env = node_info.get('environmentMetrics', {})
            if not env and node_id in self.env_telemetry_cache:
                env = self.env_telemetry_cache[node_id]
                logger.debug(f"Using cached telemetry for {node_id}")

            # Map API keys to Display labels
            env_map = {
                'temperature': 'Temp',
                'relativeHumidity': 'Hum',
                'barometricPressure': 'Press',
                'lux': 'Lux',
                'white_lux': 'WhiteLux',
                'ir_lux': 'IRLux',
                'gas_resistance': 'Gas',
                'iaq': 'IAQ',
                'distance': 'Dist',
                'wind_speed': 'Wind',
                'wind_gust': 'Gust',
                'wind_direction': 'WindDir',
                'rainfall_1h': 'Rain1h',
                'rainfall_24h': 'Rain24h',
                'soil_moisture': 'SoilMoist',
                'soil_temperature': 'SoilTemp'
            }
            
            for key, label in env_map.items():
                val = env.get(key)
                if val is not None:
                    # Format floats to 1 or 2 decimal places
                    if isinstance(val, float):
                         if key in ['lux', 'white_lux', 'ir_lux', 'gas_resistance']:
                             val_str = f"{val:.1f}"
                         elif key == 'barometricPressure':
                             val_str = f"{val:.1f}hPa"
                         elif key == 'temperature' or key == 'soil_temperature':
                             val_str = f"{val:.1f}C"
                         elif key == 'relativeHumidity' or key == 'soil_moisture':
                             val_str = f"{val:.1f}%"
                         else:
                             val_str = f"{val:.2f}"
                    else:
                        val_str = str(val)
                    
                    metadata_parts.append(f"{label}: {val_str}")
                
            if not metadata_parts:
                return None
                
            return "(" + ", ".join(metadata_parts) + ")"
            
        except Exception as e:
            logger.debug(f"Failed to get metadata for {node_id}: {e}")
            return None

    def get_node_info(self):
        """
        Get information about the local Meshtastic node.
        
        Returns:
            dict or None: Node information if available
        """
        if not self.interface:
            return None
        
        try:
            # 1. Get static info
            my_info = self.interface.getMyNodeInfo()
            if not my_info:
                return None
                
            # 2. Try to supplement with dynamic data from the nodes database
            my_num = my_info.get('num')
            if my_num and self.interface.nodes:
                dynamic_info = self.interface.nodes.get(my_num)
                if dynamic_info:
                    # Merge dynamic info into base info, prioritizing dynamic (contains latest position etc)
                    my_info.update(dynamic_info)
                    
            return my_info
        except Exception as e:
            logger.error(f"Failed to get node info: {e}")
            return None




class MessageQueue:
    """
    Background message queue processor for reliable sending.
    
    Features:
    - Thread-safe queueing
    - Handling of long messages (chunking)
    - Rate limiting between chunks
    - Reliable delivery with ACK confirmation and retries
    - Fallback for broadcast messages (no ACK)
    """
    def __init__(self, handler):
        self.handler = handler
        self.queue = []
        self.lock = threading.Lock()
        self.processing = False
        self.thread = None
        
        # Start background thread
        self.start()
    
    def start(self):
        """Start the processing thread."""
        if self.thread and self.thread.is_alive():
            return
            
        self.processing = True
        self.thread = threading.Thread(target=self._process_loop, daemon=True)
        self.thread.start()
        logger.info("MessageQueue processor started")
        
    def enqueue(self, text, destination_id, channel_index, session_indicator):
        """Add a message to the queue."""
        with self.lock:
            self.queue.append({
                'text': text,
                'dest': destination_id,
                'chan': channel_index,
                'sess': session_indicator,
                'time': time.time()
            })
            logger.debug(f"Message queued for {destination_id} (Queue size: {len(self.queue)})")
            
    def _process_loop(self):
        """Main processing loop."""
        while self.processing and self.handler.running:
            item = None
            with self.lock:
                if self.queue:
                    item = self.queue.pop(0)
            
            if item:
                self._send_item(item)
            else:
                time.sleep(0.5)
                
    def _send_item(self, item):
        """Process a single queue item (splits and sends chunks)."""
        text = item['text']
        dest = item['dest']
        chan = item['chan']
        sess = item['sess']
        
        # Split message
        chunks = self.handler._split_message(text)
        total_chunks = len(chunks)
        is_broadcast = (dest == '^all')
        
        for i, chunk in enumerate(chunks):
            # 1. Format Payload
            payload = chunk
            if total_chunks > 1:
                payload = f"[{i+1}/{total_chunks}] {chunk}"
            payload = f"{sess}{payload}"
            
            # 2. Send with retries
            success = self._send_chunk_reliable(payload, dest, chan, is_broadcast, i+1, total_chunks)
            if not success:
                logger.error(f"Message delivery failed for chunk {i+1}/{total_chunks}. Dropping remaining chunks.")
                break
                
            # 3. Rate limiting/Pacing between chunks
            if i < total_chunks - 1:
                time.sleep(2)  # Small delay between chunks
                
    def _send_chunk_reliable(self, payload, dest, chan, is_broadcast, chunk_num, total_chunks):
        """Send a single chunk with retries."""
        max_retries = 3
        retry_delay = 10
        
        for attempt in range(max_retries):
            # Prepare ACK event
            self.handler.current_ack_event = threading.Event() if not is_broadcast else None
            self.handler.expected_ack_id = None
            
            try:
                # Send
                packet = self.handler.interface.sendText(
                    payload,
                    destinationId=dest,
                    channelIndex=chan,
                    wantAck=not is_broadcast
                )
                
                pkt_id = getattr(packet, 'id', 'unknown')
                self.handler.expected_ack_id = pkt_id
                
                logger.info(f"Sending chunk {chunk_num}/{total_chunks} (ID: {pkt_id}, Try: {attempt+1})")
                
                # Check ACK
                if not is_broadcast and pkt_id != 'unknown':
                    # Wait for ACK
                    if self.handler.current_ack_event.wait(timeout=20):  # 20s timeout
                        logger.info(f"✅ ACK received for chunk {chunk_num}")
                        return True
                    else:
                        logger.warning(f"⚠️ ACK timeout for chunk {chunk_num} (ID: {pkt_id})")
                else:
                    # Broadcast or unknown ID -> assume success
                    return True
                    
            except Exception as e:
                logger.error(f"Error sending chunk {chunk_num}: {e}")
            
            # If not successful, wait before retry
            if attempt < max_retries - 1:
                backoff = retry_delay * (attempt + 1)
                logger.info(f"Retrying chunk {chunk_num} in {backoff}s...")
                time.sleep(backoff)
                
        return False
