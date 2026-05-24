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
