"""
Meshtastic interface handler.

This module manages the Meshtastic connection and message handling,
including sending messages, managing connections, and processing incoming packets.
"""

import time
import logging
import sys
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
                    elif debug_decoded.HasField("mqttClientProxyMessage"):
                        logger.debug(f"Content Is: MQTT Proxy Message (topic: {debug_decoded.mqttClientProxyMessage.topic})")
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
    
    def send_message(self, text, destination_id, channel_index=0, session_indicator=""):
        """
        Send a message via Meshtastic with automatic chunking and rate limiting.
        
        Long messages are automatically split into chunks to fit Meshtastic's
        message size limits. Rate limiting prevents flooding the mesh network.
        
        Args:
            text: Message text to send
            destination_id: Target node ID (e.g., '!abc123') or '^all' for broadcast
            channel_index: Meshtastic channel index (default: 0)
            session_indicator: Optional prefix for session messages (e.g., '[🟢 session] ')
        
        Returns:
            bool: True if message sent successfully
        """
        if not self.interface:
            logger.error("Cannot send message: Not connected to Meshtastic")
            return False
        
        # Split message into chunks if needed
        chunks = self._split_message(text)
        total_chunks = len(chunks)
        
        for chunk_index, chunk in enumerate(chunks):
            # Add rate limiting delay between chunks
            if chunk_index > 0:
                # Dynamic rate limiting: 5s for DM, 15s for broadcast
                delay_seconds = 15 if destination_id == '^all' else 5
                logger.info(f"Rate limiting: Waiting {delay_seconds}s before chunk {chunk_index + 1}/{total_chunks}")
                time.sleep(delay_seconds)
            
            try:
                # Format chunk with numbering if multiple chunks
                display_chunk = chunk
                if total_chunks > 1:
                    display_chunk = f"[{chunk_index + 1}/{total_chunks}] {chunk}"
                
                # Add session indicator
                display_chunk = f"{session_indicator}{display_chunk}"
                
                # Send via Meshtastic
                packet = self.interface.sendText(
                    display_chunk,
                    destinationId=destination_id,
                    channelIndex=channel_index
                )
                
                packet_id = packet.get('id') if isinstance(packet, dict) else 'unknown'
                logger.info(f"Chunk {chunk_index + 1}/{total_chunks} queued (ID: {packet_id})")
                
                # Wait for ACK if not broadcast
                if destination_id != '^all':
                    self._wait_for_ack(packet, chunk_index + 1)
                else:
                    logger.info("Broadcast sent (no ACK expected)")
                    
            except Exception as e:
                logger.error(f"Failed to send chunk {chunk_index + 1}: {e}")
                return False
        
        return True
    
    def _split_message(self, text, max_length=200):
        """
        Split a long message into chunks.
        
        Attempts to split at sentence boundaries when possible to maintain
        readability.
        
        Args:
            text: Text to split
            max_length: Maximum characters per chunk
        
        Returns:
            list: List of message chunks
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
    
    def _wait_for_ack(self, packet, chunk_number):
        """
        Wait for message acknowledgment.
        
        Args:
            packet: Packet object returned from sendText
            chunk_number: Chunk number for logging
        """
        try:
            if hasattr(packet, 'wait_for_ack'):
                if packet.wait_for_ack(timeout=self.ack_timeout):
                    logger.info(f"✅ Received ACK for chunk {chunk_number}")
                else:
                    logger.warning(f"⚠️ ACK timeout for chunk {chunk_number} (Queue might be full/congested)")
        except Exception as ack_error:
            logger.warning(f"Error waiting for ACK: {ack_error}")
    
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
