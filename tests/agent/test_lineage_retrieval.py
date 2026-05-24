from agent.lineage_retrieval import render_lineage_evidence, retrieve_lineage
from agent.runtime_state import render_active_state_context
from agent.session_scope import SessionScope
from hermes_state import SessionDB
from tests.agent.test_runtime_state import _agent


def test_retrieve_lineage_searches_current_compression_chain(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s-parent", "telegram")
    db.append_message("s-parent", "user", "Нужно сохранить решение про artifact registry и compaction audit")
    db.append_message("s-parent", "assistant", "Artifact registry уже scoped by topic")
    db.create_session("s-child", "telegram", parent_session_id="s-parent")
    db.append_message("s-child", "user", "Продолжи реализацию")

    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-child")
    evidence = retrieve_lineage(db, scope, "artifact registry", limit=3)

    assert evidence
    rendered = render_lineage_evidence(evidence)
    assert "Current Session Lineage Evidence" in rendered
    assert "artifact registry" in rendered.lower()


def test_retrieve_lineage_projects_root_session_to_latest_tip(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s-root", "telegram")
    db.append_message("s-root", "user", "Root topic started before compaction")
    db.create_session("s-child", "telegram", parent_session_id="s-root")
    db.append_message("s-child", "assistant", "Latest tip contains route metrics and writeback classifier")

    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-root")
    evidence = retrieve_lineage(db, scope, "writeback classifier", limit=3)

    assert evidence
    assert any("writeback classifier" in item.content for item in evidence)


def test_retrieve_lineage_does_not_cross_unrelated_session(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s-current", "telegram")
    db.append_message("s-current", "user", "Текущая тема про compaction")
    db.create_session("s-other", "telegram")
    db.append_message("s-other", "user", "секретное слово unrelated-leak-marker")

    scope = SessionScope(platform="telegram", chat_id="806409559", thread_id="468587", session_id="s-current")
    evidence = retrieve_lineage(db, scope, "unrelated-leak-marker", limit=3)

    assert evidence == []


def test_active_state_context_injects_lineage_evidence_only_after_local_miss(tmp_path):
    agent = _agent(tmp_path)
    agent._session_db.create_session("s-current", "telegram")
    agent._session_db.append_message("s-current", "user", "Обсуждали lineage retrieval и provenance")

    context = render_active_state_context(agent, "продолжи этот lineage retrieval")

    assert "Reference Resolver" in context
    assert "Current Session Lineage Evidence" in context
    assert "lineage retrieval" in context
