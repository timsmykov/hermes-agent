"""Deterministic RouteGuard metrics aggregation and dashboard.

This module is deliberately model-free. It can be used on ordinary successful
turns without spending tokens. LLM-based synthesis belongs outside this path and
is disabled by default by policy.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

LearningMode = Literal["learning", "maintenance", "full_stop"]
Status = Literal["pass", "warning", "fail", "unknown"]


@dataclass(frozen=True)
class RouteGuardMetricsConfig:
    enabled: bool = True
    path: str = ".hermes/routeguard/metrics.jsonl"
    dashboard_path: str = ".hermes/routeguard/dashboard.json"
    incidents_dir: str = ".hermes/routeguard/incidents"
    window_size: int = 100
    scorecard_every_routed_turns: int = 25
    scorecard_every_incidents: int = 10
    dashboard_llm_summary: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RouteGuardMetricsConfig":
        defaults = cls()
        if not isinstance(data, Mapping):
            return defaults
        return cls(
            enabled=_as_bool(data.get("enabled"), defaults.enabled),
            path=str(data.get("path") or defaults.path),
            dashboard_path=str(data.get("dashboard_path") or defaults.dashboard_path),
            incidents_dir=str(data.get("incidents_dir") or defaults.incidents_dir),
            window_size=max(1, _as_int(data.get("window_size"), defaults.window_size)),
            scorecard_every_routed_turns=max(1, _as_int(data.get("scorecard_every_routed_turns"), defaults.scorecard_every_routed_turns)),
            scorecard_every_incidents=max(1, _as_int(data.get("scorecard_every_incidents"), defaults.scorecard_every_incidents)),
            dashboard_llm_summary=_as_bool(data.get("dashboard_llm_summary"), defaults.dashboard_llm_summary),
        )


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


def scorecard_from_events(
    events: Iterable[Mapping[str, Any]],
    *,
    window_size: int = 100,
    mode: LearningMode = "learning",
) -> RouteGuardScorecard:
    """Build a deterministic scorecard from compact runtime events.

    Events intentionally omit private user content and tool results. The scorecard
    only needs route/tool/action counters for launch-mode progress visibility.
    """
    normalized = [dict(event) for event in events if isinstance(event, Mapping)]
    route_turns = [event for event in normalized if event.get("event_type") == "route_detected"]
    decision_events = [event for event in normalized if event.get("event_type") == "route_decision"]
    window_decisions = decision_events[-max(1, int(window_size)):]
    routed_window = route_turns[-max(1, int(window_size)):]
    routejudge_latencies = []
    for event in normalized:
        sample = event.get("latency_ms")
        if event.get("event_type") == "route_judge" and isinstance(sample, int):
            routejudge_latencies.append(sample)
    wrong_tool = [
        event for event in window_decisions
        if event.get("code") in {"wrong_route_observed", "block_wrong_route"}
    ]
    blocked_or_warn = [event for event in window_decisions if event.get("action") in {"warn", "block"}]
    actionable = [event for event in blocked_or_warn if event.get("required_next_tools")]
    return RouteGuardScorecard(
        mode=mode,
        total_routed_turns_since_v2=len(route_turns),
        routed_turns_window=len(routed_window),
        covered_routed_turns_window=len(routed_window),
        wrong_tool_first_call_count_window=len(wrong_tool),
        routejudge_latency_samples_ms=tuple(routejudge_latencies[-max(1, int(window_size)):]),
        routejudge_uncached_calls_window=sum(1 for event in normalized[-max(1, int(window_size)):] if event.get("event_type") == "route_judge" and not event.get("cached")),
        recovery_actionable_count_window=len(actionable),
        recovery_block_warn_count_window=len(blocked_or_warn),
        thin_harness_status="pass",
    )


class RouteGuardMetricsSink:
    """Tiny deterministic sink for RouteGuard runtime metrics.

    It writes compact JSONL events and refreshes dashboard.json without any LLM
    calls. Failures are swallowed by callers so metrics never break tool dispatch.
    """

    def __init__(self, config: RouteGuardMetricsConfig | None = None):
        self.config = config or RouteGuardMetricsConfig()

    def emit(self, event_type: str, **payload: Any) -> None:
        if not self.config.enabled:
            return
        event = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **{k: v for k, v in payload.items() if v is not None},
        }
        append_metric_event(self.config.path, event)
        self._maybe_write_incident(event)
        events = load_metric_events(self.config.path)
        write_dashboard(
            self.config.dashboard_path,
            scorecard_from_events(events, window_size=self.config.window_size),
        )

    def _maybe_write_incident(self, event: Mapping[str, Any]) -> None:
        if event.get("event_type") != "route_decision":
            return
        action = str(event.get("action") or "")
        code = str(event.get("code") or "")
        if action not in {"warn", "block"} and code != "wrong_route_observed":
            return
        incidents_dir = Path(self.config.incidents_dir)
        incidents_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        route = _slug(str(event.get("route") or "unknown"))
        filename = f"{stamp}-{route}-{_slug(code or action)}.json"
        incident = {
            "schema_version": 1,
            "severity": "P2" if action != "block" else "P1",
            "status": "captured",
            "source": "route_guard",
            "captured_at": event.get("timestamp"),
            "route": event.get("route"),
            "action": action,
            "code": code,
            "tool": event.get("tool"),
            "tool_class": event.get("tool_class"),
            "reason": event.get("reason"),
            "required_next_tools": event.get("required_next_tools") or [],
            "escalation_allowed_if": event.get("escalation_allowed_if") or [],
            "raw_user_text_stored": False,
            "promotion": {
                "fixture_required": True,
                "auto_promote": False,
                "review_required": True,
            },
        }
        (incidents_dir / filename).write_text(json.dumps(incident, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "unknown"


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
