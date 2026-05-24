from agent.active_state import ActiveStateStore
from agent.gbrain_route import classify_gbrain_route, render_gbrain_route_hint, tool_family
from agent.runtime_state import record_tool_route_observation, render_active_state_context, scope_from_agent
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
    assert hint.expected_first_tool_family == "gbrain"
    rendered = render_gbrain_route_hint(hint)
    assert "Scoped Gbrain Retrieval Route" in rendered
    assert "expected_first_tool_family: gbrain" in rendered
    assert "do not dump raw Gbrain pages" in rendered


def test_gbrain_route_deferred_when_lineage_has_evidence():
    hint = classify_gbrain_route(
        "найди roadmap проекта",
        local_resolved=False,
        lineage_hits=2,
    )

    assert hint.status == "defer"
    assert hint.reason == "current_lineage_has_evidence"
    assert hint.expected_first_tool_family is None


def test_tool_family_labels_gbrain_tools():
    assert tool_family("mcp_gbrain_knowledge_get_page") == "gbrain"
    assert tool_family("web_search") == "web"


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


def test_route_trace_records_expected_and_actual_first_tool(tmp_path):
    agent = _agent(tmp_path)
    agent._session_db.create_session("s-current", "telegram")
    scope = scope_from_agent(agent)

    render_active_state_context(agent, "найди roadmap проекта в Gbrain")
    record_tool_route_observation(agent, "mcp_gbrain_knowledge_get_page")

    state = ActiveStateStore(agent._session_db).get(scope)
    trace = state.route_traces[0]
    assert trace["expected_first_tool_family"] == "gbrain"
    assert trace["actual_first_tool"] == "mcp_gbrain_knowledge_get_page"
    assert trace["actual_first_tool_family"] == "gbrain"
    assert trace["compliance"] == "matched"


def test_route_trace_warns_on_gbrain_tool_while_lineage_route_is_deferred(tmp_path):
    agent = _agent(tmp_path)
    agent._session_db.create_session("s-current", "telegram")
    agent._session_db.append_message("s-current", "user", "roadmap проекта уже обсуждался локально")
    scope = scope_from_agent(agent)

    render_active_state_context(agent, "найди roadmap проекта")
    record_tool_route_observation(agent, "mcp_gbrain_knowledge_get_page")

    state = ActiveStateStore(agent._session_db).get(scope)
    trace = state.route_traces[0]
    assert trace["route_status"] == "defer"
    assert trace["compliance"] == "warn"
    assert trace["bypass_reason"] == "gbrain_used_while_lineage_deferred"


def test_route_trace_warns_on_bypass(tmp_path):
    agent = _agent(tmp_path)
    agent._session_db.create_session("s-current", "telegram")
    scope = scope_from_agent(agent)

    render_active_state_context(agent, "найди roadmap проекта в Gbrain")
    record_tool_route_observation(agent, "web_search")

    state = ActiveStateStore(agent._session_db).get(scope)
    trace = state.route_traces[0]
    assert trace["compliance"] == "warn"
    assert trace["bypass_reason"] == "expected_gbrain_got_web"
