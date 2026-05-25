import time

from agent.active_state import ActiveStateStore
from agent.session_scope import SessionScope
from gateway.config import Platform
from gateway.session import SessionSource
from hermes_state import SessionDB


def test_session_scope_isolates_telegram_topics():
    source_a = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="806409559",
        chat_type="dm",
        thread_id="468587",
    )
    source_b = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="806409559",
        chat_type="dm",
        thread_id="465413",
    )

    scope_a = SessionScope.from_session_source(source_a, session_id="s-a")
    scope_b = SessionScope.from_session_source(source_b, session_id="s-b")

    assert scope_a.scope_key == "telegram:806409559:thread:468587"
    assert scope_b.scope_key == "telegram:806409559:thread:465413"
    assert scope_a.scope_key != scope_b.scope_key


def test_session_scope_uses_session_id_for_cli_without_chat():
    scope = SessionScope(platform="local", session_id="abc123")

    assert scope.scope_key == "local:session:abc123"
    assert scope.to_dict()["session_id"] == "abc123"


def test_active_state_store_persists_per_scope(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope_a = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")
    scope_b = SessionScope(platform="telegram", chat_id="806409559", thread_id="465413", session_id="s-b")

    store.update_latest_user_request(
        scope_a,
        text="сделай этот отчёт",
        message_id="m1",
        timestamp=time.time(),
    )
    store.register_artifact(
        scope_a,
        {
            "artifact_id": "report-a",
            "kind": "report",
            "title": "Infinite Session report",
            "source_message_id": "m1",
        },
    )

    state_a = store.get(scope_a)
    state_b = store.get(scope_b)

    assert state_a.latest_user_request is not None
    assert state_a.latest_user_request["text"] == "сделай этот отчёт"
    assert state_a.active_artifacts[0]["artifact_id"] == "report-a"
    assert state_b.latest_user_request is None
    assert state_b.active_artifacts == []

    events = db.list_active_session_events(scope_a.scope_key)
    assert [event["event_type"] for event in events] == ["artifact_registered", "latest_user_request"]


def test_active_state_archives_stale_artifacts(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.register_artifact(
        scope,
        {
            "artifact_id": "old-file",
            "kind": "file",
            "title": "old.md",
            "created_at": 10.0,
            "updated_at": 10.0,
        },
    )
    state = store.archive_stale_artifacts(scope, older_than_seconds=100.0, now=200.0)

    assert state.active_artifacts[0]["status"] == "archived"
    assert state.active_artifacts[0]["status_reason"] == "stale"
    events = db.list_active_session_events(scope.scope_key)
    assert events[0]["event_type"] == "artifacts_archived_stale"


def test_current_task_updates_from_user_and_final_assistant_output(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.update_latest_user_request(scope, text="доработай Infinite Session Engine", message_id="u1")
    store.update_current_task_from_assistant(scope, text="Готово: задача завершена.", message_id="a1")

    state = store.get(scope)
    assert state.current_task["text"] == "доработай Infinite Session Engine"
    assert state.current_task["status"] == "completed"
    assert state.current_task["completed_message_id"] == "a1"


def test_current_task_ignores_low_signal_user_turn(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.update_latest_user_request(scope, text="спасибо", message_id="u1")

    assert store.get(scope).current_task is None


def test_current_task_does_not_overwrite_in_progress_task_with_status_question(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.update_latest_user_request(scope, text="закрыть roadmap до конца", message_id="u1")
    store.update_latest_user_request(scope, text="что по статусу?", message_id="u2")

    state = store.get(scope)
    assert state.current_task["text"] == "закрыть roadmap до конца"
    assert state.latest_user_request["text"] == "что по статусу?"


def test_latest_substantive_user_turn_replaces_stale_in_progress_task(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.update_latest_user_request(scope, text="посмотри перегруз сервера", message_id="u1", timestamp=1000)
    store.update_latest_user_request(
        scope,
        text="после завершения закоммить все изменения в репозиториях",
        message_id="u2",
        timestamp=2000,
    )

    state = store.get(scope)
    assert state.current_task is not None
    assert state.latest_user_request is not None
    assert state.current_task["text"] == "после завершения закоммить все изменения в репозиториях"
    assert state.current_task["source_message_id"] == "u2"
    assert state.latest_user_request["text"] == "после завершения закоммить все изменения в репозиториях"


def test_current_task_not_completed_by_negated_assistant_output(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.update_latest_user_request(scope, text="доделать компонент", message_id="u1")
    store.update_current_task_from_assistant(scope, text="Not done yet — need more tests", message_id="a1")

    assert store.get(scope).current_task["status"] == "in_progress"


def test_active_state_save_merges_stale_additive_updates(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    stale_a = store.get(scope)
    stale_b = store.get(scope)
    stale_a.active_artifacts = [{"artifact_id": "file:/tmp/a.md", "kind": "file", "status": "active"}]
    saved_a = store.save(stale_a, event_type="artifact_registered")
    stale_b.route_traces = [{"trace_id": "route:t1", "turn_id": "t1", "compliance": "pending"}]
    saved_b = store.save(stale_b, event_type="route_trace_recorded")

    state = store.get(scope)
    assert saved_a.version == 1
    assert saved_b.version > saved_a.version
    assert [artifact["artifact_id"] for artifact in state.active_artifacts] == ["file:/tmp/a.md"]
    assert [trace["trace_id"] for trace in state.route_traces] == ["route:t1"]
    assert any(entry.get("stale_save_merged") for entry in state.mutation_audit)


def test_register_artifact_supersedes_previous_same_file_path(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.register_artifact(scope, {"artifact_id": "file:v1", "kind": "file", "local_path": "/tmp/report.md"})
    store.register_artifact(scope, {"artifact_id": "file:v2", "kind": "file", "local_path": "/tmp/report.md"})

    state = store.get(scope)
    assert state.active_artifacts[0]["artifact_id"] == "file:v2"
    assert state.active_artifacts[0]["status"] == "active"
    assert state.active_artifacts[1]["artifact_id"] == "file:v1"
    assert state.active_artifacts[1]["status"] == "superseded"
    assert state.active_artifacts[1]["superseded_by"] == "file:v2"


def test_active_state_debug_report_is_compact_and_scoped(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.update_latest_user_request(scope, text="доделать runtime continuity", message_id="u1")
    store.register_artifact(scope, {"artifact_id": "file:v1", "kind": "file", "local_path": "/tmp/report.md"})
    report = store.render_debug_report(scope)

    assert "Active Session Debug" in report
    assert "telegram:806409559:thread:468587" in report
    assert "current_task_status: in_progress" in report
    assert "artifact: file active /tmp/report.md" in report
