import asyncio
from unittest.mock import MagicMock

import pytest

from gateway.session import SessionStore
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


@pytest.mark.asyncio
async def test_agent_restart_marks_session_notifies_and_schedules_restart(tmp_path, monkeypatch):
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="123", thread_id="456")
    session_key = runner._session_key_for_source(source)
    runner._session_sources[session_key] = source
    runner.session_store = SessionStore(sessions_dir=tmp_path, config=runner.config)
    runner.session_store.get_or_create_session(source)
    runner._running_agents[session_key] = MagicMock()
    runner._gateway_loop = asyncio.get_running_loop()
    runner._agent_restart_state_path = tmp_path / ".agent_restart_state.json"
    runner._thread_metadata_for_source = MagicMock(return_value={"thread_id": "456"})
    runner._invalidate_session_run_generation = MagicMock()
    restart_mock = MagicMock(return_value=True)
    runner.request_restart = restart_mock
    monkeypatch.setenv("INVOCATION_ID", "systemd-test")

    result = runner.request_agent_restart(
        session_key=session_key,
        reason="config changed",
        resume_current_task=True,
        cooldown_seconds=60,
    )
    await asyncio.sleep(0.05)

    assert result["success"] is True
    assert result["status"] == "restart_scheduled"
    assert runner.session_store._entries[session_key].resume_pending is True
    assert runner.session_store._entries[session_key].resume_reason == "restart_timeout"
    assert any("Делаю restart Hermes: config changed" in msg for msg in adapter.sent)
    runner._running_agents[session_key].interrupt.assert_called_once()
    runner._invalidate_session_run_generation.assert_called_once_with(
        session_key, reason="agent_self_restart"
    )
    restart_mock.assert_called_once_with(detached=False, via_service=True)


def test_agent_restart_refuses_when_human_approval_pending(tmp_path):
    runner, _adapter = make_restart_runner()
    source = make_restart_source()
    session_key = runner._session_key_for_source(source)
    runner._session_sources[session_key] = source
    runner.session_store = SessionStore(sessions_dir=tmp_path, config=runner.config)
    runner.session_store.get_or_create_session(source)
    runner._running_agents[session_key] = MagicMock()
    runner._pending_approvals[session_key] = {"command": "rm -rf /tmp/x"}
    runner._agent_restart_state_path = tmp_path / ".agent_restart_state.json"

    result = runner.request_agent_restart(
        session_key=session_key,
        reason="should not restart",
        resume_current_task=True,
    )

    assert result["success"] is False
    assert "approval" in result["error"]
    assert runner.session_store._entries[session_key].resume_pending is False


def test_agent_restart_cooldown_guard(tmp_path):
    runner, _adapter = make_restart_runner()
    source = make_restart_source()
    session_key = runner._session_key_for_source(source)
    runner._session_sources[session_key] = source
    runner.session_store = SessionStore(sessions_dir=tmp_path, config=runner.config)
    runner.session_store.get_or_create_session(source)
    runner._running_agents[session_key] = MagicMock()
    runner._agent_restart_state_path = tmp_path / ".agent_restart_state.json"
    runner._agent_restart_state_path.write_text(
        '{"last_requested_at": 9999999999, "consecutive": 1}'
    )

    result = runner.request_agent_restart(
        session_key=session_key,
        reason="too soon",
        resume_current_task=True,
        cooldown_seconds=60,
    )

    assert result["success"] is False
    assert "cooldown" in result["error"]
    assert runner.session_store._entries[session_key].resume_pending is False
