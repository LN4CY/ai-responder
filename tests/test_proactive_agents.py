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
    
    with patch('config.HISTORY_DIR', str(history_dir)):
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
    responder._schedule_message_tool(delay_seconds=60, context_note="Test reminder")
    
    assert len(responder.scheduled_tasks) == 1
    task_id = responder.scheduled_tasks[0]['id']
    
    # File should exist thanks to _save_proactive_tasks
    file_path = tmp_path / "history" / config.PROACTIVE_TASKS_FILE
    assert file_path.exists()
    
    # Load into a new responder instance
    with patch('config.HISTORY_DIR', str(tmp_path / "history")):
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
            new_responder._schedule_message_tool(delay_seconds=60, context_note="Test reminder 2")
            assert new_responder.scheduled_tasks[1]['id'] == next_id

def test_proactive_task_limits(responder):
    import config
    
    with patch('ai_responder.config.MAX_PROACTIVE_TASKS_PER_USER', 2):
        # Add 2 tasks (should succeed)
        res1 = responder._schedule_message_tool(delay_seconds=60, context_note="T1")
        assert "✅" in res1
        
        res2 = responder._schedule_message_tool(delay_seconds=60, context_note="T2")
        assert "✅" in res2
        
        assert len(responder.scheduled_tasks) == 2
        
        # Add 3rd task (should fail)
        res3 = responder._schedule_message_tool(delay_seconds=60, context_note="T3")
        assert "⚠️ Limit reached" in res3
        assert len(responder.scheduled_tasks) == 2
