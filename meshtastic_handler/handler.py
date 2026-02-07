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
from meshtastic import mesh_pb2
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
    
    def connect(self, on_receive_callback=None):
        """
        Establish connection to Meshtastic device.
        
        Args:
            on_receive_callback: Function to call when messages are received
                                Signature: callback(packet, interface)
        
        Returns:
            bool: True if connection successful
        """
        max_retries = 5
        retry_delay = 5
        
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
                
                # Subscribe to ACKs for reliable sending
                try:
                    pub.unsubscribe(self._on_ack, "meshtastic.ack")
                except:
                    pass
                pub.subscribe(self._on_ack, "meshtastic.ack")
                logger.info("✅ Subscribed to meshtastic.ack")

                self.running = True
                logger.info("✅ Connected to Meshtastic")
                return True
                
            except Exception as e:
                logger.error(f"❌ Connection failed (Attempt {attempt+1}/{max_retries}): {e}")
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
    
    def _on_ack(self, packetId, interface):
        """Handle incoming ACK events."""
        if getattr(self, 'current_ack_event', None) and getattr(self, 'expected_ack_id', None) == packetId:
            logger.debug(f"⚡ Event-driven ACK received for ID {packetId}")
            self.current_ack_event.set()

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
    
    def get_node_metadata(self, node_id):
        """
        Get metadata (location, battery, environment) for a node.
        
        Args:
            node_id: Node ID (e.g., '!1234abcd')
            
        Returns:
            str: Formatted metadata string or None
        """
        if not self.interface or not self.interface.nodes:
            return None
            
        try:
            # Convert node_id string to int if needed (meshtastic nodes use integers)
            node_int = node_id
            if isinstance(node_id, str):
                if node_id.startswith('!'):
                    node_int = int(node_id[1:], 16)
                elif node_id.isdigit():
                    node_int = int(node_id)
            
            node_info = self.interface.nodes.get(node_int)
            if not node_info:
                return None
                
            metadata_parts = []
            
            # 1. Location
            pos = node_info.get('position', {})
            lat = pos.get('latitude')
            lon = pos.get('longitude')
            if lat is not None and lon is not None:
                metadata_parts.append(f"Location: {lat:.4f}, {lon:.4f}")
            
            # 2. Device Metrics (Battery)
            metrics = node_info.get('deviceMetrics', {})
            battery = metrics.get('batteryLevel')
            voltage = metrics.get('voltage')
            if battery is not None:
                metadata_parts.append(f"Battery: {battery}%")
            elif voltage is not None:
                metadata_parts.append(f"Voltage: {voltage:.2f}V")
                
            # 3. Environment Metrics (Temp, Pressure, Humidity)
            env = node_info.get('environmentMetrics', {})
            temp = env.get('temperature')
            press = env.get('barometricPressure')
            hum = env.get('relativeHumidity')
            if temp is not None:
                metadata_parts.append(f"Temp: {temp:.1f}C")
            if press is not None:
                metadata_parts.append(f"Pressure: {press:.1f}hPa")
            if hum is not None:
                metadata_parts.append(f"Humidity: {hum:.1f}%")
                
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
            return self.interface.getMyNodeInfo()
        except Exception as e:
            logger.error(f"Failed to get node info: {e}")
            return None

    def is_connected(self):
        """
        Check if currently connected to Meshtastic.
        
        Returns:
            bool: True if connected
        """
        return self.interface is not None and self.running


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
