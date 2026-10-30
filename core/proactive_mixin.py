# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Proactive agent task scheduling, condition watchers, and event collector mixin."""

import os
import re
import json
import itertools
import threading
from core._compat import get_time, get_datetime, get_config_module, get_logger


class ProactiveMixin:
    """Mixin for proactive scheduled tasks, condition watchers, and event aggregation."""

    def _schedule_mcp_task(self, delay_seconds=None, context_note=None, recur_interval_seconds=None, max_duration_seconds=None, notify_targets=None, absolute_time=None, from_node=None, to_node=None, channel=0):
        """Tool handler: schedule a one-shot or recurring proactive message."""
        logger = get_logger()
        time = get_time()
        datetime = get_datetime()
        config = get_config_module()

        # DM-only enforcement
        if to_node == '^all':
            return "⚠️ Proactive alerts can only be registered from a Direct Message to avoid spamming public channels."

        now = time.time()
        
        # Determine delay from absolute time if provided
        if absolute_time:
            try:
                # 1. Try HH:MM (today or tomorrow)
                if re.match(r"^\d{1,2}:\d{2}$", absolute_time):
                    dt_now = datetime.datetime.now()
                    target_time = datetime.datetime.strptime(absolute_time, "%H:%M")
                    # Combine today's date with target time
                    target_dt = dt_now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
                    # If target is in the past, assume tomorrow
                    if target_dt < dt_now:
                        target_dt += datetime.timedelta(days=1)
                    delay_seconds = (target_dt - dt_now).total_seconds()
                # 2. Try YYYY-MM-DD HH:MM
                elif re.match(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}$", absolute_time):
                    target_dt = datetime.datetime.strptime(absolute_time, "%Y-%m-%d %H:%M")
                    delay_seconds = (target_dt - datetime.datetime.now()).total_seconds()
                else:
                    return f"Error: Unsupported time format '{absolute_time}'. Use HH:MM or YYYY-MM-DD HH:MM."
                
                if delay_seconds < 0:
                    return f"Error: Target time '{absolute_time}' is in the past."
            except Exception as e:
                logger.error(f"Error parsing absolute time '{absolute_time}': {e}")
                return f"Error parsing time: {e}"

        if delay_seconds is None and recur_interval_seconds is not None:
            delay_seconds = float(recur_interval_seconds)

        if delay_seconds is None:
            return "Error: Either delay_seconds, recur_interval_seconds, or absolute_time must be provided."

        if recur_interval_seconds:
            end_time = (now + max_duration_seconds) if max_duration_seconds else None
            max_runs = int(round(max_duration_seconds / recur_interval_seconds)) if max_duration_seconds else None
        else:
            end_time = now + delay_seconds
            max_runs = 1

        task_id = f"sched-{next(self._task_counter)}"
        task = {
            'id': task_id,
            'next_time': now + delay_seconds,
            'end_time': end_time,
            'interval': recur_interval_seconds,
            'run_count': 0,
            'max_runs': max_runs,
            'context_note': context_note,
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'targets': notify_targets or 'requester',
        }
        with self._scheduled_tasks_lock:
            # Enforce 50 tasks per user limit
            user_tasks = [t for t in self.scheduled_tasks if t.get('from_node') == from_node]
            cond_watchers = [w for w in self.condition_watchers if w.get('from_node') == from_node]
            online_watchers = [w for w in self.node_online_watchers if w.get('from_node') == from_node]
            total_current = len(user_tasks) + len(cond_watchers) + len(online_watchers)
            
            if total_current >= config.MAX_PROACTIVE_TASKS_PER_USER:
                return f"⚠️ Limit reached: You can only have {config.MAX_PROACTIVE_TASKS_PER_USER} active proactive tasks. Please cancel an existing task first."

            self.scheduled_tasks.append(task)
            self._save_proactive_tasks()
        
        self._index_task_registration(task_id, 'ScheduledTask', from_node, context_note, notify_targets)

        if recur_interval_seconds:
            duration_desc = f"for the next {max_duration_seconds}s" if max_duration_seconds else "until cancelled"
            return (f"✅ [{task_id}] Recurring reminder registered! I will send a message every {recur_interval_seconds}s "
                    f"{duration_desc} about: {context_note}")
        else:
            time_desc = f"at {absolute_time}" if absolute_time else f"in {int(delay_seconds)}s"
            return f"✅ [{task_id}] Reminder scheduled {time_desc} about: {context_note}"

    def _send_message_mcp(self, target, message):
        """Tool handler: send a one-off message to a specific node or channel."""
        logger = get_logger()
        logger.info(f"📤 Tool request: send_message to {target}: {message}")
        
        _to = '^all'
        _ch = 0
        
        # 1. Resolve Target
        if target.startswith('ch:'):
            try:
                _ch = int(target[3:])
                if hasattr(self, 'is_channel_allowed'):
                    if not self.is_channel_allowed(_ch):
                        return f"Error: Channel {_ch} is not in the allowed channels list."
                else:
                    allowed_channels = self.config.get('meshcore_allowed_channels') or self.config.get('allowed_channels', [])
                    if _ch not in allowed_channels:
                        return f"Error: Channel {_ch} is not in the allowed_channels list."
            except ValueError:
                return f"Error: Invalid channel format '{target}'. Use 'ch:N'."
        else:
            # Node resolution
            node_id = target
            if not node_id.startswith('!'):
                found_id = self.meshtastic.find_node_by_name(target)
                if found_id:
                    node_id = found_id
                else:
                    return f"Error: Node '{target}' not found on the mesh."
            _to = node_id
            
        # 2. Enqueue Message
        self.meshtastic.send_message(message, _to, _ch, "")
        
        return f"✅ Message queued for {target}."

    def _watch_condition_mcp(self, node_id_or_name, metric, operator, threshold, context_note, notify_targets=None, is_persistent=False, from_node=None, to_node=None, channel=0):
        """Tool handler: add a telemetry condition watcher."""
        logger = get_logger()
        config = get_config_module()

        # DM-only enforcement
        if to_node == '^all':
            return "⚠️ Proactive alerts can only be registered from a Direct Message to avoid spamming public channels."

        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found."
                
        node_id = node_id.lower()

        task_id = f"cond-{next(self._task_counter)}"
        watcher = {
            'id': task_id,
            'node_id': node_id,
            'metric': metric,
            'operator': operator,
            'threshold': threshold,
            'context_note': context_note,
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'targets': notify_targets or 'requester',
            'is_persistent': is_persistent,
        }
        with self._condition_watchers_lock:
            # Enforce limit
            scheduled = [t for t in self.scheduled_tasks if t.get('from_node') == from_node]
            cond_watchers = [w for w in self.condition_watchers if w.get('from_node') == from_node]
            online_watchers = [w for w in self.node_online_watchers if w.get('from_node') == from_node]
            total_current = len(scheduled) + len(cond_watchers) + len(online_watchers)
            
            if total_current >= config.MAX_PROACTIVE_TASKS_PER_USER:
                return f"⚠️ Limit reached: You can only have {config.MAX_PROACTIVE_TASKS_PER_USER} active proactive tasks."

            self.condition_watchers.append(watcher)
            self._save_proactive_tasks()
        
        cond_note = f"Alert when {node_id} {metric}{operator}{threshold}: {context_note}" if context_note else f"Alert when {node_id} {metric}{operator}{threshold}"
        self._index_task_registration(task_id, 'ConditionWatcher', from_node, cond_note, node_id)

        logger.info(f"👁️ Condition watcher [{task_id}] registered: {node_id} {metric}{operator}{threshold}")
        return f"✅ [{task_id}] Watching {node_id_or_name}: will alert when {metric} {operator} {threshold}"

    def _watch_node_online_mcp(self, node_id_or_name, context_note, notify_targets=None, is_persistent=False, from_node=None, to_node=None, channel=0):
        """Tool handler: add a node-online watcher."""
        logger = get_logger()
        config = get_config_module()

        # DM-only enforcement
        if to_node == '^all':
            return "⚠️ Proactive alerts can only be registered from a Direct Message to avoid spamming public channels."

        node_id = node_id_or_name
        if not node_id.startswith('!'):
            found_id = self.meshtastic.find_node_by_name(node_id_or_name)
            if found_id:
                node_id = found_id
            else:
                return f"Error: Node '{node_id_or_name}' not found. Make sure I've seen it at least once before."
                
        node_id = node_id.lower()

        task_id = f"node-{next(self._task_counter)}"
        watcher = {
            'id': task_id,
            'node_id': node_id,
            'context_note': context_note,
            'from_node': from_node,
            'to_node': to_node,
            'channel': channel,
            'targets': notify_targets or 'requester',
            'is_persistent': is_persistent,
        }
        with self._node_online_watchers_lock:
            # Enforce limit
            scheduled = [t for t in self.scheduled_tasks if t.get('from_node') == from_node]
            cond_watchers = [w for w in self.condition_watchers if w.get('from_node') == from_node]
            online_watchers = [w for w in self.node_online_watchers if w.get('from_node') == from_node]
            total_current = len(scheduled) + len(cond_watchers) + len(online_watchers)
            
            if total_current >= config.MAX_PROACTIVE_TASKS_PER_USER:
                return f"⚠️ Limit reached: You can only have {config.MAX_PROACTIVE_TASKS_PER_USER} active proactive tasks."

            self.node_online_watchers.append(watcher)
            self._save_proactive_tasks()
        
        online_note = f"Alert when {node_id_or_name} comes online: {context_note}" if context_note else f"Alert when {node_id_or_name} comes online"
        self._index_task_registration(task_id, 'NodeOnlineWatcher', from_node, online_note, node_id)

        logger.info(f"👀 Node-online watcher [{task_id}] registered for {node_id}")
        return f"✅ [{task_id}] Watching for {node_id_or_name}: I'll alert you when it's heard on the mesh"

    def _list_proactive_tasks_mcp(self, from_node=None):
        """Tool handler: list all active proactive tasks for the current user."""
        time = get_time()
        caller = from_node
        lines = []
        now = time.time()

        with self._scheduled_tasks_lock:
            for t in self.scheduled_tasks:
                if t.get('from_node') != caller:
                    continue
                remaining = max(0, int(t['next_time'] - now))
                recur = f" (every {t['interval']}s)" if t.get('interval') else ""
                lines.append(f"[{t['id']}] ⏰ Fires in {remaining}s{recur}: {t['context_note']} → {t['targets']}")

        with self._condition_watchers_lock:
            for w in self.condition_watchers:
                if w.get('from_node') != caller:
                    continue
                lines.append(f"[{w['id']}] 👁 {w['node_id']} {w['metric']}{w['operator']}{w['threshold']} → {w['targets']}")

        with self._node_online_watchers_lock:
            for w in self.node_online_watchers:
                if w.get('from_node') != caller:
                    continue
                lines.append(f"[{w['id']}] 🟢 Waiting for {w['node_id']} → {w['targets']}")

        if not lines:
            return "📋 You have no active proactive tasks."
        return "📋 Your active tasks:\n" + "\n".join(lines)

    def _cancel_proactive_task_mcp(self, task_id, from_node=None):
        """Tool handler: cancel/delete a proactive task by ID, or 'all' to delete everything."""
        caller = from_node
        cancelled = []
        raw_id = str(task_id).strip()
        clean_id = raw_id.strip('[]').lower()
        cancel_all = clean_id in ('all', 'everything', '*', 'all tasks')

        with self._scheduled_tasks_lock:
            keep, remove = [], []
            for t in self.scheduled_tasks:
                tid = str(t.get('id', '')).lower()
                if (t.get('from_node') == caller or not caller) and (cancel_all or tid == clean_id):
                    remove.append(t.get('id'))
                else:
                    keep.append(t)
            self.scheduled_tasks = keep
            cancelled.extend(remove)

        with self._condition_watchers_lock:
            keep, remove = [], []
            for w in self.condition_watchers:
                wid = str(w.get('id', '')).lower()
                if (w.get('from_node') == caller or not caller) and (cancel_all or wid == clean_id):
                    remove.append(w.get('id'))
                else:
                    keep.append(w)
            self.condition_watchers = keep
            cancelled.extend(remove)

        with self._node_online_watchers_lock:
            keep, remove = [], []
            for w in self.node_online_watchers:
                nid = str(w.get('id', '')).lower()
                if (w.get('from_node') == caller or not caller) and (cancel_all or nid == clean_id):
                    remove.append(w.get('id'))
                else:
                    keep.append(w)
            self.node_online_watchers = keep
            cancelled.extend(remove)

        if cancelled:
            self._save_proactive_tasks()
            for cid in cancelled:
                self._index_task_cancellation(cid)
            ids = ', '.join(f'[{c}]' for c in cancelled)
            return f"✅ Cancelled and removed: {ids}"
        return f"❌ Task [{task_id}] not found or already completed/removed."

    def _save_proactive_tasks(self):
        """Save all proactive tasks to a JSON file atomically."""
        logger = get_logger()
        config = get_config_module()
        with self._proactive_persistence_lock:
            try:
                tasks_data = {
                    'scheduled_tasks': list(self.scheduled_tasks),
                    'condition_watchers': list(self.condition_watchers),
                    'node_online_watchers': list(self.node_online_watchers)
                }
                file_path = os.path.join(self.history_dir, config.PROACTIVE_TASKS_FILE)
                tmp_path = f"{file_path}.tmp"
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(tasks_data, f, indent=2)
                os.replace(tmp_path, file_path)
                logger.debug(f"💾 Saved proactive tasks to {file_path}")
            except Exception as e:
                logger.error(f"Failed to save proactive tasks: {e}")

    def _load_proactive_tasks(self):
        """Load proactive tasks from disk and restore ID counter."""
        logger = get_logger()
        config = get_config_module()
        file_path = os.path.join(self.history_dir, config.PROACTIVE_TASKS_FILE)
        if not os.path.exists(file_path):
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tasks_data = json.load(f)
            
            self.scheduled_tasks = tasks_data.get('scheduled_tasks', [])
            self.condition_watchers = tasks_data.get('condition_watchers', [])
            self.node_online_watchers = tasks_data.get('node_online_watchers', [])
            
            # Restore ID counter to skip used IDs
            all_tasks = self.scheduled_tasks + self.condition_watchers + self.node_online_watchers
            max_id = 0
            for t in all_tasks:
                try:
                    # Parse ID like "sched-5" or "cond-12"
                    id_num = int(t['id'].split('-')[1])
                    max_id = max(max_id, id_num)
                except (ValueError, IndexError, KeyError):
                    continue
            
            self._task_counter = itertools.count(max_id + 1)
            logger.info(f"📂 Loaded {len(all_tasks)} proactive tasks (Next ID: {max_id + 1})")
        except Exception as e:
            logger.error(f"Failed to load proactive tasks: {e}")

    def _fire_system_trigger(self, context_note, from_node, to_node, channel, targets='requester', disable_tools=False, run_count=None, max_runs=None):
        """Fire a proactive system-triggered AI response to one or more targets."""
        logger = get_logger()
        exec_info = ""
        if run_count is not None:
            exec_info = f" (Execution {run_count} of {max_runs})" if max_runs else f" (Execution #{run_count})"

        prompt = (
            f"[SYSTEM WAKEUP] A proactive task has triggered{exec_info}.\n\n"
            f"COMMAND/CONTEXT: {context_note}\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. You MUST execute any instructions or checks contained in the COMMAND/CONTEXT above.\n"
            f"2. SEMANTIC CONTINUITY: Check your Knowledge Graph (memory_query_facts or memory_search on 'Hub_System_{from_node}') for previous 'System Task:' observations related to this task. This allows you to track state, counts, or trends across recurring events.\n"
            f"3. Use appropriate tools (telemetry, location, etc.) to fetch fresh data if the command requires it.\n"
            f"4. Your natural text response will be delivered automatically to: {targets}.\n"
            f"5. Do NOT use 'send_message' to deliver the final report to {targets} (your text response handles this). Only use 'send_message' if COMMAND/CONTEXT explicitly instructs you to send a message to a DIFFERENT node or channel than '{targets}'."
        )
        logger.info(f"🔔 Firing system trigger for {from_node}: {context_note} -> targets={targets}{exec_info}")

        target_list = [t.strip() for t in targets.split(',') if t.strip()]
        allowed_channels = self.config.get('allowed_channels', [])

        for target in target_list:
            if target == 'requester':
                _to = from_node
                _ch = channel
            elif target.startswith('ch:'):
                try:
                    ch_idx = int(target[3:])
                except ValueError:
                    logger.warning(f"Invalid channel target: {target}")
                    continue
                if ch_idx not in allowed_channels:
                    logger.warning(f"Channel {ch_idx} not in allowed_channels — skipping.")
                    continue
                _to = '^all'
                _ch = ch_idx
            elif target.startswith('!'):
                _to = target
                _ch = 0
            else:
                # Resolve name to ID
                found_id = self.meshtastic.find_node_by_name(target)
                if found_id:
                    _to = found_id
                    _ch = 0
                else:
                    logger.warning(f"Unknown target (not a node name or ID): {target}")
                    continue

            t = threading.Thread(
                target=self._process_ai_query_thread,
                args=(prompt, _to, _to, _ch),
                kwargs={'is_dm': (_to != '^all'), 'is_system_trigger': True, 'disable_tools': disable_tools},
                daemon=True
            )
            t.start()

    def _on_telemetry_proactive(self, packet, interface):
        """Callback fired on every incoming telemetry packet. Evaluates deferred requests and condition watchers."""
        logger = get_logger()
        time = get_time()
        try:
            from_id_raw = packet.get('fromId')
            if isinstance(from_id_raw, int):
                from_id = f"!{from_id_raw:08x}"
            else:
                from_id = str(from_id_raw).lower() if from_id_raw else None
            
            if not from_id:
                return

            now = time.time()
            # --- 1. Deferred telemetry callbacks ---
            if from_id in self.pending_telemetry_requests:
                req = self.pending_telemetry_requests[from_id]
                if not req.get('polling', False):
                    event_str = f"Requested {req.get('context_note', 'telemetry')} arrived."
                    self._add_to_collector(from_id, event_str, req)
                    logger.info(f"⏳ Telemetry for {from_id} buffered in collector.")

            # --- 2. Condition watchers ---
            decoded = packet.get('decoded', {})
            telemetry = decoded.get('telemetry', {})
            
            if not telemetry:
                return

            metric_values = {}
            category_map = {
                'device_metrics': 'deviceMetrics',
                'environment_metrics': 'environmentMetrics',
                'power_metrics': 'powerMetrics',
                'health_metrics': 'healthMetrics',
                'air_quality_metrics': 'airQualityMetrics',
                'local_stats': 'localStats',
                'host_metrics': 'hostMetrics'
            }

            for snake_cat, camel_cat in category_map.items():
                cat_data = telemetry.get(snake_cat) or telemetry.get(camel_cat)
                if isinstance(cat_data, dict):
                    for k, v in cat_data.items():
                        snake_key = re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower()
                        metric_values[snake_key] = v
            
            if 'rxSnr' in packet:
                metric_values['snr'] = packet['rxSnr']

            # --- 3. Background Semantic Indexing ---
            self._index_to_mcp('telemetry', {
                'node_id': from_id,
                'type': 'status_snapshot',
                'data': metric_values
            })

            # Build set of matching ID representations
            raw_prefix = str(packet.get('raw_pubkey_prefix', '')).lower()
            alt_ids = {from_id, from_id.lstrip('!')}
            if raw_prefix:
                alt_ids.add(raw_prefix)
                alt_ids.add(f"!{raw_prefix}")

            radio = getattr(self, 'meshtastic', None)
            if radio and hasattr(radio, 'find_node_by_name'):
                try:
                    resolved = radio.find_node_by_name(from_id)
                    if resolved and isinstance(resolved, str):
                        alt_ids.add(resolved.lower())
                        alt_ids.add(resolved.lower().lstrip('!'))
                except Exception:
                    pass

            with self._condition_watchers_lock:
                triggered = []
                for w in self.condition_watchers:
                    wid = str(w['node_id']).lower()
                    wid_clean = wid.lstrip('!')
                    if wid not in alt_ids and wid_clean not in alt_ids:
                        continue
                    
                    if now - w.get('last_fired_at', 0) < 60:
                        continue
                    
                    val = metric_values.get(w['metric'])
                    if val is None:
                        continue
                        
                    op = w['operator']
                    thr = w['threshold']
                    
                    condition_met = (
                        (op == '<'  and val < thr)  or
                        (op == '>'  and val > thr)  or
                        (op == '<=' and val <= thr) or
                        (op == '>=' and val >= thr) or
                        (op == '==' and val == thr)
                    )
                    
                    if condition_met:
                        w['last_fired_at'] = now
                        triggered.append(w)
                
                for w in triggered:
                    if not w.get('is_persistent', False):
                        self.condition_watchers.remove(w)
                if triggered:
                    self._save_proactive_tasks()
                    for w in triggered:
                        event_str = (
                            f"Condition alert: {w['context_note']}. "
                            f"Reading: {w['metric']}={metric_values.get(w['metric'])} "
                            f"(threshold: {w['operator']} {w['threshold']})."
                        )
                        self._add_to_collector(from_id, event_str, w)

        except Exception as e:
            logger.warning(f"Error in proactive telemetry handler: {e}")

    def _add_to_collector(self, node_id, event_text, context_dict):
        """Add an event to the proactive collector and ensure a timer is running."""
        node_id = node_id.lower()
        with self._collector_lock:
            if node_id not in self._proactive_event_collector:
                self._proactive_event_collector[node_id] = {
                    'events': [],
                    'from_node': context_dict.get('from_node'),
                    'to_node': context_dict.get('to_node'),
                    'channel': context_dict.get('channel'),
                    'targets': context_dict.get('targets', 'requester'),
                    'timer': None
                }
            
            entry = self._proactive_event_collector[node_id]
            if event_text not in entry['events']:
                entry['events'].append(event_text)
            
            if entry['timer']:
                entry['timer'].cancel()
            
            entry['timer'] = threading.Timer(3.0, self._dispatch_collector, args=[node_id])
            entry['timer'].daemon = True
            entry['timer'].start()

    def _dispatch_collector(self, node_id):
        """Aggregate all events for a node and fire a single AI trigger."""
        logger = get_logger()
        node_id = node_id.lower()
        entry = None
        with self._collector_lock:
            entry = self._proactive_event_collector.pop(node_id, None)
        
        if not entry or not entry['events']:
            return

        events_summary = "\n".join([f"- {e}" for e in entry['events']])
        metadata = self.meshtastic.get_node_metadata(node_id)
        
        context = (
            f"Proactive update for {node_id}:\n"
            f"{events_summary}\n\n"
            f"Current Node Metadata:\n{metadata}"
        )
        
        if node_id in self.pending_telemetry_requests:
            self.pending_telemetry_requests.pop(node_id)
            
        self._fire_system_trigger(
            context, 
            entry['from_node'], 
            entry['to_node'], 
            entry['channel'], 
            targets=entry['targets'], 
            disable_tools=False
        )
        logger.info(f"📣 Dispatched aggregated proactive Turn for {node_id} ({len(entry['events'])} events).")
