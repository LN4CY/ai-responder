# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""AI query execution and provider interaction mixin."""

import threading
from core._compat import (
    get_provider_fn,
    get_time,
    get_requests,
    get_config_module,
    get_logger,
)


class QueryMixin:
    """Mixin for AI query handling, tool orchestration, and background worker threads."""

    def get_ai_response(self, prompt, history_key, is_session=False, location=None, tools=None):
        """
        Get AI response using the configured provider with tuned context.
        
        Args:
            prompt: User's input text
            history_key: Key for history context
            is_session: Whether this is an active continuous session
            location: Optional location dict {'latitude': float, 'longitude': float}
            
        Returns:
            str: AI response or error message
        """
        logger = get_logger()
        config = get_config_module()
        get_provider = get_provider_fn()
        provider_name = self.config.get('current_provider', 'ollama')
        
        try:
            # Get provider instance
            provider = get_provider(provider_name, self.config)
            
            # Fetch dynamic tools from MCP client
            tools = None
            if provider.supports_tools:
                tools = self.mcp_client.get_all_tools()
            
            # Get history for context
            history = None
            if history_key and history_key in self.history:
                if not is_session:
                    limit = 2
                elif self.mcp_client and self.mcp_client.has_server('memory'):
                    is_local = provider_name == 'ollama'
                    limit = (config.MEMORY_BOOTSTRAP_LOCAL if is_local
                             else config.MEMORY_BOOTSTRAP_ONLINE)
                    logger.debug(f"Semantic Memory active → bootstrap history limit={limit} "
                                 f"({'local' if is_local else 'online'} provider)")
                else:
                    limit = 30
                history = self.history[history_key][-limit:]
            
            # Get response
            response = provider.get_response(
                prompt, history, context_id=history_key, location=location, 
                tools=tools, mcp_client=self.mcp_client
            )
            return response
            
        except ValueError as e:
            logger.error(f"Provider error: {e}")
            return f"Error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error getting AI response: {e}")
            return f"Error: {str(e)}"

    def _handle_ai_query(self, query, from_node, to_node, channel, is_dm=None, initial_msg="Thinking... 🤖", packet_metadata=None):
        """
        Handle an AI query in a background thread.
        
        Args:
            query: User's question/prompt
            from_node: Source node ID
            to_node: Destination node ID
            channel: Channel index
            is_dm: Optional DM status (if already determined)
            initial_msg: Initial "thinking" message to send
            packet_metadata: Optional telemetry dictionary for the incoming packet
        """
        # Determine if this is a DM interaction if not provided
        if is_dm is None:
            is_dm = self._is_dm(to_node)
        
        # Send initial acknowledgment
        if initial_msg:
            self.send_response(initial_msg, from_node, to_node, channel, is_admin_cmd=False)
        
        # Process in background thread
        thread = threading.Thread(
            target=self._process_ai_query_thread,
            args=(query, from_node, to_node, channel, is_dm, False, False, packet_metadata)
        )
        thread.daemon = True
        thread.start()
    
    def _touch_worker(self):
        """Update the active worker timestamp to prevent false-positive hang detection during long tools."""
        time = get_time()
        thread_id = threading.get_ident()
        with self._workers_lock:
            if thread_id in self._active_workers:
                self._active_workers[thread_id]['start_time'] = time.time()

    def _get_location_address_mcp(self, lat, lon):
        """Tool to reverse geocode lat/lon to a physical address using OpenStreetMap"""
        logger = get_logger()
        requests = get_requests()
        logger.info(f"📍 Reverse geocoding requested for {lat}, {lon}")
        url = "https://nominatim.openstreetmap.org/reverse"
        
        headers = {
            "User-Agent": "AI-Responder-Meshtastic/1.5 (https://github.com/LN4CY/ai-responder)"
        }
        
        params = {
            "format": "json",
            "lat": lat,
            "lon": lon,
            "zoom": 18,
            "addressdetails": 1
        }
        
        try:
            self._touch_worker()
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "display_name" in data:
                    return f"Address found: {data['display_name']}"
                else:
                    return "No address found for these coordinates."
            else:
                return f"Error geocoding: HTTP {response.status_code}"
        except Exception as e:
            logger.error(f"Geocoding error: {e}")
            return f"Error contacting geocoding service: {e}"

    def _inject_legacy_metadata(self, query, from_node, packet_metadata=None):
        """Helper to inject a clean metadata block for tool-blind models."""
        handler = getattr(self, 'meshtastic', None)
        if not handler:
            return query
        my_id = (handler.get_node_info() or {}).get('user', {}).get('id')
        my_info = handler.get_node_metadata(my_id) if my_id else None
        neighbor_summary = handler.get_node_list_summary(max_nodes=8) if hasattr(handler, 'get_node_list_summary') else None
        user_info = handler.get_node_metadata(from_node)
        
        metadata_block = "\n\n[RADIO CONTEXT]\n"
        if my_info:
            metadata_block += f"Self: {my_info}\n"
        if user_info:
            metadata_block += f"User ({from_node}): {user_info}\n"
        if packet_metadata:
            pkt_desc = []
            hops = packet_metadata.get('hops')
            if hops is not None:
                pkt_desc.append(f"{hops} hops" if hops > 0 else "direct (0 hops)")
            if packet_metadata.get('path'):
                pkt_desc.append(f"Route: {packet_metadata['path']}")
            if packet_metadata.get('relay_node'):
                pkt_desc.append(f"Relayed by: {packet_metadata['relay_node']}")
            if packet_metadata.get('snr') is not None:
                snr = packet_metadata['snr']
                pkt_desc.append(f"SNR: {snr:.1f}dB" if isinstance(snr, (int, float)) else f"SNR: {snr}dB")
            if packet_metadata.get('rssi') is not None:
                pkt_desc.append(f"RSSI: {packet_metadata['rssi']}dBm")
            if packet_metadata.get('via_mqtt'):
                pkt_desc.append("Transport: MQTT")
            elif hops is not None or packet_metadata.get('snr') is not None:
                pkt_desc.append("Transport: LoRa RF")
            if pkt_desc:
                metadata_block += f"Inbound Packet: {' | '.join(pkt_desc)}\n"
        if neighbor_summary:
            metadata_block += f"{neighbor_summary}\n"
        metadata_block += "[/RADIO CONTEXT]"
        
        return f"{query}{metadata_block}"

    def _process_ai_query_thread(self, query, from_node, to_node, channel, is_dm=False, is_system_trigger=False, disable_tools=False, packet_metadata=None):
        """Background thread for processing AI queries with adaptive tool support."""
        logger = get_logger()
        time = get_time()
        config = get_config_module()
        get_provider = get_provider_fn()

        thread_id = threading.get_ident()
        network = getattr(self, 'mesh_type', 'meshtastic')
        with self._workers_lock:
            self._active_workers[thread_id] = {
                'start_time': time.time(),
                'from_node': from_node,
                'to_node': to_node,
                'channel': channel,
                'is_system_trigger': is_system_trigger,
                'network': network,
                'packet_metadata': packet_metadata
            }
            
        # Inject context for MCP tools running in this thread
        threading.current_thread().ai_context = {
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'is_system_trigger': is_system_trigger,
            'network': network,
            'packet_metadata': packet_metadata
        }
            
        try:
            # Short sleep to allow "Thinking..." message to clear if needed
            time.sleep(2)

            # 1. Get History Key and Session Status
            is_session = is_dm and self.session_manager.is_active(from_node)
            history_key = self._get_history_key(from_node, channel, is_dm)
            current_history = list(self.history.get(history_key, []))
            
            # 2. Capability Check & Tool Orchestration
            provider_name = self.config.get('current_provider', 'ollama')
            provider = get_provider(provider_name, self.config)
            
            # User-controlled awareness toggle
            awareness_enabled = self.config.get('meshtastic_awareness', config.MESHTASTIC_AWARENESS)
            
            tools = None
            final_query = query
            
            if not awareness_enabled:
                logger.info("🚫 Mesh Awareness is DISABLED. Skipping metadata/tools.")
                if not is_system_trigger:
                    self.add_to_history(history_key, 'user', query, node_id=from_node)
                    current_history = list(self.history.get(history_key, []))
                else:
                    current_history.append({'role': 'user', 'content': query})
            else:
                is_first_msg = len(current_history) == 0
                needs_refresh = (from_node in self._refresh_metadata_nodes)
                
                keywords = ['battery', 'voltage', 'location', 'where', 'snr', 'rssi', 'distance', 'away', 'status', 'path', 'hop', 'hops', 'route', 'repeater']
                is_keyword_query = any(k in query.lower() for k in keywords)
                has_packet_metadata = bool(packet_metadata and any(v is not None for v in packet_metadata.values()))
                
                mentions_bot = False
                active_handler = getattr(self, 'meshtastic', None)
                my_node_info = active_handler.get_node_info() if active_handler else {}
                if my_node_info:
                    bot_names = [
                        str(my_node_info.get('user', {}).get('longName') or '').lower(),
                        str(my_node_info.get('user', {}).get('shortName') or '').lower(),
                        'bot', 'you'
                    ]
                    bot_names = [n for n in bot_names if n]
                    mentions_bot = any(n in query.lower() for n in bot_names)

                must_refresh = is_first_msg or needs_refresh or is_keyword_query or mentions_bot or has_packet_metadata
                
                combined_metadata = None
                if must_refresh:
                    my_info = active_handler.get_node_info() or {} if active_handler else {}
                    my_id = my_info.get('user', {}).get('id')
                    local_metadata = active_handler.get_node_metadata(my_id) if active_handler else None
                    remote_metadata = active_handler.get_node_metadata(from_node) if active_handler else None
                    combined_metadata = self._format_dual_metadata(
                        local_metadata,
                        remote_metadata,
                        network=network if network == 'meshcore' else None,
                        packet_metadata=packet_metadata
                    )
                    
                    if from_node in self._refresh_metadata_nodes:
                        self._refresh_metadata_nodes.remove(from_node)
                
                if provider.supports_tools:
                    if not disable_tools:
                        logger.info(f"🤖 Provider '{provider.name}' supports tools. Using function calling.")
                        tools = self.mcp_client.get_all_tools()
                    else:
                        logger.info(f"🤖 Provider '{provider.name}' supports tools, but they are disabled for this turn.")
                    
                    if not is_system_trigger:
                        self.add_to_history(history_key, 'user', query, node_id=from_node, metadata=combined_metadata)
                        current_history = list(self.history.get(history_key, []))
                    else:
                        msg = f"User ({from_node}): {query}" if combined_metadata else query
                        current_history.append({'role': 'user', 'content': msg})
                else:
                    logger.info(f"💾 Provider '{provider.name}' does not support native tools. Injecting legacy metadata block.")
                    final_query = self._inject_legacy_metadata(query, from_node, packet_metadata=packet_metadata) if combined_metadata else query
                    if not is_system_trigger:
                        self.add_to_history(history_key, 'user', query, node_id=from_node, metadata=combined_metadata)
                        current_history = list(self.history.get(history_key, []))
                        if final_query != query and current_history:
                            current_history[-1] = {'role': 'user', 'content': final_query}
                    else:
                        current_history.append({'role': 'user', 'content': final_query})

            # 3. Add to history logging
            current_session = self.session_manager.get_session_name(from_node)
            msgs_count = len(current_history)
            logger.info(f"🧠 AI Context: Session='{current_session or 'None'}' | Messages={msgs_count} | Trigger={is_system_trigger}")

            # 4. Extract Primary Location for Grounding if available (only if awareness is enabled)
            location = None
            if awareness_enabled and hasattr(self.meshtastic, '_get_node_by_id'):
                try:
                    node_info = self.meshtastic._get_node_by_id(from_node)
                    if node_info:
                        pos = node_info.get('position', {})
                        lat = pos.get('latitude')
                        if lat is None and pos.get('latitudeI') is not None:
                            lat = pos.get('latitudeI') / 1e7
                        lon = pos.get('longitude')
                        if lon is None and pos.get('longitudeI') is not None:
                            lon = pos.get('longitudeI') / 1e7
                        if lat is not None and lon is not None:
                            location = {'latitude': lat, 'longitude': lon}
                            logger.info(f"📍 Primary location identified for grounding: {lat:.6f}, {lon:.6f}")
                except Exception as e:
                    logger.debug(f"Could not extract primary location for grounding: {e}")

            # 5. Get AI response
            if self.mcp_client and isinstance(getattr(self.mcp_client, 'has_server', None), object) and self.mcp_client.has_server('memory') is True:
                is_local = str(provider_name).lower() in ('ollama', 'local')
                history_limit = config.MEMORY_BOOTSTRAP_LOCAL if is_local else config.MEMORY_BOOTSTRAP_ONLINE
            else:
                history_limit = 30
            response = provider.get_response(final_query, current_history[-history_limit:], 
                                          context_id=history_key if not is_system_trigger else f"sys_{history_key}", 
                                          location=location, tools=tools, mcp_client=self.mcp_client)
            
            # 6. Silent-ACK
            if response == "__SILENT_ACK__":
                logger.info("🔇 Silent ACK — all telemetry was handled by proactive callbacks. No reply sent.")
                return
            
            # 7. Add assistant response to history
            if not is_system_trigger:
                self.add_to_history(history_key, 'assistant', response)
                
                if is_dm:
                    session_name = self.session_manager.get_session_name(from_node)
                    if session_name:
                        with self._history_lock:
                            saved_history = list(self.history.get(history_key, []))
                        self.conversation_manager.save_conversation(from_node, session_name, saved_history)
                        self.session_manager.update_activity(from_node)
            
            # 9. Background Semantic Indexing
            self._index_to_mcp('conversation', {
                'node_id': from_node,
                'channel': channel,
                'prompt': query,
                'response': response,
                'is_dm': is_dm,
                'is_system': is_system_trigger,
                'packet_metadata': packet_metadata
            })
            
            logger.info(f"💬 {provider.name} response ({len(response)} chars): {response[:80]}...")
            self.send_response(response, from_node, to_node, channel, is_admin_cmd=False, use_session_indicator=is_session)
            logger.info(f"✅ Response queued to {from_node} on ch{channel}")
            
        except Exception as e:
            logger.error(f"Error processing AI query: {e}", exc_info=True)
            self.send_response(f"❌ Error: {str(e)[:50]}", from_node, to_node, channel)
        finally:
            with self._workers_lock:
                self._active_workers.pop(thread_id, None)

    def _log_provider_info(self):
        """Log the current AI provider and model."""
        logger = get_logger()
        config = get_config_module()
        current_provider = self.config.get('current_provider', 'ollama')
        logger.info(f"🤖 Active AI Provider: {current_provider.upper()}")
        
        if current_provider == 'gemini':
            logger.info(f"🧠 Model: {config.GEMINI_MODEL}")
        elif current_provider == 'ollama':
            logger.info(f"🦙 Model: {config.OLLAMA_MODEL}")
        elif current_provider == 'openai':
            logger.info(f"🤖 Model: {config.OPENAI_MODEL}")
        elif current_provider == 'anthropic':
            logger.info(f"🧠 Model: {config.ANTHROPIC_MODEL}")
