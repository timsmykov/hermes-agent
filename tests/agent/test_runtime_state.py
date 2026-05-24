from types import SimpleNamespace

from agent.runtime_state import (
    artifact_from_tool_result,
    record_compaction_handoff,
    register_tool_artifact,
    render_active_state_context,
    scope_from_agent,
)
from hermes_state import SessionDB


class DummyAgent(SimpleNamespace):
    pass


def _agent(tmp_path):
    return DummyAgent(
        platform="telegram",
        _chat_id="806409559",
        _thread_id="468587",
        _gateway_session_key="agent:main:telegram:dm:806409559:468587",
        _profile_name="orchestrator",
        session_id="s-current",
        _session_db=SessionDB(tmp_path / "state.db"),
    )


def test_scope_from_agent_matches_topic_scope(tmp_path):
    scope = scope_from_agent(_agent(tmp_path))

    assert scope.scope_key == "telegram:806409559:thread:468587"


def test_artifact_from_write_file_result():
    artifact = artifact_from_tool_result(
        "write_file",
        {"path": "/tmp/report.md"},
        '{"bytes_written": 10}',
    )

    assert artifact is not None
    assert artifact["artifact_id"] == "file:/tmp/report.md"
    assert artifact["kind"] == "file"


def test_register_tool_artifact_feeds_active_state_context(tmp_path):
    agent = _agent(tmp_path)

    register_tool_artifact(
        agent,
        "write_file",
        {"path": "/tmp/report.md"},
        '{"bytes_written": 10}',
    )
    context = render_active_state_context(agent, "продолжи этот файл")

    assert "Active Session State" in context
    assert "/tmp/report.md" in context
    assert "Reference Resolver" in context
    assert '"status": "resolved"' in context


def test_register_tool_artifact_ignores_failed_result(tmp_path):
    agent = _agent(tmp_path)

    register_tool_artifact(
        agent,
        "write_file",
        {"path": "/tmp/report.md"},
        '{"error": "boom"}',
    )
    context = render_active_state_context(agent, "продолжи этот файл")

    assert "/tmp/report.md" not in context


def test_record_compaction_handoff_preserves_active_state_audit(tmp_path):
    agent = _agent(tmp_path)
    register_tool_artifact(
        agent,
        "write_file",
        {"path": "/tmp/report.md"},
        '{"bytes_written": 10}',
    )

    record_compaction_handoff(
        agent,
        old_session_id="s-old",
        new_session_id="s-new",
        before_count=120,
        after_count=12,
    )

    scope = scope_from_agent(agent)
    state = agent._session_db.get_active_session_state(scope.scope_key)
    assert state["handoff"]["kind"] == "context_compaction"
    assert state["handoff"]["old_session_id"] == "s-old"
    assert state["handoff"]["active_artifact_count"] == 1
    events = agent._session_db.list_active_session_events(scope.scope_key)
    assert events[0]["event_type"] == "handoff_recorded"
