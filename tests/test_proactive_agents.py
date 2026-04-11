import os
import json
import pytest
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_config():
    with patch('ai_responder.Config') as MockConfig:
        config_inst = MockConfig.return_value
        config_inst.get.return_value = 'ollama'
        yield config_inst

@pytest.fixture
def mock_meshtastic():
    with patch('ai_responder.MeshtasticHandler') as MockHandler:
        yield MockHandler

@pytest.fixture
def responder(tmp_path, mock_config, mock_meshtastic):
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    
    conversations_dir = tmp_path / "conversations"
    
    with patch('config.HISTORY_DIR', str(history_dir)), \
         patch('config.CONVERSATIONS_DIR', str(conversations_dir)), \
         patch('conversation.manager.config.CONVERSATIONS_DIR', str(conversations_dir)):
        from ai_responder import AIResponder
        responder = AIResponder(history_dir=str(history_dir))
        responder._active_workers = {
            123: {'start_time': 0, 'from_node': '!user1', 'to_node': '!bot_id', 'channel': 0}
        }
        with patch('threading.get_ident', return_value=123):
            yield responder

def test_proactive_persistence(responder, tmp_path):
    import config
    # Register 1 task
    responder._schedule_mcp_task(delay_seconds=60, context_note="Test reminder", from_node='!user1', to_node='!bot', channel=0)
    
    assert len(responder.scheduled_tasks) == 1
    task_id = responder.scheduled_tasks[0]['id']
    
    # File should exist thanks to _save_proactive_tasks
    file_path = tmp_path / "history" / config.PROACTIVE_TASKS_FILE
    assert file_path.exists()
    
    # Load into a new responder instance
    conversations_dir = tmp_path / "conversations"
    with patch('config.HISTORY_DIR', str(tmp_path / "history")), \
         patch('config.CONVERSATIONS_DIR', str(conversations_dir)), \
         patch('conversation.manager.config.CONVERSATIONS_DIR', str(conversations_dir)):
        from ai_responder import AIResponder
        new_responder = AIResponder(history_dir=str(tmp_path / "history"))
        
        assert len(new_responder.scheduled_tasks) == 1
        assert new_responder.scheduled_tasks[0]['id'] == task_id
        
        # Test ID increments correctly (resume from task_id)
        id_num = int(task_id.split('-')[1])
        next_id = f"sched-{id_num + 1}"
        new_responder._active_workers = {
            321: {'start_time': 0, 'from_node': '!user1', 'to_node': '!bot_id', 'channel': 0}
        }
        with patch('threading.get_ident', return_value=321):
            new_responder._schedule_mcp_task(delay_seconds=60, context_note="Test reminder 2", from_node='!user1', to_node='!bot', channel=0)
            assert new_responder.scheduled_tasks[1]['id'] == next_id

def test_proactive_task_limits(responder):
    import config
    
    with patch('ai_responder.config.MAX_PROACTIVE_TASKS_PER_USER', 2):
        # Add 2 tasks (should succeed)
        res1 = responder._schedule_mcp_task(delay_seconds=60, context_note="T1", from_node='!user1', to_node='!bot', channel=0)
        assert "✅" in res1
        
        res2 = responder._schedule_mcp_task(delay_seconds=60, context_note="T2", from_node='!user1', to_node='!bot', channel=0)
        assert "✅" in res2
        
        assert len(responder.scheduled_tasks) == 2
        
        # Add 3rd task (should fail)
        res3 = responder._schedule_mcp_task(delay_seconds=60, context_note="T3", from_node='!user1', to_node='!bot', channel=0)
        assert "⚠️ Limit reached" in res3
        assert len(responder.scheduled_tasks) == 2

def test_watch_condition_tool(responder):
    """Test adding a condition watcher."""
    res = responder._watch_condition_mcp(
        node_id_or_name="!abcd",
        metric="battery_level",
        operator="<",
        threshold=20,
        context_note="Low battery",
        from_node="!user1",
        to_node="!bot",
        channel=0
    )
    assert "✅" in res
    assert len(responder.condition_watchers) == 1
    
    watcher = responder.condition_watchers[0]
    assert watcher['metric'] == "battery_level"
    assert watcher['operator'] == "<"
    assert watcher['threshold'] == 20
    assert watcher['node_id'] == "!abcd"

def test_watch_node_online_tool(responder):
    """Test adding a node online watcher."""
    res = responder._watch_node_online_mcp(
        node_id_or_name="!1234",
        context_note="Node returned",
        from_node="!user1",
        to_node="!bot",
        channel=0
    )
    assert "✅" in res
    assert len(responder.node_online_watchers) == 1
    
    watcher = responder.node_online_watchers[0]
    assert watcher['node_id'] == "!1234"
    assert watcher['context_note'] == "Node returned"

def test_list_proactive_tasks_tool(responder):
    """Test listing of multiple task types."""
    # Add dummy tasks owned by the current mock user (!user1)
    responder.scheduled_tasks.append({
        'id': 'sched-1', 'next_time': 9999999999, 'context_note': 'Test Sched', 'from_node': '!user1', 'targets': 'requester'
    })
    responder.condition_watchers.append({
        'id': 'cond-1', 'node_id': '!abcd', 'metric': 'battery_level', 'operator': '<', 'threshold': 10, 'context_note': 'Bat', 'from_node': '!user1', 'targets': 'requester'
    })
    responder.node_online_watchers.append({
        'id': 'node-1', 'node_id': '!efgh', 'context_note': 'Online', 'from_node': '!user1', 'targets': 'requester'
    })
    
    res = responder._list_proactive_tasks_mcp(from_node='!user1')
    assert "sched-1" in res
    assert "cond-1" in res
    assert "node-1" in res

def test_cancel_proactive_task_tool(responder):
    """Test cancelling tasks by ID and 'all'."""
    # Setup
    responder.scheduled_tasks.append({'id': 'sched-1', 'from_node': '!user1'})
    responder.condition_watchers.append({'id': 'cond-1', 'from_node': '!user1'})
    responder.node_online_watchers.append({'id': 'node-1', 'from_node': '!user1'})
    
    # Target single cancellation
    res = responder._cancel_proactive_task_mcp('cond-1', from_node='!user1')
    assert "✅" in res
    assert len(responder.condition_watchers) == 0
    assert len(responder.scheduled_tasks) == 1
    
    # Target 'all'
    res_all = responder._cancel_proactive_task_mcp('all', from_node='!user1')
    assert "✅" in res_all
    assert len(responder.scheduled_tasks) == 0
    assert len(responder.node_online_watchers) == 0

def test_on_telemetry_proactive_evaluations(responder):
    """Test telemetry evaluation, including snake/camel case normalization."""
    # 1. Setup condition watcher
    watcher = {
        'id': 'cond-1',
        'node_id': '!12345678',
        'metric': 'battery_level',
        'operator': '<',
        'threshold': 50,
        'context_note': 'Battery low',
        'from_node': '!user1',
        'to_node': '!bot',
        'channel': 0,
        'targets': 'requester'
    }
    responder.condition_watchers.append(watcher)
    
    # 2. Mock telemetry packet (using camelCase to test normalizer)
    packet = {
        'fromId': '!12345678',
        'decoded': {
            'telemetry': {
                'deviceMetrics': {
                    'batteryLevel': 48,
                    'voltage': 3.9
                }
            }
        }
    }
    
    with patch.object(responder, '_fire_system_trigger') as mock_trigger:
        responder._on_telemetry_proactive(packet, None)
        # Manually trigger the collector
        responder._dispatch_collector('!12345678')
        
        # Verify trigger fired and watcher was removed
        mock_trigger.assert_called_once()
        context = mock_trigger.call_args[0][0]
        assert "Condition alert: Battery low" in context
        assert len(responder.condition_watchers) == 0

def test_on_telemetry_proactive_fire_deferred(responder):
    """Test that a pending telemetry request fires when data arrives."""
    node_id = "!abcd"
    responder.pending_telemetry_requests[node_id] = {
        'from_node': '!user1', 'to_node': '!bot', 'channel': 0, 'context_note': 'environment telemetry for !abcd'
    }
    
    # Minimal telemetry packet to trigger interest
    packet = {
        'fromId': node_id, 
        'decoded': {'telemetry': {'deviceMetrics': {'batteryLevel': 99}}}
    }
    
    with patch.object(responder, '_fire_system_trigger') as mock_trigger:
        responder._on_telemetry_proactive(packet, None)
        # Manually trigger the collector
        responder._dispatch_collector(node_id)
        
        mock_trigger.assert_called_once()
        context = mock_trigger.call_args[0][0]
        assert "Requested environment telemetry" in context
        assert node_id not in responder.pending_telemetry_requests

def test_fire_system_trigger(responder):
    """Test the system trigger routes messages correctly."""
    with patch.object(responder, '_process_ai_query_thread') as mock_thread, \
         patch.object(responder, 'config') as mock_config:
        
        # Test Direct Send (ch:0) targeting a channel
        mock_config.get.return_value = [0, 1]
        responder._fire_system_trigger(
            context_note="Alert!", 
            from_node="!user1", 
            to_node="!bot", 
            channel=0, 
            targets="ch:1"
        )
        assert mock_thread.call_count == 1
        args, kwargs = mock_thread.call_args
        assert args[1] == "^all"  # to_node
        assert args[3] == 1       # channel
        
        # Test Requester
        responder._fire_system_trigger(
            context_note="Alert!", 
            from_node="!user1", 
            to_node="!bot", 
            channel=0, 
            targets="requester"
        )
        assert mock_thread.call_count == 2
        args, kwargs = mock_thread.call_args
        assert args[1] == "!user1" # to_node
        assert args[3] == 0        # channel

def test_collector_aggregation(responder):
    """Test that multiple events for the same node are aggregated into one trigger."""
    node_id = "!9999"
    
    # 1. Setup a pending request
    responder.pending_telemetry_requests[node_id] = {
        'from_node': '!user1', 'to_node': '!bot', 'channel': 0, 'context_note': 'environment telemetry'
    }
    
    # 2. Setup a condition watcher
    watcher = {
        'id': 'cond-agg', 'node_id': node_id, 'metric': 'temperature', 'operator': '>', 'threshold': 30,
        'context_note': 'High temp', 'from_node': '!user1', 'to_node': '!bot', 'channel': 0, 'targets': 'requester'
    }
    responder.condition_watchers.append(watcher)
    
    # 3. Fire a telemetry packet that triggers both
    packet = {
        'fromId': node_id,
        'decoded': {
            'telemetry': {
                'environmentMetrics': {'temperature': 35}
            }
        }
    }
    
    with patch.object(responder, '_fire_system_trigger') as mock_trigger:
        # This will call _add_to_collector twice (once for deferred, once for watcher)
        responder._on_telemetry_proactive(packet, None)
        
        # Verify collector has both events
        with responder._collector_lock:
            assert len(responder._proactive_event_collector[node_id]['events']) == 2
            
        # Manually dispatch
        responder._dispatch_collector(node_id)
        
        # Verify ONLY ONE trigger fired with BOTH contexts
        mock_trigger.assert_called_once()
        context = mock_trigger.call_args[0][0]
        assert "Requested environment telemetry" in context
        assert "Condition alert: High temp" in context
        assert "temperature=35" in context

def test_system_trigger_with_history(responder):
    """Test that a system trigger can see the conversation history."""
    node_id = "!user1"
    history_key = f"DM:{node_id}"
    
    # Pre-populate history
    responder.add_to_history(history_key, 'user', "Start count at 1", node_id=node_id)
    responder.add_to_history(history_key, 'assistant', "Count is 1", node_id=node_id)
    
    with patch.object(responder, 'history') as mock_history_dict:
        # Use MagicMock to behave like the history dict
        mock_history_dict.get.return_value = [
            {'role': 'user', 'content': 'Start count at 1'},
            {'role': 'assistant', 'content': 'Count is 1'}
        ]
        
        with patch.object(responder, 'config') as mock_config, \
             patch('ai_responder.get_provider') as mock_get_provider:
            
            mock_provider = MagicMock()
            mock_provider.get_response.return_value = "Count is now 2"
            mock_get_provider.return_value = mock_provider
            
            # Fire a system trigger
            responder._process_ai_query_thread(
                query="Increment count",
                from_node=node_id,
                to_node="!bot",
                channel=0,
                is_dm=True,
                is_system_trigger=True
            )
            
            # Verify the provider was called with the history
            args, kwargs = mock_provider.get_response.call_args
            history_sent = kwargs.get('history') or args[1]
            assert len(history_sent) == 3
            assert history_sent[0]['content'] == 'Start count at 1'
