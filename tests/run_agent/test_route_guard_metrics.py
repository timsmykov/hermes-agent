from agent.route_guard_metrics import (
    RouteGuardScorecard,
    build_dashboard,
    full_stop_criteria,
    load_metric_events,
    maintenance_criteria,
    next_mode,
    append_metric_event,
)


def test_dashboard_is_deterministic_and_reports_token_budget():
    score = RouteGuardScorecard(
        mode="learning",
        total_routed_turns_since_v2=150,
        routed_turns_window=100,
        fixture_total=100,
        fixture_passed=99,
        p0_p1_total=5,
        p0_p1_passed=5,
        false_positive_count_window=0,
        wrong_tool_first_call_count_window=0,
        user_correction_count_window=1,
        routejudge_latency_samples_ms=(900, 1000, 800),
        self_improvement_llm_calls_on_ordinary_success_window=0,
        thin_harness_status="pass",
        recovery_actionable_count_window=10,
        recovery_block_warn_count_window=10,
    )

    dashboard = build_dashboard(score)

    assert dashboard["mode"] == "learning"
    assert dashboard["token_budget_status"] == "ok"
    assert dashboard["fixture_accuracy_display"] == "99.0%"
    assert dashboard["self_improvement_llm_calls"]["ordinary_success_calls"] == 0
    assert "stop_progress" in dashboard


def test_maintenance_requires_usage_floor_and_zero_success_llm_calls():
    too_early = RouteGuardScorecard(
        total_routed_turns_since_v2=149,
        routed_turns_window=100,
        fixture_total=100,
        fixture_passed=100,
        p0_p1_total=1,
        p0_p1_passed=1,
        thin_harness_status="pass",
        recovery_actionable_count_window=1,
        recovery_block_warn_count_window=1,
    )
    assert not all(maintenance_criteria(too_early).values())

    ready = RouteGuardScorecard(
        total_routed_turns_since_v2=150,
        routed_turns_window=100,
        fixture_total=100,
        fixture_passed=100,
        p0_p1_total=1,
        p0_p1_passed=1,
        thin_harness_status="pass",
        recovery_actionable_count_window=1,
        recovery_block_warn_count_window=1,
    )
    assert all(maintenance_criteria(ready).values())
    assert next_mode(ready) == "maintenance"

    token_burn = RouteGuardScorecard(
        total_routed_turns_since_v2=150,
        routed_turns_window=100,
        fixture_total=100,
        fixture_passed=100,
        p0_p1_total=1,
        p0_p1_passed=1,
        self_improvement_llm_calls_on_ordinary_success_window=1,
        thin_harness_status="pass",
        recovery_actionable_count_window=1,
        recovery_block_warn_count_window=1,
    )
    assert not maintenance_criteria(token_burn)["ordinary_success_self_improvement_llm_calls_zero"]
    assert build_dashboard(token_burn)["token_budget_status"] == "exceeded"


def test_full_stop_is_usage_based_not_calendar_based():
    score = RouteGuardScorecard(
        mode="maintenance",
        total_routed_turns_since_v2=150,
        routed_turns_window=100,
        post_maintenance_routed_turns=100,
        fixture_total=100,
        fixture_passed=100,
        p0_p1_total=5,
        p0_p1_passed=5,
        p0_p1_incidents_window=0,
        false_positive_count_window=0,
        wrong_tool_first_call_count_window=0,
        user_correction_count_window=1,
        routejudge_latency_samples_ms=(800, 900, 1000),
        thin_harness_status="pass",
        recovery_actionable_count_window=10,
        recovery_block_warn_count_window=10,
    )

    criteria = full_stop_criteria(score)

    assert all(criteria.values())
    assert next_mode(score) == "full_stop"


def test_full_stop_fails_when_latency_or_correction_exceeds_strict_budget():
    score = RouteGuardScorecard(
        mode="maintenance",
        total_routed_turns_since_v2=150,
        routed_turns_window=100,
        post_maintenance_routed_turns=100,
        fixture_total=100,
        fixture_passed=100,
        p0_p1_total=5,
        p0_p1_passed=5,
        user_correction_count_window=2,
        routejudge_latency_samples_ms=(1010,),
        thin_harness_status="pass",
    )

    criteria = full_stop_criteria(score)

    assert not criteria["user_correction_rate_le_1_per_100"]
    assert not criteria["routejudge_p95_le_1_0s"]
    assert next_mode(score) == "maintenance"


def test_metric_jsonl_roundtrip_skips_corrupt_lines(tmp_path):
    path = tmp_path / "metrics.jsonl"
    append_metric_event(path, {"event_type": "routed_turn_finalized", "payload": {"ordinary_success": True}})
    path.write_text(path.read_text() + "not-json\n", encoding="utf-8")

    events = load_metric_events(path)

    assert events == [{"event_type": "routed_turn_finalized", "payload": {"ordinary_success": True}}]
