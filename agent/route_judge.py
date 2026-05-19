"""Structured RouteJudge envelope for hybrid RouteGuard routing.

The LLM may suggest semantic route intent, but this module validates every
suggestion against deterministic route contracts before RouteGuard uses it.
No live model calls live here; tests exercise pure parsing/validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence


class ToolClass(StrEnum):
    NOTION_API = "notion_api"
    BROWSER_UI = "browser_ui"
    GENERIC_WEB = "generic_web"
    SHELL_ESCAPE = "shell_escape"
    DELEGATION = "delegation"
    SCHEDULING = "scheduling"
    FILE_ARTIFACT = "file_artifact"
    IMAGE_GENERATION = "image_generation"
    GBRAIN = "gbrain"
    OTHER = "other"


class EscalationReason(StrEnum):
    UI_ONLY_REQUESTED = "ui_only_requested"
    NOTION_AI_REQUESTED = "notion_ai_requested"
    PERMISSION_OR_SHARE_UI_REQUESTED = "permission_or_share_ui_requested"
    API_ACCESS_DENIED_AFTER_HEALTH_OK = "api_access_denied_after_health_ok"
    API_ENDPOINT_UNSUPPORTED = "api_endpoint_unsupported"
    COMMENTS_API_EMPTY_BUT_COMMENTS_EXPECTED = "comments_api_empty_but_comments_expected"
    USER_EXPLICITLY_REQUESTED_BROWSER = "user_explicitly_requested_browser"


class RouteJudgeStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_CALLED = "not_called"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_INVALID = "schema_invalid"
    LOW_CONFIDENCE = "low_confidence"
    UNKNOWN_ROUTE = "unknown_route"
    UNAUTHORIZED_TOOL_CLASS = "unauthorized_tool_class"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    UNAUTHORIZED_ESCALATION = "unauthorized_escalation"
    CONFLICTS_WITH_DETERMINISTIC_ROUTE = "conflicts_with_deterministic_route"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class RouteJudgeConfig:
    enabled: bool = False
    mode: str = "observe"
    confidence_threshold: float = 0.80
    timeout_ms: int = 1200
    max_calls_per_turn: int = 1
    cache_enabled: bool = True
    self_improvement_enabled: bool = False
    self_improvement_llm_on_success: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RouteJudgeConfig":
        defaults = cls()
        if not isinstance(data, Mapping):
            return defaults
        return cls(
            enabled=_as_bool(data.get("enabled"), defaults.enabled),
            mode=_mode(data.get("mode"), defaults.mode),
            confidence_threshold=_as_float(data.get("confidence_threshold"), defaults.confidence_threshold),
            timeout_ms=max(100, _as_int(data.get("timeout_ms"), defaults.timeout_ms)),
            max_calls_per_turn=max(0, _as_int(data.get("max_calls_per_turn"), defaults.max_calls_per_turn)),
            cache_enabled=_as_bool(data.get("cache_enabled"), defaults.cache_enabled),
            self_improvement_enabled=_as_bool(data.get("self_improvement_enabled"), defaults.self_improvement_enabled),
            self_improvement_llm_on_success=_as_bool(
                data.get("self_improvement_llm_on_success"), defaults.self_improvement_llm_on_success
            ),
        )


@dataclass(frozen=True)
class RoutePolicy:
    route: str
    primary_tool_classes: tuple[str, ...]
    required_next_tools: tuple[str, ...]
    blocked_before_primary: tuple[str, ...]
    allowed_escalations: tuple[str, ...]
    confidence_threshold: float = 0.80
    enforceable: bool = False


@dataclass(frozen=True)
class RouteJudgeRaw:
    route: str
    confidence: float
    intent_summary: str = ""
    primary_tool_classes: tuple[str, ...] = ()
    required_next_tools: tuple[str, ...] = ()
    blocked_before_primary: tuple[str, ...] = ()
    escalation_reason: str | None = None
    rationale: str = ""


@dataclass(frozen=True)
class RouteJudgeEnvelope:
    status: str
    accepted: bool
    route: str = "unknown"
    confidence: float = 0.0
    intent_summary: str = ""
    primary_tool_classes: tuple[str, ...] = ()
    required_next_tools: tuple[str, ...] = ()
    blocked_before_primary: tuple[str, ...] = ()
    escalation_reason: str | None = None
    rationale: str = ""
    validation_errors: tuple[str, ...] = ()
    latency_ms: int | None = None
    cached: bool = False

    @classmethod
    def not_called(cls) -> "RouteJudgeEnvelope":
        return cls(status=RouteJudgeStatus.NOT_CALLED.value, accepted=False)

    @classmethod
    def failure(cls, status: RouteJudgeStatus | str, *errors: str) -> "RouteJudgeEnvelope":
        value = status.value if isinstance(status, RouteJudgeStatus) else str(status)
        return cls(status=value, accepted=False, validation_errors=tuple(e for e in errors if e))


def parse_route_judge_json(text: str) -> RouteJudgeRaw | RouteJudgeEnvelope:
    """Parse model JSON into RouteJudgeRaw, or return a failure envelope."""
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - parser boundary
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.MALFORMED_JSON, str(exc))
    if not isinstance(data, Mapping):
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.SCHEMA_INVALID, "judge output must be a JSON object")
    return parse_route_judge_mapping(data)


def parse_route_judge_mapping(data: Mapping[str, Any]) -> RouteJudgeRaw | RouteJudgeEnvelope:
    route = data.get("route")
    confidence = data.get("confidence")
    if not isinstance(route, str) or not route.strip():
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.SCHEMA_INVALID, "route must be a non-empty string")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.SCHEMA_INVALID, "confidence must be a number")
    if confidence < 0 or confidence > 1:
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.SCHEMA_INVALID, "confidence must be between 0 and 1")

    fields = {}
    for key in ("primary_tool_classes", "required_next_tools", "blocked_before_primary"):
        value = data.get(key, ())
        if value is None:
            value = ()
        if not _is_str_sequence(value):
            return RouteJudgeEnvelope.failure(RouteJudgeStatus.SCHEMA_INVALID, f"{key} must be a string array")
        fields[key] = tuple(str(v) for v in value)

    escalation_reason = data.get("escalation_reason")
    if escalation_reason is not None and not isinstance(escalation_reason, str):
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.SCHEMA_INVALID, "escalation_reason must be string or null")

    intent_summary = _short_text(data.get("intent_summary", ""), 240)
    rationale = _short_text(data.get("rationale", ""), 240)
    return RouteJudgeRaw(
        route=route.strip(),
        confidence=float(confidence),
        intent_summary=intent_summary,
        primary_tool_classes=fields["primary_tool_classes"][:6],
        required_next_tools=fields["required_next_tools"][:8],
        blocked_before_primary=fields["blocked_before_primary"][:8],
        escalation_reason=escalation_reason.strip() if isinstance(escalation_reason, str) else None,
        rationale=rationale,
    )


def validate_route_judge(
    raw: RouteJudgeRaw | RouteJudgeEnvelope,
    *,
    policies: Mapping[str, RoutePolicy],
    available_tools: Sequence[str] = (),
    deterministic_route_hint: str = "",
) -> RouteJudgeEnvelope:
    """Validate a raw LLM route judgment against deterministic policy contracts."""
    if isinstance(raw, RouteJudgeEnvelope):
        return raw

    if raw.route == "unknown":
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNKNOWN_ROUTE, "route is unknown")
    policy = policies.get(raw.route)
    if policy is None:
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNKNOWN_ROUTE, f"unknown route: {raw.route}")
    if deterministic_route_hint and deterministic_route_hint != raw.route:
        return RouteJudgeEnvelope.failure(
            RouteJudgeStatus.CONFLICTS_WITH_DETERMINISTIC_ROUTE,
            f"deterministic route {deterministic_route_hint} conflicts with judge route {raw.route}",
        )

    threshold = max(policy.confidence_threshold, 0.0)
    if raw.confidence < threshold:
        return RouteJudgeEnvelope.failure(
            RouteJudgeStatus.LOW_CONFIDENCE,
            f"confidence {raw.confidence:.2f} below threshold {threshold:.2f}",
        )

    known_classes = {item.value for item in ToolClass}
    for item in raw.primary_tool_classes + raw.blocked_before_primary:
        if item not in known_classes:
            return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNAUTHORIZED_TOOL_CLASS, f"unknown tool class: {item}")

    if not set(raw.primary_tool_classes).issubset(set(policy.primary_tool_classes)):
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNAUTHORIZED_TOOL_CLASS, "primary tool class outside route policy")
    if not set(raw.blocked_before_primary).issubset(set(policy.blocked_before_primary)):
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNAUTHORIZED_TOOL_CLASS, "blocked class outside route policy")

    if not set(raw.required_next_tools).issubset(set(policy.required_next_tools)):
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNAUTHORIZED_TOOL, "required tool outside route policy")
    if available_tools and not set(raw.required_next_tools).issubset(set(available_tools)):
        return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNAUTHORIZED_TOOL, "required tool unavailable")

    if raw.escalation_reason:
        known_escalations = {item.value for item in EscalationReason}
        if raw.escalation_reason not in known_escalations:
            return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNAUTHORIZED_ESCALATION, "unknown escalation reason")
        if raw.escalation_reason not in set(policy.allowed_escalations):
            return RouteJudgeEnvelope.failure(RouteJudgeStatus.UNAUTHORIZED_ESCALATION, "escalation outside route policy")

    return RouteJudgeEnvelope(
        status=RouteJudgeStatus.ACCEPTED.value,
        accepted=True,
        route=raw.route,
        confidence=raw.confidence,
        intent_summary=raw.intent_summary,
        primary_tool_classes=raw.primary_tool_classes,
        required_next_tools=raw.required_next_tools,
        blocked_before_primary=raw.blocked_before_primary,
        escalation_reason=raw.escalation_reason,
        rationale=raw.rationale,
    )


def _is_str_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)


def _short_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


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
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except Exception:  # noqa: BLE001
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except Exception:  # noqa: BLE001
        return default


def _mode(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip().lower() in {"observe", "warn", "enforce"}:
        return value.strip().lower()
    return default
