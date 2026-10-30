# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Command router and shared command handlers mixin for AI Responder."""

from core._compat import get_time, get_config_module, get_logger


class CommandsMixin:
    """Mixin for handling !ai commands, help, conversations, providers, and scheduler."""

    def process_command(self, text, from_node, to_node, channel):
        """
        Process AI commands and queries.
        
        This is the main command router that handles all !ai commands and
        delegates to appropriate handlers.
        
        Args:
            text: Command text
            from_node: Source node ID
            to_node: Destination node ID
            channel: Channel index
        """
        # Track node for telemetry logging of active users
        try:
            handler = getattr(self, 'meshtastic', None)
            if handler:
                handler.track_node(from_node)
        except Exception:
            pass
            
        # Extract command and arguments
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            return
        
        cmd = parts[1].lower() if len(parts) > 1 else ''
        args = parts[2] if len(parts) > 2 else ''
        
        is_dm = self._is_dm(to_node)
        is_admin = self.is_admin(from_node)
        
        # ===== Help Command =====
        if cmd == '-h':
            self._handle_help_command(from_node, to_node, channel, is_dm, is_admin)
            return
        
        # ===== Memory Status =====
        elif cmd == '-m':
            status = self.get_memory_status(from_node, channel, is_dm)
            self.send_response(status, from_node, to_node, channel, is_admin_cmd=False)
            return
        
        # ===== Session Commands (DM only) =====
        if cmd == '-n':
            # Check for explicit wipe command: !ai -n rm all
            if args.lower() == 'rm all':
                # 1. Clear local in-memory history buffer
                key = f"DM:{from_node}" if is_dm else f"Channel:{channel}:{from_node}"
                self.clear_history(key)
                
                # 2. Disk & Semantic Wipe
                if is_dm:
                    # Wipe all disk-based sessions for this user
                    self.conversation_manager.delete_all_conversations(from_node)
                    # Semantic: Wipe everything (Topic='all') for this node's hubs
                    self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': 'all', 'channel': 0})
                    self.send_response("☢️ Nuclear Wipe: All DM sessions and Graph memory forgotten.", from_node, to_node, channel)
                else:
                    # Wipe channel graph memory
                    self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': 'all', 'channel': channel})
                    self.send_response(f"☢️ Nuclear Wipe: All history for Channel {channel} index forgotten in Graph.", from_node, to_node, channel)
                return

            if is_dm:
                # 1. If in a session, end it first (don't clear its history)
                if self.session_manager.is_active(from_node):
                    self.session_manager.end_session(from_node)
                
                if args:
                    # 2. Start NEW named session (Pivot)
                    success, message, conv_name = self.session_manager.start_session(from_node, args, channel, to_node)
                    self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
                else:
                    # 3. No args: Safe Context Reset (Non-destructive)
                    dm_key = f"DM:{from_node}"
                    self.clear_history(dm_key)
                    self.send_response("✨ DM context reset to Default. (Memories safely archived in Graph)", from_node, to_node, channel, is_admin_cmd=False)
            else:
                # Channel mode: Safe Context Reset (Non-destructive)
                channel_key = f"Channel:{channel}:{from_node}"
                self.clear_history(channel_key)
                if args:
                    # Treat args as a fresh query after reset
                    self._handle_ai_query(args, from_node, to_node, channel, is_dm=False, initial_msg="Thinking (New Conversation)... 🤖")
                else:
                    self.send_response(f"✨ Channel {channel} window reset. (History archived in Graph)", from_node, to_node, channel, is_admin_cmd=False)
            return
        
        if cmd in ('-end', 'end', 'close', 'exit', 'quit'):
            if is_dm:
                success, message, _, _ = self.session_manager.end_session(from_node)
                self.send_response(message, from_node, to_node, channel, is_admin_cmd=False)
            else:
                self.send_response("⚠️ Sessions are DM-only. Use !ai -n in channels to clear history.", 
                                 from_node, to_node, channel, is_admin_cmd=False)
            return
        
        # ===== Conversation Management =====
        if cmd == '-c':
            self._handle_conversation_command(args, from_node, to_node, channel)
            return
        
        # ===== Admin Commands (DM only) =====
        admin_only_commands = ['-p', '-ch', '-a', '-s']
        if cmd in admin_only_commands:
            if not is_admin:
                self.send_response("⛔ Unauthorized: Admin only.", from_node, to_node, channel, is_admin_cmd=True)
                return
            
            if not is_dm:
                self.send_response("⚙️ Admin commands are DM only. Please send this command in a direct message.", 
                                 from_node, to_node, channel, is_admin_cmd=True)
                return
            
            # Route to appropriate admin handler
            if cmd == '-p':
                self._handle_provider_command(args, from_node, to_node, channel)
            elif cmd == '-ch':
                self._handle_channel_command(args, from_node, to_node, channel)
            elif cmd == '-a':
                self._handle_admin_command(args, from_node, to_node, channel)
            elif cmd == '-s':
                self._handle_scheduler_command(args, from_node, to_node, channel)
            return
        
        # ===== Default: AI Query =====
        # If no command matched, treat the entire text (minus !ai) as a query
        query = ' '.join(parts[1:]) if len(parts) > 1 else ''
        if query:
            self._handle_ai_query(query, from_node, to_node, channel)
    
    def _handle_help_command(self, from_node, to_node, channel, is_dm, is_admin):
        """Send expanded categorized multi-message help."""
        # 1. Basic Commands
        if is_dm:
            msg1 = (
                "🤖 AI Basic Commands\n"
                "!ai [msg] : Ask AI (No prefix in session)\n"
                "!ai -h : Show this help\n"
                "!ai -m : Memory/context status\n"
                "!ai -n : Safely reset context"
            )
        else:
            msg1 = (
                "🤖 AI Basic Commands\n"
                "!ai [msg] : Ask AI (prefix required)\n"
                "!ai -h : Show this help\n"
                "!ai -m : Memory/context status\n"
                "!ai -n : Safely reset context"
            )
        self.send_response(msg1, from_node, to_node, channel, is_admin_cmd=False)
        
        # 2. Session Management (DM only)
        if is_dm:
            msg2 = (
                "👤 Session Management\n"
                "!ai -n [name] : New named session\n"
                "!ai -n rm all : Nuclear Wipe\n"
                "!ai -c ls : List saved convos\n"
                "!ai -c [id] : Load convo #id\n"
                "!ai -c rm [id] : Delete convo\n"
                "!ai -end : Close session"
            )
            self.send_response(msg2, from_node, to_node, channel, is_admin_cmd=False)
        
        # 3. Actions & Tasks
        msg3 = (
            "📡 Proactive Tasks & Alerts\n"
            "!ai -s : List tasks\n"
            "!ai cancel [id] : Stop alert"
        )
        self.send_response(msg3, from_node, to_node, channel, is_admin_cmd=False)
        
        # 4. Practical Examples
        msg4 = (
            "💡 Examples\n"
            "- \"!ai Ping SNR/count every 15s\"\n"
            "- \"!ai Watch XYZ temp; alert if >35\"\n"
            "- \"!ai Remind me at 10pm to swap\"\n"
            "- \"!ai Tell Node X I am coming\""
        )
        self.send_response(msg4, from_node, to_node, channel, is_admin_cmd=False)
        
        # 5. Admin Tools (Admin + DM only)
        if is_admin and is_dm:
            msg5 = (
                "⚙️ Admin Tools\n"
                "!ai -p  [ollama|gemini] : Switch AI\n"
                "!ai -ch [ls|add 1|rm 1] : Channels\n"
                "!ai -a  [ls|add !id|rm !id] : Admins\n"
                "!ai -s  [ls|rm id|rm all] : Proactive tasks"
            )
            self.send_response(msg5, from_node, to_node, channel, is_admin_cmd=False)
    
    def _handle_conversation_command(self, args, from_node, to_node, channel):
        """Handle conversation management commands with Semantic Memory integration."""
        is_dm = self._is_dm(to_node)
        
        if not args:
            # Load last conversation
            metadata = self.conversation_manager._load_metadata(from_node)
            if metadata:
                latest = max(metadata.items(), key=lambda x: x[1]['last_access'])
                self._load_and_respond(latest[0], from_node, to_node, channel, is_dm)
            else:
                self.send_response("No saved conversations found.", from_node, to_node, channel)
            return
        
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower()
        
        if subcmd == 'ls':
            # 1. Get Disk listing
            listing = self.conversation_manager.list_conversations(from_node)
            
            # 2. Add Semantic clues if enabled
            if self.mcp_client and self.mcp_client.has_server('memory'):
                listing += "\n(Semantic Memory Active: Deep Archive enabled)"
            
            self.send_response(listing, from_node, to_node, channel)
        
        elif subcmd == 'rm':
            if len(parts) < 2:
                self.send_response("Usage: !ai -c rm [id/all]", from_node, to_node, channel)
                return
            identifier = parts[1]
            
            if identifier.lower() == 'all':
                success, message = self.conversation_manager.delete_all_conversations(from_node)
                # Semantic Wipe
                self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': 'all', 'channel': channel})
                self.send_response(f"{message} (Graph pruned)", from_node, to_node, channel)
                self.session_manager.end_session(from_node)
                
                # Clear correct active history key
                key = self._get_history_key(from_node, channel, is_dm)
                self.clear_history(key)
            else:
                # Sync delete - find name first
                name = self._resolve_conversation_name(from_node, identifier)
                success, message = self.conversation_manager.delete_conversation(from_node, identifier)
                if name:
                    self._index_to_mcp('delete_history', {'node_id': from_node, 'topic': name, 'channel': channel})
                self.send_response(f"{message} (Graph synced)", from_node, to_node, channel)
        
        else:
            # Load specific conversation
            self._load_and_respond(args, from_node, to_node, channel, is_dm)

    def _resolve_conversation_name(self, node_id, identifier):
        """Resolve a slot ID or prefix to a full conversation name."""
        metadata = self.conversation_manager._load_metadata(node_id)
        if identifier.isdigit():
            target = int(identifier)
            for name, data in metadata.items():
                if data['index'] == target:
                    return name
        elif identifier in metadata:
            return identifier
        return None

    def _load_and_respond(self, identifier, from_node, to_node, channel, is_dm):
        """Helper to load a session (Semantic Graph or disk archive) and notify user."""
        logger = get_logger()
        history = None
        conversation_name = self._resolve_conversation_name(from_node, identifier) or identifier
        success = False
        message = f"Conversation '{identifier}' not found."
        source = None

        # 1. Try Semantic Graph re-hydration first
        if self.mcp_client and self.mcp_client.has_server('memory'):
            rehydrated = self._rehydrate_session_from_graph(from_node, conversation_name)
            if rehydrated:
                history = rehydrated
                success = True
                source = 'graph'
                message = f"💧 Re-hydrated '{conversation_name}' from Semantic Graph ({len(rehydrated)//2} turns)."

        # 2. Fallback to disk archive (verbatim, used when graph has no record)
        if not success:
            success, message, history, loaded_name = self.conversation_manager.load_conversation(from_node, identifier)
            if success:
                conversation_name = loaded_name
                source = 'disk'

        if success and history:
            with self._history_lock:
                self.history[conversation_name] = history
            self._refresh_metadata_nodes.add(from_node)
            if is_dm:
                self.session_manager.start_session(from_node, conversation_name, channel, to_node)
                message += "\n🟢 Session Resumed"
            logger.info(f"📂 Loaded session '{conversation_name}' for {from_node} from {source}")
            self.send_response(message, from_node, to_node, channel)
        else:
            self.send_response(message, from_node, to_node, channel)

    def _rehydrate_session_from_graph(self, node_id, topic_name):
        """Reconstruct a session's conversation history from Knowledge Graph observations."""
        logger = get_logger()
        try:
            # Step 1: hubs that link to this topic via discusses_topic
            topic_result = self.mcp_client.call_tool("memory_query_facts", {
                "entity": topic_name,
                "direction": "incoming"
            })
            topic_hubs = set()
            if isinstance(topic_result, dict):
                for fact in topic_result.get("facts", []):
                    if fact.get("predicate") == "discusses_topic":
                        topic_hubs.add(fact["subject"])

            # Step 2: hubs this node participated in
            node_result = self.mcp_client.call_tool("memory_query_facts", {
                "entity": node_id,
                "direction": "outgoing"
            })
            node_hubs = set()
            if isinstance(node_result, dict):
                for fact in node_result.get("facts", []):
                    if fact.get("predicate") == "participated_in":
                        node_hubs.add(fact["object"])

            # Validated candidates: hub discusses the topic AND belongs to this node
            candidate_hubs = topic_hubs & node_hubs

            # Fallback: topic_name might be the hub name itself (e.g. Chat_!abc_CH0)
            if not candidate_hubs and topic_name in node_hubs:
                candidate_hubs = {topic_name}

            if not candidate_hubs:
                logger.debug(f"Re-hydration: no hub found for {node_id}/{topic_name}")
                return None

            # Step 3: collect recorded_turn observations across all matching hubs
            all_turns = []
            for hub in candidate_hubs:
                hub_result = self.mcp_client.call_tool("memory_query_facts", {
                    "entity": hub,
                    "direction": "outgoing"
                })
                if isinstance(hub_result, dict):
                    for fact in hub_result.get("facts", []):
                        if fact.get("predicate") == "recorded_turn":
                            all_turns.append({
                                "observation": fact.get("object", ""),
                                "valid_from": fact.get("valid_from") or "",
                            })

            if not all_turns:
                logger.debug(f"Re-hydration: hubs found but no recorded_turn facts for {node_id}/{topic_name}")
                return None

            # Step 4: chronological order then parse into history format
            all_turns.sort(key=lambda t: t["valid_from"])

            history = []
            for turn in all_turns:
                obs = turn["observation"]
                if obs.startswith("User: ") and " | AI: " in obs:
                    user_part, ai_part = obs.split(" | AI: ", 1)
                    history.append({"role": "user",      "content": user_part[len("User: "):]})
                    history.append({"role": "assistant", "content": ai_part})
                elif obs.startswith("System Task: ") and " | Result: " in obs:
                    sys_part, res_part = obs.split(" | Result: ", 1)
                    history.append({"role": "user",      "content": f"[Context] {sys_part}"})
                    history.append({"role": "assistant", "content": res_part})
                else:
                    history.append({"role": "user",      "content": obs})
                    history.append({"role": "assistant", "content": "[Noted]"})

            if not history:
                return None

            logger.info(f"💧 Re-hydrated {len(all_turns)} turns for {node_id}/{topic_name} "
                        f"from {len(candidate_hubs)} hub(s)")
            return history

        except Exception as e:
            logger.warning(f"Re-hydration from graph failed for {node_id}/{topic_name}: {e}")
            return None
    
    def _is_provider_configured(self, provider_name):
        """Check if an AI provider has valid configuration / API keys."""
        config = get_config_module()
        p = provider_name.lower()
        if p in ['ollama', 'local']:
            return True
        elif p == 'gemini':
            key = self.config.get('gemini_api_key') or getattr(config, 'GEMINI_API_KEY', '')
            return bool(key and str(key).strip())
        elif p == 'anthropic':
            key = self.config.get('anthropic_api_key') or getattr(config, 'ANTHROPIC_API_KEY', '')
            return bool(key and str(key).strip())
        elif p == 'openai':
            key = self.config.get('openai_api_key') or getattr(config, 'OPENAI_API_KEY', '')
            return bool(key and str(key).strip())
        return False

    def _handle_provider_command(self, args, from_node, to_node, channel):
        """Handle AI provider switching."""
        if not args or args.strip().lower() == 'ls':
            current = self.config.get('current_provider', 'ollama')
            providers_status = []
            for p in ['ollama', 'gemini', 'anthropic', 'openai']:
                is_configured = self._is_provider_configured(p)
                if p == current:
                    marker = "✅"
                    suffix = " (active)"
                elif is_configured:
                    marker = "🟢"
                    suffix = " (ready)"
                else:
                    marker = "❌"
                    suffix = " (no key)"
                providers_status.append(f"{marker} {p}{suffix}")
            
            message = "🤖 AI Providers:\n" + "\n".join(providers_status)
            self.send_response(message, from_node, to_node, channel, is_admin_cmd=True)
            return
        
        # Switch provider
        provider = args.lower()
        valid_providers = ['ollama', 'local', 'gemini', 'openai', 'anthropic']
        
        if provider not in valid_providers:
            self.send_response(f"Invalid provider. Choose: {', '.join(valid_providers)}", 
                             from_node, to_node, channel, is_admin_cmd=True)
            return
        
        if provider == 'local':
            provider = 'ollama'
        
        self.config['current_provider'] = provider
        self.config.save()
        
        provider_labels = {
            'ollama': 'LOCAL',
            'gemini': 'ONLINE',
            'openai': 'ONLINE',
            'anthropic': 'ONLINE'
        }
        label = provider_labels.get(provider, provider.upper())
        if not self._is_provider_configured(provider):
            self.send_response(f"⚠️ Switched to {label} ({provider}), but it is unconfigured (missing API key).", from_node, to_node, channel, is_admin_cmd=True)
        else:
            self.send_response(f"✅ Switched to {label} ({provider})", from_node, to_node, channel, is_admin_cmd=True)

    def _handle_scheduler_command(self, args, from_node, to_node, channel):
        """Handle !ai -s admin command for managing proactive tasks."""
        time = get_time()
        sub_parts = args.split(maxsplit=1) if args else []
        sub = sub_parts[0].lower() if sub_parts else ''
        sub_arg = sub_parts[1] if len(sub_parts) > 1 else ''

        # ── LIST ───────────────────────────────────────────────────────────
        if not sub or sub == 'ls' or sub == 'list':
            lines = []
            now = time.time()

            with self._scheduled_tasks_lock:
                for t in self.scheduled_tasks:
                    remaining = max(0, int(t['next_time'] - now))
                    recur = f" (every {t['interval']}s)" if t.get('interval') else ""
                    lines.append(f"[{t['id']}] ⏰ {t['from_node']} → {t['context_note']} in {remaining}s{recur}")

            with self._condition_watchers_lock:
                for w in self.condition_watchers:
                    lines.append(f"[{w['id']}] 👁 {w['from_node']} → {w['node_id']} {w['metric']}{w['operator']}{w['threshold']}")

            with self._node_online_watchers_lock:
                for w in self.node_online_watchers:
                    lines.append(f"[{w['id']}] 🟢 {w['from_node']} → waiting for {w['node_id']}")

            if not lines:
                self.send_response("📋 No active proactive tasks.", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response("📋 Active tasks:\n" + "\n".join(lines), from_node, to_node, channel, is_admin_cmd=True)
            return

        # ── REMOVE ─────────────────────────────────────────────────────────
        if sub == 'rm':
            if not sub_arg:
                self.send_response("Usage: !ai -s rm <id/all>", from_node, to_node, channel, is_admin_cmd=True)
                return

            cancel_all = (sub_arg.strip().lower() == 'all')
            task_id = sub_arg.strip()
            cancelled = []

            with self._scheduled_tasks_lock:
                keep, remove = [], []
                for t in self.scheduled_tasks:
                    if cancel_all or t.get('id') == task_id:
                        remove.append(t.get('id'))
                    else:
                        keep.append(t)
                self.scheduled_tasks = keep
                cancelled.extend(remove)

            with self._condition_watchers_lock:
                keep, remove = [], []
                for w in self.condition_watchers:
                    if cancel_all or w.get('id') == task_id:
                        remove.append(w.get('id'))
                    else:
                        keep.append(w)
                self.condition_watchers = keep
                cancelled.extend(remove)

            with self._node_online_watchers_lock:
                keep, remove = [], []
                for w in self.node_online_watchers:
                    if cancel_all or w.get('id') == task_id:
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
                self.send_response(f"✅ Removed: {ids}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                self.send_response(f"❌ Task [{task_id}] not found.", from_node, to_node, channel, is_admin_cmd=True)
            return

        # ── ADD (usage hint) ────────────────────────────────────────────────
        if sub == 'add':
            hint = (
                "To add tasks, use natural language:\n"
                "• !ai Remind me in 5m → sched timer\n"
                "• !ai Ping me every 30s for 5m\n"
                "• !ai Alert me when L4B1 battery <10%\n"
                "• !ai Notify me when L4B1 comes online"
            )
            self.send_response(hint, from_node, to_node, channel, is_admin_cmd=True)
            return

        # ── FALLBACK ────────────────────────────────────────────────────────
        usage = "Usage: !ai -s [rm <id/all>]"
        self.send_response(usage, from_node, to_node, channel, is_admin_cmd=True)
