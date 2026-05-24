from agent.gbrain_route import classify_gbrain_route, render_gbrain_route_hint
from agent.runtime_state import render_active_state_context
from tests.agent.test_runtime_state import _agent


def test_gbrain_route_allowed_after_local_and_lineage_miss_for_durable_intent():
    hint = classify_gbrain_route(
        "найди roadmap проекта в Gbrain",
        local_resolved=False,
        lineage_hits=0,
    )

    assert hint.status == "allowed"
    assert hint.prompt_dump_allowed is False
    assert hint.required_provenance is True
    rendered = render_gbrain_route_hint(hint)
    assert "Scoped Gbrain Retrieval Route" in rendered
    assert "do not dump raw Gbrain pages" in rendered


def test_gbrain_route_deferred_when_lineage_has_evidence():
    hint = classify_gbrain_route(
        "найди roadmap проекта",
        local_resolved=False,
        lineage_hits=2,
    )

    assert hint.status == "defer"
    assert hint.reason == "current_lineage_has_evidence"


def test_active_state_context_injects_gbrain_route_without_raw_dump(tmp_path):
    agent = _agent(tmp_path)
    agent._session_db.create_session("s-current", "telegram")

    context = render_active_state_context(agent, "найди roadmap проекта в Gbrain")

    assert "Scoped Gbrain Retrieval Route" in context
    assert "do not dump raw Gbrain pages" in context
    assert "Reference Resolver" not in context  # no ambiguous reference, route hint stays standalone


def test_active_state_context_defers_gbrain_route_when_lineage_matches(tmp_path):
    agent = _agent(tmp_path)
    agent._session_db.create_session("s-current", "telegram")
    agent._session_db.append_message("s-current", "user", "roadmap проекта уже обсуждался локально")

    context = render_active_state_context(agent, "найди roadmap проекта")

    assert "Current Session Lineage Evidence" in context
    assert "status: defer" in context
