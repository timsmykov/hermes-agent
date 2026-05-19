"""Deterministic RouteGuard metrics aggregation and dashboard.

This module is deliberately model-free. It can be used on ordinary successful
turns without spending tokens. LLM-based synthesis belongs outside this path and
is disabled by default by policy.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

LearningMode = Literal["learning", "maintenance", "full_stop"]
Status = Literal["pass", "warning", "fail", "unknown"]


@dataclass(frozen=True)
class RouteGuardScorecard:
    mode: LearningMode = "learning"
    total_routed_turns_since_v2: int = 0
    routed_turns_window: int = 0
    post_maintenance_routed_turns: int = 0
    fixture_total: int = 0
    fixture_passed: int = 0
    p0_p1_total: int = 0
    p0_p1_passed: int = 0
    p0_p1_incidents_window: int = 0
    false_positive_count_window: int = 0
    wrong_tool_first_call_count_window: int = 0
    covered_routed_turns_window: int = 0
    user_correction_count_window: int = 0
    routejudge_latency_samples_ms: tuple[int, ...] = ()
    routejudge_uncached_calls_window: int = 0
    self_improvement_llm_calls_window: int = 0
    self_improvement_llm_calls_on_ordinary_success_window: int = 0
    self_improvement_synthesis_count_window: int = 0
    thin_harness_status: Status = "unknown"
    coverage_complete_enforce_policies: int = 0
    coverage_total_enforce_policies: int = 0
    recovery_actionable_count_window: int = 0
    recovery_block_warn_count_window: int = 0
    new_route_repeated_uncovered_classes_window: int = 0
    new_route_classes_last_50: dict[str, int] = field(default_factory=dict)
    last_promotion_at: str | None = None
    maintenance_entered_at: str | None = None
    maintenance_entered_turn_index: int | None = None
    tim_requested_continued_improvement: bool = False

    @property
    def fixture_accuracy(self) -> float:
        return _rate(self.fixture_passed, self.fixture_total)

    @property
    def p0_p1_regression_passed(self) -> bool:
        return self.p0_p1_total > 0 and self.p0_p1_passed == self.p0_p1_total

    @property
    def false_positive_rate(self) -> float:
        return _rate(self.false_positive_count_window, self.routed_turns_window)

    @property
    def wrong_tool_first_call_rate(self) -> float:
        return _rate(self.wrong_tool_first_call_count_window, self.covered_routed_turns_window or self.routed_turns_window)

    @property
    def user_correction_rate_per_100(self) -> float:
        return 100 * _rate(self.user_correction_count_window, self.routed_turns_window)

    @property
    def routejudge_p95_latency_s(self) -> float | None:
        percentile = _p95(self.routejudge_latency_samples_ms)
        return None if percentile is None else percentile / 1000.0

    @property
    def recovery_quality(self) -> float:
        if self.recovery_block_warn_count_window == 0:
            return 1.0
        return _rate(self.recovery_actionable_count_window, self.recovery_block_warn_count_window)

    @property
    def token_budget_status(self) -> str:
        if self.self_improvement_llm_calls_on_ordinary_success_window > 0:
            return "exceeded"
        if self.self_improvement_synthesis_count_window > max(1, math.ceil(max(1, self.routed_turns_window) / 25)):
            return "warning"
        return "ok"


def maintenance_criteria(score: RouteGuardScorecard) -> dict[str, bool]:
    return {
        "routed_turns_since_v2_ge_150": score.total_routed_turns_since_v2 >= 150,
        "fixture_accuracy_ge_98": score.fixture_accuracy >= 0.98,
        "p0_p1_regression_100": score.p0_p1_regression_passed,
        "false_positive_rate_le_1_percent": score.false_positive_rate <= 0.01,
        "wrong_tool_first_call_rate_le_1_percent": score.wrong_tool_first_call_rate <= 0.01,
        "user_correction_rate_le_2_per_100": score.user_correction_rate_per_100 <= 2.0,
        "no_uncovered_route_class_gt_3": score.new_route_repeated_uncovered_classes_window <= 3,
        "thin_harness_pass": score.thin_harness_status == "pass",
        "routejudge_p95_le_1_5s": score.routejudge_p95_latency_s is None or score.routejudge_p95_latency_s <= 1.5,
        "ordinary_success_self_improvement_llm_calls_zero": score.self_improvement_llm_calls_on_ordinary_success_window == 0,
        "recovery_quality_ge_95": score.recovery_quality >= 0.95,
    }


def full_stop_criteria(score: RouteGuardScorecard) -> dict[str, bool]:
    return {
        "current_mode_maintenance": score.mode == "maintenance",
        "post_maintenance_routed_turns_ge_100": score.post_maintenance_routed_turns >= 100,
        "total_routed_turns_since_v2_ge_150": score.total_routed_turns_since_v2 >= 150,
        "no_p0_p1_post_maintenance": score.p0_p1_incidents_window == 0,
        "false_positive_rate_le_0_5_percent_last_100": score.false_positive_rate <= 0.005,
        "wrong_tool_first_call_rate_le_0_5_percent_last_100": score.wrong_tool_first_call_rate <= 0.005,
        "user_correction_rate_le_1_per_100": score.user_correction_rate_per_100 <= 1.0,
        "no_new_route_class_ge_3_last_50": all(count < 3 for count in score.new_route_classes_last_50.values()),
        "routejudge_p95_le_1_0s": score.routejudge_p95_latency_s is None or score.routejudge_p95_latency_s <= 1.0,
        "ordinary_success_self_improvement_llm_calls_zero": score.self_improvement_llm_calls_on_ordinary_success_window == 0,
        "thin_harness_audit_pass": score.thin_harness_status == "pass",
        "tim_not_requesting_continued_improvement": not score.tim_requested_continued_improvement,
    }


def next_mode(score: RouteGuardScorecard) -> LearningMode:
    if all(full_stop_criteria(score).values()):
        return "full_stop"
    if all(maintenance_criteria(score).values()):
        return "maintenance"
    return "learning"


def build_dashboard(score: RouteGuardScorecard) -> dict[str, Any]:
    maintenance = maintenance_criteria(score)
    full_stop = full_stop_criteria(score)
    combined = full_stop if score.mode == "maintenance" else maintenance
    met = sum(1 for value in combined.values() if value)
    total = len(combined)
    missing = [key for key, value in combined.items() if not value]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": score.mode,
        "recommended_next_mode": next_mode(score),
        "routed_turns_window": score.routed_turns_window,
        "total_routed_turns_since_v2": score.total_routed_turns_since_v2,
        "post_maintenance_routed_turns": score.post_maintenance_routed_turns,
        "fixture_accuracy": score.fixture_accuracy,
        "fixture_accuracy_display": _percent(score.fixture_accuracy),
        "p0_p1_regression_pass": {"passed": score.p0_p1_passed, "total": score.p0_p1_total, "display": f"{score.p0_p1_passed}/{score.p0_p1_total}"},
        "false_positive_rate": score.false_positive_rate,
        "false_positive_rate_display": _percent(score.false_positive_rate),
        "wrong_tool_first_call_rate": score.wrong_tool_first_call_rate,
        "wrong_tool_first_call_rate_display": _percent(score.wrong_tool_first_call_rate),
        "user_correction_rate_per_100": score.user_correction_rate_per_100,
        "user_correction_rate_display": f"{score.user_correction_rate_per_100:.1f} per 100",
        "routejudge_p95_latency_s": score.routejudge_p95_latency_s,
        "routejudge_p95_latency_display": "n/a" if score.routejudge_p95_latency_s is None else f"{score.routejudge_p95_latency_s:.2f}s",
        "self_improvement_llm_calls": {
            "count": score.self_improvement_llm_calls_window,
            "window": "current_scorecard_window",
            "ordinary_success_calls": score.self_improvement_llm_calls_on_ordinary_success_window,
        },
        "token_budget_status": score.token_budget_status,
        "thin_harness_status": score.thin_harness_status,
        "coverage_status": {
            "complete": score.coverage_complete_enforce_policies,
            "total": score.coverage_total_enforce_policies,
            "display": f"{score.coverage_complete_enforce_policies}/{score.coverage_total_enforce_policies} enforce policies complete",
        },
        "recovery_quality": score.recovery_quality,
        "recovery_quality_display": _percent(score.recovery_quality),
        "new_route_pressure": {
            "repeated_uncovered_classes": score.new_route_repeated_uncovered_classes_window,
            "classes_last_50": score.new_route_classes_last_50,
        },
        "last_promotion": score.last_promotion_at,
        "stop_progress": {"criteria_met": met, "criteria_total": total, "display": f"{met}/{total} criteria met"},
        "next_stop_condition": missing[0] if missing else "all criteria met",
        "criteria": {"maintenance": maintenance, "full_stop": full_stop},
    }


def write_dashboard(path: str | Path, score: RouteGuardScorecard) -> dict[str, Any]:
    dashboard = build_dashboard(score)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dashboard


def append_metric_event(path: str | Path, event: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n")


def load_metric_events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def scorecard_to_json(score: RouteGuardScorecard) -> dict[str, Any]:
    data = asdict(score)
    data["fixture_accuracy"] = score.fixture_accuracy
    data["false_positive_rate"] = score.false_positive_rate
    data["wrong_tool_first_call_rate"] = score.wrong_tool_first_call_rate
    data["user_correction_rate_per_100"] = score.user_correction_rate_per_100
    data["routejudge_p95_latency_s"] = score.routejudge_p95_latency_s
    data["recovery_quality"] = score.recovery_quality
    data["token_budget_status"] = score.token_budget_status
    return data


def _rate(numerator: int, denominator: int) -> float:
    return numerator / max(1, denominator)


def _p95(samples: Iterable[int]) -> int | None:
    ordered = sorted(int(sample) for sample in samples)
    if not ordered:
        return None
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _percent(value: float) -> str:
    return f"{100 * value:.1f}%"
