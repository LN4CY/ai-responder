# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Background Semantic Memory / Knowledge Graph indexing mixin."""

import os
import re
from core._compat import get_time, get_datetime, get_config_module, get_logger


class IndexingMixin:
    """Mixin for asynchronous Knowledge Graph indexing of conversations, telemetry, and tasks."""

    def _index_to_mcp(self, data_type, payload):
        """Dispatch indexing task to the background executor."""
        # Only proceed if Semantic Memory MCP server is active
        if not self.mcp_client or not self.mcp_client.has_server('memory'):
            return
        
        self._bg_executor.submit(self._index_task_wrapper, data_type, payload)

    def _index_task_wrapper(self, data_type, payload):
        """Internal wrapper for the background executor to handle Knowledge Graph logic."""
        logger = get_logger()
        # 1. Graceful Fallback check
        if not self.mcp_client.has_server('memory'):
            return

        try:
            if data_type == 'conversation':
                self._bg_index_conversation(payload)
            elif data_type == 'telemetry':
                self._bg_index_telemetry(payload)
            elif data_type == 'delete_history':
                self._bg_delete_semantic_history(payload)
        except Exception as e:
            logger.warning(f"⚠️ Background indexing failed ({data_type}): {e}")

    def _get_semantic_hub_name(self, node_id, channel=0, is_dm=False, is_system=False):
        """Standardized naming logic for Semantic Hubs."""
        if is_system:
            return f"Hub_System_{node_id}"
        if is_dm:
            return f"Hub_Chat_{node_id}"
        else:
            return f"Hub_CH{channel}"

    def _bg_delete_semantic_history(self, payload):
        """Perform a Nuclear Wipe of semantic memory for a context."""
        logger = get_logger()
        node_id = payload.get('node_id')
        topic = payload.get('topic')
        channel = payload.get('channel', 0)
        
        if not node_id:
            return
        
        targets = []
        if topic == 'all':
            # 1. Target the persistent conversation hubs
            targets.append(self._get_semantic_hub_name(node_id, channel, is_dm=True))
            targets.append(self._get_semantic_hub_name(node_id, channel, is_dm=False))
            targets.append(self._get_semantic_hub_name(node_id, channel, is_system=True))
            targets.append(f"Chat_{node_id}_CH{channel}")
        elif topic:
            targets.append(topic)
        
        if targets:
            logger.info(f"🗑️ Semantically pruning and applying deletion tombstone to hubs: {targets}")
            for target in targets:
                self.mcp_client.call_tool("memory_delete_hub", {"target": target})
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": target,
                    "predicate": "has_status",
                    "object": "Archived_by_User_Wipe"
                })

    def _bg_index_conversation(self, payload):
        """Index a conversation turn into the Knowledge Graph."""
        logger = get_logger()
        node_id = payload.get('node_id')
        channel = payload.get('channel', 0)
        prompt = payload.get('prompt')
        response = payload.get('response')
        is_dm = payload.get('is_dm', True)
        is_system = payload.get('is_system', False)
        
        if not node_id or not prompt or not response:
            return
        if not isinstance(prompt, str) or not isinstance(response, str):
            return
        
        # 1. Active Session vs Special-Purpose Hubs
        session_name = self.session_manager.get_session_name(node_id) if is_dm else None
        if not isinstance(session_name, str):
            session_name = None
        
        hub_name = self._get_semantic_hub_name(node_id, channel, is_dm=is_dm, is_system=is_system)
        if is_system:
            hub_type = "SystemHub"
        elif not session_name:
            hub_type = "ChatHub"
        else:
            hub_name = f"Chat_{node_id}_CH{channel}"
            hub_type = "SessionHub"

        # 2. Define the Hub
        self.mcp_client.call_tool("memory_add_fact", {
            "subject": hub_name,
            "predicate": "is_a",
            "object": hub_type
        })
        self.mcp_client.call_tool("memory_add_fact", {
            "subject": node_id,
            "predicate": "participated_in",
            "object": hub_name
        })

        # Base station / host node is also a participant in the conversation
        my_node_id = None
        if hasattr(self.meshtastic, 'get_node_info'):
            my_info = self.meshtastic.get_node_info() or {}
            my_node_id = my_info.get('user', {}).get('id')

        if my_node_id and str(my_node_id).startswith('!') and str(my_node_id).lower() != str(node_id).lower():
            self.mcp_client.call_tool("memory_add_fact", {
                "subject": my_node_id,
                "predicate": "participated_in",
                "object": hub_name
            })
            self.mcp_client.call_tool("memory_add_fact", {
                "subject": node_id,
                "predicate": "interacts_with",
                "object": my_node_id
            })
            self.mcp_client.call_tool("memory_add_fact", {
                "subject": my_node_id,
                "predicate": "interacts_with",
                "object": node_id
            })
            self.mcp_client.call_tool("memory_add_fact", {
                "subject": my_node_id,
                "predicate": "is_a",
                "object": "MeshNode"
            })
            u = my_info.get('user', {})
            if u.get('longName'):
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": my_node_id,
                    "predicate": "has_name",
                    "object": u.get('longName')
                })
            if u.get('shortName'):
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": my_node_id,
                    "predicate": "has_short_name",
                    "object": u.get('shortName')
                })
        
        # 3. Relate Topic if in session
        if session_name:
            self.mcp_client.call_tool("memory_add_fact", {
                "subject": hub_name,
                "predicate": "discusses_topic",
                "object": session_name
            })
            
        # 4. Add Activity Observation
        if is_system:
            trigger_context = prompt.split("COMMAND/CONTEXT:")[1].split("CRITICAL INSTRUCTIONS:")[0].strip() if "COMMAND/CONTEXT:" in prompt else prompt
            observation = f"System Task: {trigger_context} | Result: {response}"
        else:
            observation = f"User: {prompt} | AI: {response}"
            
        self.mcp_client.call_tool("memory_add_fact", {
            "subject": hub_name,
            "predicate": "recorded_turn",
            "object": observation
        })

        # Ensure node identity and type are indexed in Knowledge Graph
        if hasattr(self.meshtastic, '_get_node_by_id'):
            node_info = self.meshtastic._get_node_by_id(node_id)
            if node_info:
                user = node_info.get('user', {})
                if user.get('longName'):
                    self.mcp_client.call_tool("memory_add_fact", {
                        "subject": node_id,
                        "predicate": "has_name",
                        "object": user.get('longName')
                    })
                if user.get('shortName'):
                    self.mcp_client.call_tool("memory_add_fact", {
                        "subject": node_id,
                        "predicate": "has_short_name",
                        "object": user.get('shortName')
                    })
        self.mcp_client.call_tool("memory_add_fact", {
            "subject": node_id,
            "predicate": "is_a",
            "object": "MeshNode"
        })

        # Link task execution if this is a system proactive turn
        if is_system:
            t_match = re.search(r"\[?(sched-\d+|cond-\d+|node-\d+)\]?", prompt, re.IGNORECASE)
            if t_match:
                t_id = t_match.group(1).lower()
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": t_id,
                    "predicate": "executed_in",
                    "object": hub_name
                })

        # Inter-node relationships mentioned in turn
        combined_text = f"{prompt} {response}"
        for token in set(re.findall(r"(![a-zA-Z0-9]{8}|[a-zA-Z0-9_\-]+)", combined_text)):
            ref_id = None
            if token.startswith('!') and len(token) == 9 and token.lower() != node_id.lower():
                ref_id = token.lower()
            elif hasattr(self.meshtastic, 'find_node_by_name'):
                found = self.meshtastic.find_node_by_name(token)
                if found and found.lower() != node_id.lower():
                    ref_id = found.lower()

            if ref_id:
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": hub_name,
                    "predicate": "references_node",
                    "object": ref_id
                })
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": node_id,
                    "predicate": "interacts_with",
                    "object": ref_id
                })
                if hasattr(self.meshtastic, '_get_node_by_id'):
                    ref_info = self.meshtastic._get_node_by_id(ref_id)
                    if ref_info:
                        ru = ref_info.get('user', {})
                        if ru.get('shortName'):
                            self.mcp_client.call_tool("memory_add_fact", {"subject": ref_id, "predicate": "has_short_name", "object": ru.get('shortName')})
                        if ru.get('longName'):
                            self.mcp_client.call_tool("memory_add_fact", {"subject": ref_id, "predicate": "has_name", "object": ru.get('longName')})
                        self.mcp_client.call_tool("memory_add_fact", {"subject": ref_id, "predicate": "is_a", "object": "MeshNode"})
        
        logger.info(f"🧠 Semantically indexed conversation turn for {node_id} -> {hub_name}")

    def _bg_index_telemetry(self, payload):
        """Index telemetry status into the Node's hub."""
        logger = get_logger()
        time = get_time()
        datetime = get_datetime()
        node_id = payload.get('node_id')
        t_type = payload.get('type')
        data = payload.get('data')
        
        if not node_id or not data:
            return
        
        # Throttle: Only index telemetry every 15 minutes per node to avoid bloat
        now = time.time()
        cache_key = f"{node_id}_{t_type}"
        last_time = self._mcp_indexing_cache.get(cache_key, 0)
        if now - last_time < 900: # 15 minutes
            return
            
        self._mcp_indexing_cache[cache_key] = now
        
        ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        if isinstance(data, dict):
            summary = ", ".join([f"{k}: {v}" for k, v in data.items() if v is not None])
        else:
            summary = str(data)
            
        self.mcp_client.call_tool("memory_add_fact", {
            "subject": node_id,
            "predicate": "is_a",
            "object": "MeshNode"
        })
        
        # Build the semantic graph mapping of the node's name
        if hasattr(self.meshtastic, '_get_node_by_id'):
            node_info = self.meshtastic._get_node_by_id(node_id)
            if node_info:
                user = node_info.get('user', {})
                if user.get('longName'):
                    self.mcp_client.call_tool("memory_add_fact", {
                        "subject": node_id,
                        "predicate": "has_name",
                        "object": user.get('longName')
                    })
                if user.get('shortName'):
                    self.mcp_client.call_tool("memory_add_fact", {
                        "subject": node_id,
                        "predicate": "has_short_name",
                        "object": user.get('shortName')
                    })
        
        self.mcp_client.call_tool("memory_add_fact", {
            "subject": node_id,
            "predicate": "reported_telemetry",
            "object": f"[{ts}] Type {t_type}: {summary}"
        })
        logger.info(f"🧠 Semantically indexed telemetry for {node_id}")

    def _index_task_registration(self, task_id, task_type, from_node, context_note, targets=None):
        """Record a newly created proactive task as explicit relational triples in the Knowledge Graph."""
        logger = get_logger()
        if not self.mcp_client or not self.mcp_client.has_server('memory'):
            return
        try:
            tid_clean = str(task_id).strip('[]').lower()
            self.mcp_client.call_tool("memory_add_fact", {
                "subject": tid_clean,
                "predicate": "is_a",
                "object": task_type
            })
            if from_node:
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": tid_clean,
                    "predicate": "scheduled_by",
                    "object": from_node
                })
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": from_node,
                    "predicate": "scheduled_task",
                    "object": tid_clean
                })
            if context_note:
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": tid_clean,
                    "predicate": "has_description",
                    "object": str(context_note)[:250]
                })

            # Target node resolution
            target_node_id = None
            raw_tgt = targets[0] if isinstance(targets, list) and targets else targets
            if raw_tgt and raw_tgt != 'requester':
                if str(raw_tgt).startswith('!'):
                    target_node_id = str(raw_tgt).lower()
                elif hasattr(self.meshtastic, 'find_node_by_name'):
                    target_node_id = self.meshtastic.find_node_by_name(raw_tgt)

            # Scan context note for destination node
            if not target_node_id and context_note:
                for token in re.findall(r"(![a-zA-Z0-9]{8}|[a-zA-Z0-9_\-]+)", str(context_note)):
                    if token.startswith('!') and len(token) == 9 and token.lower() != str(from_node).lower():
                        target_node_id = token.lower()
                        break
                    elif hasattr(self.meshtastic, 'find_node_by_name'):
                        found = self.meshtastic.find_node_by_name(token)
                        if found and str(found).lower() != str(from_node).lower():
                            target_node_id = str(found).lower()
                            break

            if target_node_id:
                self.mcp_client.call_tool("memory_add_fact", {
                    "subject": tid_clean,
                    "predicate": "targets_node",
                    "object": target_node_id
                })
                if from_node:
                    self.mcp_client.call_tool("memory_add_fact", {
                        "subject": from_node,
                        "predicate": "interacts_with",
                        "object": target_node_id
                    })
        except Exception as e:
            logger.warning(f"⚠️ Failed to index task registration {task_id}: {e}")

    def _index_task_cancellation(self, task_id):
        """Mark task as cancelled in the Knowledge Graph."""
        logger = get_logger()
        if not self.mcp_client or not self.mcp_client.has_server('memory'):
            return
        try:
            tid_clean = str(task_id).strip('[]').lower()
            self.mcp_client.call_tool("memory_add_fact", {
                "subject": tid_clean,
                "predicate": "has_status",
                "object": "cancelled"
            })
        except Exception as e:
            logger.warning(f"⚠️ Failed to index task cancellation {task_id}: {e}")

    def _bg_sync_graph_triples(self):
        """Background sync: index all known mesh nodes and backfill relational triples in the Knowledge Graph."""
        logger = get_logger()
        config = get_config_module()
        if not self.mcp_client or not self.mcp_client.has_server('memory'):
            return

        if getattr(self, '_syncing_graph', False):
            return
        self._syncing_graph = True

        try:
            logger.info("🔄 Running semantic graph sync...")
            # 1. Sync all mesh nodes with names and callsigns
            if hasattr(self.meshtastic, 'get_all_nodes'):
                all_nodes = self.meshtastic.get_all_nodes() or []
                for n in all_nodes:
                    nid = n.get('id')
                    if not nid or not str(nid).startswith('!'):
                        continue
                    self.mcp_client.call_tool("memory_add_fact", {
                        "subject": nid,
                        "predicate": "is_a",
                        "object": "MeshNode"
                    })
                    if n.get('longName'):
                        self.mcp_client.call_tool("memory_add_fact", {
                            "subject": nid,
                            "predicate": "has_name",
                            "object": str(n.get('longName'))
                        })
                    if n.get('shortName'):
                        self.mcp_client.call_tool("memory_add_fact", {
                            "subject": nid,
                            "predicate": "has_short_name",
                            "object": str(n.get('shortName'))
                        })

            # 2. Sync active proactive tasks
            with self._scheduled_tasks_lock:
                for t in self.scheduled_tasks:
                    self._index_task_registration(t.get('id'), 'ScheduledTask', t.get('from_node'), t.get('context_note'), t.get('targets'))
            with self._condition_watchers_lock:
                for w in self.condition_watchers:
                    note = f"Alert when {w.get('metric')} {w.get('operator')} {w.get('threshold')}"
                    self._index_task_registration(w.get('id'), 'ConditionWatcher', w.get('from_node'), note, w.get('node_id'))
            with self._node_online_watchers_lock:
                for w in self.node_online_watchers:
                    note = f"Alert when {w.get('node_id')} comes online"
                    self._index_task_registration(w.get('id'), 'NodeOnlineWatcher', w.get('from_node'), note, w.get('node_id'))

            # 3. Backfill missing relational triples from recorded turns
            db_path = getattr(config, "MEMORY_DB_PATH", "/app/data/memory.db")
            if os.path.exists(db_path):
                import sqlite3
                conn = sqlite3.connect(db_path, timeout=10.0)
                cur = conn.cursor()

                alias_to_node = {}
                cur.execute("SELECT subject, object FROM kg_facts WHERE predicate IN ('has_name', 'has_short_name')")
                for s, o in cur.fetchall():
                    if str(s).startswith("!"):
                        alias_to_node[str(o).lower()] = str(s)
                        alias_to_node[str(s).lower()] = str(s)

                cur.execute("SELECT subject, object, valid_from FROM kg_facts WHERE predicate = 'recorded_turn'")
                turns = cur.fetchall()
                conn.close()

                for s, o, v in turns:
                    text = str(o)
                    task_ids = set(re.findall(r"\[?(sched-\d+|cond-\d+|node-\d+)\]?", text, re.IGNORECASE))
                    requester = s.replace("Hub_Chat_", "").replace("Hub_System_", "") if s.startswith("Hub_") else None

                    # Base station participation and interaction
                    my_node_id = None
                    if hasattr(self.meshtastic, 'get_node_info'):
                        my_info = self.meshtastic.get_node_info() or {}
                        my_node_id = my_info.get('user', {}).get('id')
                    if my_node_id and str(my_node_id).startswith('!') and (s.startswith("Hub_Chat_") or s.startswith("Hub_System_")):
                        self.mcp_client.call_tool("memory_add_fact", {"subject": my_node_id, "predicate": "participated_in", "object": s})
                        if requester and requester.startswith("!") and requester != my_node_id:
                            self.mcp_client.call_tool("memory_add_fact", {"subject": requester, "predicate": "interacts_with", "object": my_node_id})
                            self.mcp_client.call_tool("memory_add_fact", {"subject": my_node_id, "predicate": "interacts_with", "object": requester})

                    # Extract target nodes
                    targets = set()
                    for token in re.findall(r"(![a-zA-Z0-9]{8}|[a-zA-Z0-9_\-]+)", text):
                        t_low = token.lower()
                        if t_low in alias_to_node:
                            t_nid = alias_to_node[t_low]
                            if t_nid != requester:
                                targets.add(t_nid)

                    for tid in task_ids:
                        tid_lower = tid.lower()
                        self.mcp_client.call_tool("memory_add_fact", {"subject": tid_lower, "predicate": "is_a", "object": "ScheduledTask"})
                        if requester and requester.startswith("!"):
                            self.mcp_client.call_tool("memory_add_fact", {"subject": tid_lower, "predicate": "scheduled_by", "object": requester})
                            self.mcp_client.call_tool("memory_add_fact", {"subject": requester, "predicate": "scheduled_task", "object": tid_lower})
                        for tgt in targets:
                            self.mcp_client.call_tool("memory_add_fact", {"subject": tid_lower, "predicate": "targets_node", "object": tgt})
                            if requester and requester.startswith("!"):
                                self.mcp_client.call_tool("memory_add_fact", {"subject": requester, "predicate": "interacts_with", "object": tgt})
                        if s.startswith("Hub_System_"):
                            self.mcp_client.call_tool("memory_add_fact", {"subject": tid_lower, "predicate": "executed_in", "object": s})

                    for tgt in targets:
                        self.mcp_client.call_tool("memory_add_fact", {"subject": s, "predicate": "references_node", "object": tgt})
                        if requester and requester.startswith("!") and not task_ids:
                            self.mcp_client.call_tool("memory_add_fact", {"subject": requester, "predicate": "interacts_with", "object": tgt})

            logger.info("✅ Semantic graph sync completed successfully.")
        except Exception as e:
            logger.warning(f"⚠️ Semantic graph sync encountered error: {e}")
        finally:
            self._syncing_graph = False
