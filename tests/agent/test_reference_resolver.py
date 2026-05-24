from agent.active_state import ActiveStateStore
from agent.reference_resolver import ReferenceResolver
from agent.session_scope import SessionScope
from hermes_state import SessionDB


def _store(tmp_path):
    return ActiveStateStore(SessionDB(tmp_path / "state.db"))


def test_reference_resolver_resolves_current_scope_artifact(tmp_path):
    store = _store(tmp_path)
    resolver = ReferenceResolver(store)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.register_artifact(
        scope,
        {
            "artifact_id": "report-a",
            "kind": "report",
            "title": "Infinite Session report",
        },
    )

    result = resolver.resolve(scope, "продолжи этот отчёт")

    assert result.status == "resolved"
    assert result.source == "active_state"
    assert result.artifact is not None
    assert result.artifact["artifact_id"] == "report-a"


def test_reference_resolver_does_not_cross_topic_scope(tmp_path):
    store = _store(tmp_path)
    resolver = ReferenceResolver(store)
    scope_a = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")
    scope_b = SessionScope(platform="telegram", chat_id="806409559", thread_id="465413", session_id="s-b")

    store.register_artifact(
        scope_a,
        {
            "artifact_id": "report-a",
            "kind": "report",
            "title": "Topic A report",
        },
    )

    result = resolver.resolve(scope_b, "покажи этот отчёт")

    assert result.status == "needs_clarification"
    assert result.reason == "no_active_artifacts_in_scope"
    assert result.candidates == []


def test_reference_resolver_ignores_non_ambiguous_text(tmp_path):
    store = _store(tmp_path)
    resolver = ReferenceResolver(store)
    scope = SessionScope(platform="local", session_id="cli-1")

    result = resolver.resolve(scope, "создай новый отчёт")

    assert result.status == "not_applicable"
    assert result.reason == "no_ambiguous_reference"


def test_reference_resolver_prefers_reply_or_attachment_artifact(tmp_path):
    store = _store(tmp_path)
    resolver = ReferenceResolver(store)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")

    store.register_artifact(scope, {"artifact_id": "old", "kind": "file", "title": "old.md"})
    store.register_artifact(
        scope,
        {"artifact_id": "reply", "kind": "file", "title": "reply.md", "source": "reply"},
    )

    result = resolver.resolve(scope, "продолжи этот файл")

    assert result.status == "resolved"
    assert result.artifact is not None
    assert result.artifact["artifact_id"] == "reply"


def test_reference_resolver_falls_back_to_current_lineage(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = ActiveStateStore(db)
    resolver = ReferenceResolver(store, db)
    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-a")
    db.create_session("s-a", source="telegram")
    db.append_message("s-a", role="assistant", content="Сделал отчёт по Infinite Session Engine в /tmp/session-report.md")

    result = resolver.resolve(scope, "продолжи этот отчёт")

    assert result.source == "session_lineage"
    assert result.status == "resolved"
    assert result.artifact is not None
    assert "session-report" in result.artifact["content"]
