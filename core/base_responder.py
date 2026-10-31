# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Base AI Responder class shared across protocol-specific responder implementations."""

import os
import sys
import pathlib
import itertools
import threading
from concurrent.futures import ThreadPoolExecutor

from core._compat import (
    get_time,
    get_config_module,
    get_config_cls,
    get_conversation_manager_cls,
    get_session_manager_cls,
    get_logger,
    resolve_attr,
)
from core.history_mixin import HistoryMixin
from core.indexing_mixin import IndexingMixin
from core.proactive_mixin import ProactiveMixin
from core.commands_mixin import CommandsMixin
from core.query_mixin import QueryMixin


class BaseAIResponder(HistoryMixin, IndexingMixin, ProactiveMixin, CommandsMixin, QueryMixin):
    """
    Abstract base class for protocol-specific AI Responders.

    Provides shared state management, conversation history, semantic indexing,
    proactive tasks, command routing, AI query execution, and main health loop.
    Subclasses (`MeshtasticAIResponder`, `MeshCoreAIResponder`) initialize their
    own radio handler, MCP server, and protocol-specific admin/channel rules.
    """

    mesh_type = "base"

    def _init_common_state(self, history_dir=None):
        """Initialize shared state, managers, locks, and background executors."""
        logger = get_logger()
        time = get_time()
        config = get_config_module()
        ConfigCls = get_config_cls()
        ConversationManagerCls = get_conversation_manager_cls()
        SessionManagerCls = get_session_manager_cls()

        self.config = ConfigCls()

        if history_dir is None:
            history_dir = config.HISTORY_DIR
        self.history_dir = history_dir

        self.conversation_manager = ConversationManagerCls()
        self.session_manager = SessionManagerCls(self.conversation_manager)

        # Track nodes that need a metadata refresh in their next message
        self._refresh_metadata_nodes = set()

        # State management
        self.running = True
        self.last_activity = time.time()
        self.last_probe = 0
        self.connection_lost = False

        # In-memory history cache
        self.history = {}
        self._history_lock = threading.RLock()
        self._proactive_persistence_lock = threading.RLock()

        # Worker tracking
        self._active_workers = {}
        self._workers_lock = threading.Lock()

        # Proactive Agent State
        self._task_counter = itertools.count(1)
        self.scheduled_tasks = []
        self._scheduled_tasks_lock = threading.Lock()

        self.condition_watchers = []
        self._condition_watchers_lock = threading.Lock()

        self.pending_telemetry_requests = {}

        self.node_online_watchers = []
        self._node_online_watchers_lock = threading.Lock()

        # Collector Pattern State
        self._proactive_event_collector = {}
        self._collector_lock = threading.Lock()

        # Background Memory Indexing
        self._bg_executor = ThreadPoolExecutor(max_workers=3)
        self._mcp_indexing_cache = {}

        # Ensure history directory exists
        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)
            logger.info(f"Created history directory: {self.history_dir}")

        # Initial task load from disk
        self._load_proactive_tasks()

    def _merge_env_list(self, env_val, config_key, is_int=False):
        """Merge a comma-separated environment variable into a persistent config list."""
        logger = get_logger()
        if not env_val:
            return
        try:
            raw_items = [n.strip() for n in env_val.split(',') if n.strip()]
            parsed = [int(n) for n in raw_items if n.isdigit()] if is_int else raw_items
            if parsed:
                existing = self.config.get(config_key, [])
                updated = False
                for item in parsed:
                    if item not in existing:
                        existing.append(item)
                        updated = True
                if updated or config_key not in self.config.data:
                    self.config[config_key] = existing
                    self.config.save()
                    logger.info(f"Initialized {config_key} from environment: {existing}")
        except Exception as e:
            logger.warning(f"Failed to parse environment setting for {config_key}: {e}")

    def _cleanup_admin_list(self, config_key):
        """Auto-clean comma-separated strings in a persisted admin list."""
        logger = get_logger()
        current_admins = self.config.get(config_key, [])
        cleaned_admins = []
        needs_cleanup = False
        for item in current_admins:
            if isinstance(item, str) and ',' in item:
                needs_cleanup = True
                cleaned_admins.extend([n.strip() for n in item.split(',') if n.strip()])
            else:
                cleaned_admins.append(str(item).strip())
        if needs_cleanup:
            final_admins = []
            for item in cleaned_admins:
                if item not in final_admins:
                    final_admins.append(item)
            logger.info(f"🧹 Auto-cleaned corrupted {config_key} list from config: {final_admins}")
            self.config[config_key] = final_admins
            self.config.save()

    def _init_provider_from_env(self):
        """Initialize AI provider from environment variable if config is new."""
        logger = get_logger()
        config = get_config_module()
        ai_provider = resolve_attr('AI_PROVIDER', getattr(config, 'AI_PROVIDER', None))
        if ai_provider:
            if self.config.is_new or 'current_provider' not in self.config.data:
                self.config['current_provider'] = ai_provider
                self.config.save()
                logger.info(f"Initialized AI provider from environment: {ai_provider}")

    def touch_health(self):
        """Touch the healthy file to indicate the service is running."""
        try:
            pathlib.Path("/tmp/healthy").touch()
        except Exception:
            pass

    def on_receive(self, packet, interface):
        """Callback for incoming mesh packets."""
        logger = get_logger()
        time = get_time()
        self.touch_health()

        try:
            self.last_activity = time.time()

            # Check node-online watchers on EVERY packet (before portnum filter)
            from_id_raw = packet.get('fromId')
            if from_id_raw:
                if isinstance(from_id_raw, int):
                    from_id_check = f"!{from_id_raw:08x}"
                else:
                    from_id_check = str(from_id_raw).lower()

                # Build set of matching ID representations (name, hex ID, pubkey prefix)
                raw_prefix = str(packet.get('raw_pubkey_prefix', '')).lower()
                alt_ids = {from_id_check, from_id_check.lstrip('!')}
                if raw_prefix:
                    alt_ids.add(raw_prefix)
                    alt_ids.add(f"!{raw_prefix}")

                radio = getattr(self, 'meshtastic', None)
                if radio and hasattr(radio, 'find_node_by_name'):
                    try:
                        resolved = radio.find_node_by_name(from_id_check)
                        if resolved and isinstance(resolved, str):
                            alt_ids.add(resolved.lower())
                            alt_ids.add(resolved.lower().lstrip('!'))
                    except Exception:
                        pass

                with self._node_online_watchers_lock:
                    triggered = []
                    for w in self.node_online_watchers:
                        wid = str(w.get('node_id', '')).lower()
                        wid_clean = wid.lstrip('!')
                        if wid in alt_ids or wid_clean in alt_ids:
                            triggered.append(w)
                    for w in triggered:
                        if not w.get('is_persistent', False):
                            self.node_online_watchers.remove(w)
                    if triggered:
                        self._save_proactive_tasks()

                for w in triggered:
                    context = f"Node online alert: {w['context_note']}. Node {from_id_check} was just heard on the mesh."
                    self._fire_system_trigger(context, w['from_node'], w['to_node'], w['channel'], targets=w.get('targets', 'requester'), disable_tools=True)
                    logger.info(f"🟢 Node-online watcher fired for {from_id_check}")

            # Extract packet data
            if 'decoded' not in packet or 'portnum' not in packet['decoded']:
                return

            if packet['decoded']['portnum'] != 'TEXT_MESSAGE_APP':
                return

            from_node = packet.get('fromId', 'unknown')
            to_node = packet.get('toId', 'unknown')
            channel = packet.get('channel', 0)
            text = packet['decoded'].get('text', '').strip()

            if not text:
                return

            logger.info(f"📨 [{self.mesh_type.upper()}] Message from {from_node} to {to_node} on channel {channel}: {text[:50]}...")

            # Extract live packet telemetry
            hops = packet.get('hops')
            if hops is None:
                hop_start = packet.get('hopStart', packet.get('hop_start'))
                hop_limit = packet.get('hopLimit', packet.get('hop_limit'))
                if hop_start is not None and hop_limit is not None:
                    hops = max(0, hop_start - hop_limit)

            snr = packet.get('snr', packet.get('rxSnr', packet.get('rx_snr')))
            rssi = packet.get('rssi', packet.get('rxRssi', packet.get('rx_rssi')))
            path = packet.get('path')
            relay_node = packet.get('relayNode', packet.get('relay_node'))
            via_mqtt = packet.get('viaMqtt', packet.get('via_mqtt', False))
            rx_time = packet.get('recv_time', packet.get('rxTime', packet.get('rx_time', packet.get('sender_timestamp'))))

            packet_metadata = {
                'hops': hops,
                'path': path,
                'snr': snr,
                'rssi': rssi,
                'relay_node': relay_node,
                'via_mqtt': via_mqtt,
                'rx_time': rx_time,
            }

            is_dm = self._is_dm(to_node)

            # Check if user is in an active session (DM only)
            if is_dm and self.session_manager.is_active(from_node):
                timed_out, message, session_channel, session_to_node = self.session_manager.check_timeout(from_node)
                if timed_out:
                    self.send_response(message, from_node, session_to_node, session_channel, is_admin_cmd=False)
                    return
                else:
                    clean_text = text.strip().lower()
                    if clean_text in ('end', 'exit', 'quit', 'close', '-end'):
                        success, end_msg, _, _ = self.session_manager.end_session(from_node)
                        self.send_response(end_msg, from_node, to_node, channel, is_admin_cmd=False)
                        return
                    if not text.startswith('!ai'):
                        self._handle_ai_query(text, from_node, to_node, channel, is_dm=is_dm, packet_metadata=packet_metadata)
                        return

            if text.lower().startswith('!ai'):
                self.process_command(text, from_node, to_node, channel, packet_metadata=packet_metadata)

        except Exception as e:
            logger.error(f"Error in on_receive: {e}", exc_info=True)

    def _on_connected_setup(self):
        """Hook for protocol-specific post-connection setup (e.g., pubsub subscriptions)."""
        pass

    def connect(self):
        """Connect to the radio handler and start the main health/scheduler loop."""
        logger = get_logger()
        time = get_time()
        config = get_config_module()
        health_timeout = resolve_attr('HEALTH_CHECK_ACTIVITY_TIMEOUT', getattr(config, 'HEALTH_CHECK_ACTIVITY_TIMEOUT', 300))

        net_name = "MeshCore" if self.mesh_type == "meshcore" else "Meshtastic"
        logger.info(f"🚀 AI Responder Service Starting ({net_name})...")

        self._log_provider_info()

        radio = getattr(self, 'meshtastic', None)
        if radio and radio.connect(on_receive_callback=self.on_receive):
            logger.info(f"✅ Initial connection to {net_name} successful.")
            if hasattr(self, '_bg_executor') and self._bg_executor:
                self._bg_executor.submit(self._bg_sync_graph_triples)
        else:
            logger.warning(f"⚠️ Initial connection to {net_name} failed. Will retry in main loop.")

        self._on_connected_setup()

        self.running = True

        try:
            self._last_health_log = 0
            while self.running:
                current_time = time.time()
                health_ok = True
                reasons = []

                radio = getattr(self, 'meshtastic', None)

                # Check radio activity
                if radio and hasattr(radio, 'last_activity') and radio.last_activity > 0:
                    time_since_radio = current_time - radio.last_activity
                    if time_since_radio > health_timeout:
                        time_since_last_probe = current_time - self.last_probe
                        if time_since_last_probe > 40:
                            logger.warning(f"Radio silent for {int(time_since_radio)}s. Sending active probe...")
                            self.last_probe = current_time
                            radio.send_probe()
                        elif time_since_last_probe > 30:
                            health_ok = False
                            reasons.append(f"Radio silent (Probed {int(time_since_last_probe)}s ago - NO REPLY)")

                # Check connection status
                if not radio or not radio.is_connected():
                    if not self.connection_lost:
                        self.connection_lost = True
                        self.last_activity = current_time
                        logger.warning(f"{net_name} connection lost. Attempting to reconnect...")

                    if int(current_time) % 10 == 0:
                        if radio and radio.connect(on_receive_callback=self.on_receive):
                            logger.info(f"✅ Reconnected to {net_name} successfully.")
                            self.connection_lost = False
                            if hasattr(self, '_bg_executor') and self._bg_executor:
                                self._bg_executor.submit(self._bg_sync_graph_triples)
                        else:
                            logger.warning(f"Still disconnected from {net_name}...")

                    if current_time - self.last_activity > 60:
                        health_ok = False
                        reasons.append("Connection lost for >60s (Reconnection attempts failed)")
                else:
                    self.connection_lost = False

                # Check message queue heartbeat
                queue_heartbeat = getattr(radio, '_message_queue', None) if radio else None
                if queue_heartbeat and hasattr(queue_heartbeat, 'last_heartbeat') and (current_time - queue_heartbeat.last_heartbeat > 300):
                    health_ok = False
                    reasons.append("Message queue thread stalled (>300s)")

                # Check for stalled worker threads
                with self._workers_lock:
                    for tid, worker_data in list(self._active_workers.items()):
                        if isinstance(worker_data, dict):
                            start_time = worker_data.get('start_time', 0)
                            from_node = worker_data.get('from_node')
                            to_node = worker_data.get('to_node')
                            channel = worker_data.get('channel')
                        else:
                            start_time = worker_data
                            from_node = to_node = channel = None

                        age = int(current_time - start_time)
                        if age > 90:
                            health_ok = False
                            reasons.append(f"AI Worker thread {tid} stalled ({age}s > 90s limit)")
                            logger.warning(f"🐛 Worker thread {tid} has been running for {age}s — likely stuck in DNS/network hang.")

                            if from_node and to_node and channel is not None:
                                logger.info(f"Sending timeout notification to {from_node}")
                                self.send_response(
                                    "⚠️ AI request timed out due to network congestion or API failure.",
                                    from_node,
                                    to_node,
                                    channel,
                                    is_admin_cmd=False
                                )
                                time.sleep(2)
                            break

                # Periodic health status log (every 60s)
                if current_time - self._last_health_log > 60:
                    self._last_health_log = current_time
                    with self._workers_lock:
                        active_count = len(self._active_workers)
                    queue = getattr(radio, '_message_queue', None) if radio else None
                    q_age = int(current_time - queue.last_heartbeat) if queue else -1
                    connected = radio.is_connected() if radio else False
                    logger.info(
                        f"💓 Health: connected={connected} | "
                        f"active_workers={active_count} | "
                        f"queue_last_heartbeat={q_age}s ago"
                    )

                if health_ok:
                    try:
                        with open("/tmp/healthy", "w") as f:
                            f.write(str(current_time))
                    except Exception:
                        pass
                else:
                    logger.error(f"Health check FAILED: {', '.join(reasons)}. Exiting...")
                    if os.path.exists("/tmp/healthy"):
                        try:
                            os.remove("/tmp/healthy")
                        except Exception:
                            pass
                    sys.exit(1)

                # Periodic session timeout check
                timed_out_data = self.session_manager.check_all_timeouts()
                for data in timed_out_data:
                    self.send_response(
                        data['message'],
                        data['user_id'],
                        data['to_node'],
                        data['channel'],
                        is_admin_cmd=False
                    )

                # Scheduled task ticker
                now = time.time()
                tasks_changed = False
                with self._scheduled_tasks_lock:
                    to_keep = []
                    for task in self.scheduled_tasks:
                        if now >= task['next_time']:
                            tasks_changed = True
                            task['run_count'] = task.get('run_count', 0) + 1
                            self._fire_system_trigger(
                                task['context_note'],
                                task['from_node'],
                                task['to_node'],
                                task['channel'],
                                targets=task.get('targets', 'requester'),
                                run_count=task['run_count'],
                                max_runs=task.get('max_runs')
                            )
                            interval = task.get('interval')
                            end_time = task.get('end_time')
                            max_runs = task.get('max_runs')
                            run_count = task.get('run_count', 0)

                            is_expired = False
                            if not interval:
                                is_expired = True
                            elif max_runs and run_count >= max_runs:
                                is_expired = True
                            elif end_time is not None and (now + interval) > (end_time + 2):
                                is_expired = True

                            if not is_expired:
                                next_fire = task['next_time'] + interval
                                if next_fire <= now:
                                    next_fire = now + interval
                                task['next_time'] = next_fire
                                to_keep.append(task)
                            else:
                                logger.info(f"🏁 Proactive task [{task.get('id')}] completed (runs: {run_count}) and was automatically removed.")
                        else:
                            to_keep.append(task)
                    self.scheduled_tasks = to_keep
                    if tasks_changed:
                        self._save_proactive_tasks()

                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("\n👋 Shutting down AI Responder...")
        finally:
            if radio and hasattr(radio, 'disconnect'):
                radio.disconnect()
            logger.info("✅ AI Responder stopped.")
