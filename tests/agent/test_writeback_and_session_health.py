from agent.active_state import ActiveStateStore
from agent.session_health import build_session_health_report, render_session_health_report
from agent.writeback_classifier import classify_writeback_candidate, writeback_wrapper
from tests.agent.test_runtime_state import _agent
from agent.runtime_state import scope_from_agent


def test_writeback_classifier_skips_ephemeral_turns():
    decision = classify_writeback_candidate([{"role": "user", "content": "спасибо"}])

    assert decision.action == "skip"
    assert decision.reason == "ephemeral_or_low_signal"


def test_writeback_classifier_marks_expiry_aware_open_loop():
    decision = classify_writeback_candidate([
        {"role": "user", "content": "надо потом проверить compaction audit"}
    ])
    wrapped = writeback_wrapper(decision)

    assert decision.action == "raw_capture"
    assert decision.expires is True
    assert wrapped["target"] == "runtime_raw_capture"
    assert wrapped["promotion_required"] is False


def test_writeback_classifier_stages_significant_sessions():
    decision = classify_writeback_candidate(
        [{"role": "assistant", "content": "Решили архитектурный принцип: Gbrain canonical only."}],
        artifact_count=1,
        tool_count=6,
    )
    wrapped = writeback_wrapper(decision)

    assert decision.action == "staged_artifact"
    assert wrapped["target"] == "gbrain_staging_artifact"
    assert wrapped["promotion_required"] is True


def test_writeback_classifier_requires_review_for_explicit_memory():
    decision = classify_writeback_candidate(
        [{"role": "user", "content": "запомни это как правило проекта"}],
        user_requested_memory=True,
    )
    wrapped = writeback_wrapper(decision)

    assert decision.action == "canonical_review"
    assert wrapped["target"] == "gbrain_review_queue"
    assert wrapped["promotion_required"] is True


def test_session_health_report_surfaces_route_and_writeback_metrics(tmp_path):
    agent = _agent(tmp_path)
    scope = scope_from_agent(agent)
    store = ActiveStateStore(agent._session_db)
    store.record_route_trace(scope, {"expected_first_tool_family": "gbrain", "query": "roadmap"})
    store.record_first_tool(scope, tool_name="web_search", tool_family="web")
    store.record_writeback_decision(scope, {"action": "staged_artifact", "reason": "significant"})

    report = build_session_health_report(agent._session_db, scope)
    rendered = render_session_health_report(report)

    assert report["status"] == "attention"
    assert report["route_warnings"] == 1
    assert report["writeback_pending"] == 1
    assert "Infinite Session Health" in rendered
    assert "route_warnings: 1" in rendered
