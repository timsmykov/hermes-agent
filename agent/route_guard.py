"""Executable route policy guardrails for wrong-tool routing.

This module is intentionally small and side-effect free. Runtime code owns where
and how decisions become synthetic tool results, warnings, or trace logs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from agent.route_judge import (
    RouteJudgeConfig,
    RouteJudgeEnvelope,
    RouteJudgeRaw,
    ToolClass,
    parse_route_judge_json,
    parse_route_judge_mapping,
    validate_route_judge,
)
from agent.route_policies import BUILTIN_ROUTE_POLICIES, NOTION_PAGE_ANALYSIS_POLICY, ROUTE_NOTION_PAGE_ANALYSIS
from agent.route_guard_metrics import RouteGuardMetricsConfig, RouteGuardMetricsSink


_ROUTE_NOTION_PAGE_ANALYSIS = ROUTE_NOTION_PAGE_ANALYSIS


@dataclass(frozen=True)
class RouteGuardConfig:
    enabled: bool = False
    mode: str = "observe"  # observe | warn | enforce
    trace_enabled: bool = True
    judge: RouteJudgeConfig = field(default_factory=RouteJudgeConfig)
    metrics: RouteGuardMetricsConfig = field(default_factory=RouteGuardMetricsConfig)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RouteGuardConfig":
        defaults = cls()
        if not isinstance(data, Mapping):
            return defaults
        return cls(
            enabled=_as_bool(data.get("enabled"), defaults.enabled),
            mode=_mode(data.get("mode"), defaults.mode),
            trace_enabled=_as_bool(data.get("trace_enabled"), defaults.trace_enabled),
            judge=RouteJudgeConfig.from_mapping(data.get("judge") if isinstance(data.get("judge"), Mapping) else None),
            metrics=RouteGuardMetricsConfig.from_mapping(data.get("metrics") if isinstance(data.get("metrics"), Mapping) else None),
        )


@dataclass(frozen=True)
class RouteGuardDecision:
    action: str = "allow"  # allow | observe | warn | block
    code: str = "allow"
    route: str = ""
    tool_name: str = ""
    tool_class: str = "other"
    reason: str = ""
    required_next_tools: tuple[str, ...] = ()
    escalation_allowed_if: tuple[str, ...] = ()

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "observe", "warn"}

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "code": self.code,
            "route": self.route,
            "tool": self.tool_name,
            "tool_class": self.tool_class,
            "reason": self.reason,
            "required_next_tools": list(self.required_next_tools),
            "escalation_allowed_if": list(self.escalation_allowed_if),
        }


@dataclass
class RouteGuardState:
    route: str = ""
    user_explicit_browser: bool = False
    notion_api_attempted: bool = False
    notion_api_failed: bool = False
    notion_content_available: bool = False
    blocked_count: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    judge_envelope: RouteJudgeEnvelope = field(default_factory=RouteJudgeEnvelope.not_called)


class RouteGuardController:
    """Per-turn route policy controller.

    MVP scope: Notion page-analysis tasks must use Notion API/MCP before browser,
    generic web, or shell/delegation/cron bypass paths.
    """

    def __init__(
        self,
        config: RouteGuardConfig | None = None,
        *,
        route_judge: Callable[[str], str | Mapping[str, Any] | RouteJudgeRaw | RouteJudgeEnvelope] | None = None,
    ):
        self.config = config or RouteGuardConfig()
        self.state = RouteGuardState()
        self._route_judge = route_judge
        self._judge_cache: dict[str, RouteJudgeEnvelope] = {}
        self._judge_calls_this_turn = 0
        self._metrics = RouteGuardMetricsSink(self.config.metrics)

    def reset_for_turn(self, user_message: str | None = None) -> None:
        text = user_message or ""
        route = _detect_route(text)
        judge_envelope = RouteJudgeEnvelope.not_called()
        self._judge_calls_this_turn = 0
        if not route:
            judge_envelope = self._judge_for_turn(text)
            if judge_envelope.accepted:
                route = judge_envelope.route
        self.state = RouteGuardState(
            route=route,
            user_explicit_browser=_user_explicitly_requested_browser(text),
            judge_envelope=judge_envelope,
        )
        if self.config.enabled and route:
            self._emit_metric(
                "route_detected",
                route=route,
                judge_status=judge_envelope.status,
                judge_accepted=judge_envelope.accepted,
            )
            if judge_envelope.status != "not_called":
                self._emit_metric(
                    "route_judge",
                    route=judge_envelope.route,
                    status=judge_envelope.status,
                    accepted=judge_envelope.accepted,
                    confidence=judge_envelope.confidence,
                    latency_ms=judge_envelope.latency_ms,
                    cached=judge_envelope.cached,
                )

    @property
    def active_route(self) -> str:
        return self.state.route

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> RouteGuardDecision:
        args = args if isinstance(args, Mapping) else {}
        tool_class = classify_tool(tool_name, args)
        if not self.config.enabled or self.state.route != _ROUTE_NOTION_PAGE_ANALYSIS:
            return RouteGuardDecision(tool_name=tool_name, tool_class=tool_class)

        decision = self._notion_before_call(tool_name, args, tool_class)
        self._trace(decision)
        self._emit_metric("route_decision", **decision.to_metadata())
        return decision

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool | None = None,
    ) -> None:
        if not self.config.enabled or self.state.route != _ROUTE_NOTION_PAGE_ANALYSIS:
            return
        tool_class = classify_tool(tool_name, args if isinstance(args, Mapping) else {})
        if tool_class == "notion_api":
            self.state.notion_api_attempted = True
            if failed is True or _looks_like_error(result):
                self.state.notion_api_failed = True
            else:
                self.state.notion_content_available = True
        self._emit_metric(
            "tool_result",
            route=self.state.route,
            tool_name=tool_name,
            tool_class=tool_class,
            failed=bool(failed),
            notion_api_attempted=self.state.notion_api_attempted,
            notion_api_failed=self.state.notion_api_failed,
            notion_content_available=self.state.notion_content_available,
        )

    def _judge_for_turn(self, text: str) -> RouteJudgeEnvelope:
        if not self.config.enabled or not self.config.judge.enabled or self._route_judge is None or not text.strip():
            return RouteJudgeEnvelope.not_called()
        key = _normalize_judge_cache_key(text)
        if self.config.judge.cache_enabled and key in self._judge_cache:
            cached = self._judge_cache[key]
            return RouteJudgeEnvelope(
                status=cached.status,
                accepted=cached.accepted,
                route=cached.route,
                confidence=cached.confidence,
                intent_summary=cached.intent_summary,
                primary_tool_classes=cached.primary_tool_classes,
                required_next_tools=cached.required_next_tools,
                blocked_before_primary=cached.blocked_before_primary,
                escalation_reason=cached.escalation_reason,
                rationale=cached.rationale,
                validation_errors=cached.validation_errors,
                latency_ms=cached.latency_ms,
                cached=True,
            )
        if self._judge_calls_this_turn >= self.config.judge.max_calls_per_turn:
            return RouteJudgeEnvelope.not_called()
        self._judge_calls_this_turn += 1
        try:
            raw_output = self._route_judge(text)
            if isinstance(raw_output, RouteJudgeEnvelope):
                envelope = validate_route_judge(raw_output, policies=BUILTIN_ROUTE_POLICIES)
            elif isinstance(raw_output, RouteJudgeRaw):
                envelope = validate_route_judge(raw_output, policies=BUILTIN_ROUTE_POLICIES)
            elif isinstance(raw_output, str):
                envelope = validate_route_judge(parse_route_judge_json(raw_output), policies=BUILTIN_ROUTE_POLICIES)
            elif isinstance(raw_output, Mapping):
                envelope = validate_route_judge(parse_route_judge_mapping(raw_output), policies=BUILTIN_ROUTE_POLICIES)
            else:
                envelope = RouteJudgeEnvelope.failure("provider_error", "judge returned unsupported type")
        except TimeoutError as exc:
            envelope = RouteJudgeEnvelope.failure("timeout", str(exc))
        except Exception as exc:  # noqa: BLE001 - provider boundary
            envelope = RouteJudgeEnvelope.failure("provider_error", str(exc))
        if self.config.judge.cache_enabled:
            self._judge_cache[key] = envelope
        return envelope

    def _notion_before_call(
        self, tool_name: str, args: Mapping[str, Any], tool_class: str
    ) -> RouteGuardDecision:
        if tool_class == "notion_api":
            return RouteGuardDecision(tool_name=tool_name, tool_class=tool_class, route=self.state.route)

        # Explicit browser/UI request is a valid escalation from the start.
        if self.state.user_explicit_browser and tool_class in {"browser_ui", "generic_web", "shell_escape"}:
            return RouteGuardDecision(
                action="allow",
                code="user_explicitly_requested_browser",
                route=self.state.route,
                tool_name=tool_name,
                tool_class=tool_class,
                reason="The user explicitly requested browser/UI access.",
            )

        # After a real Notion API failure, browser fallback is allowed in warn/observe
        # and in enforce mode for browser_ui only. Generic web remains blocked for
        # private Notion content.
        if self.state.notion_api_attempted and self.state.notion_api_failed and tool_class == "browser_ui":
            return RouteGuardDecision(
                action="allow",
                code="api_access_denied_after_health_ok",
                route=self.state.route,
                tool_name=tool_name,
                tool_class=tool_class,
                reason="Notion API path was attempted and failed; browser UI fallback is allowed.",
            )

        if tool_class in {"browser_ui", "generic_web"}:
            return self._blocked_or_warn(
                tool_name,
                tool_class,
                "Ordinary Notion page analysis must use Notion MCP/API before browser or generic web fallback.",
            )

        if tool_class == "shell_escape" and _shell_bypasses_notion_policy(tool_name, args):
            return self._blocked_or_warn(
                tool_name,
                tool_class,
                "This command/code appears to access Notion or browser automation outside the Notion API route.",
            )

        if tool_class == "delegation" and _delegation_bypasses_notion_policy(args):
            return self._blocked_or_warn(
                tool_name,
                tool_class,
                "Delegated agents for Notion page analysis must not be given browser/web bypass tools before Notion API is tried.",
            )

        if tool_class == "scheduling" and _cron_bypasses_notion_policy(args):
            return self._blocked_or_warn(
                tool_name,
                tool_class,
                "Scheduled/background Notion page analysis must preserve the Notion API-first policy.",
            )

        return RouteGuardDecision(tool_name=tool_name, tool_class=tool_class, route=self.state.route)

    def _blocked_or_warn(self, tool_name: str, tool_class: str, reason: str) -> RouteGuardDecision:
        action = "block" if self.config.mode == "enforce" else ("warn" if self.config.mode == "warn" else "observe")
        if action == "block":
            self.state.blocked_count += 1
        policy = NOTION_PAGE_ANALYSIS_POLICY
        return RouteGuardDecision(
            action=action,
            code="block_wrong_route" if action == "block" else "wrong_route_observed",
            route=self.state.route,
            tool_name=tool_name,
            tool_class=tool_class,
            reason=reason,
            required_next_tools=policy.required_next_tools,
            escalation_allowed_if=policy.allowed_escalations,
        )

    def _trace(self, decision: RouteGuardDecision) -> None:
        if self.config.trace_enabled and decision.route:
            self.state.trace.append(decision.to_metadata())

    def _emit_metric(self, event_type: str, **payload: Any) -> None:
        try:
            self._metrics.emit(event_type, **payload)
        except Exception:
            # Metrics are observability only; never break routing/tool dispatch.
            return


def classify_tool(tool_name: str, args: Mapping[str, Any] | None = None) -> str:
    name = (tool_name or "").strip()
    args = args if isinstance(args, Mapping) else {}
    if name.startswith("mcp_notion_") or name.startswith("notion_"):
        return ToolClass.NOTION_API.value
    if name.startswith("mcp_browser_use_") or name.startswith("browser_"):
        return ToolClass.BROWSER_UI.value
    if name in {"web_search", "web_extract"}:
        return ToolClass.GENERIC_WEB.value
    if name in {"terminal", "execute_code"}:
        return ToolClass.SHELL_ESCAPE.value
    if name == "delegate_task":
        return ToolClass.DELEGATION.value
    if name == "cronjob":
        return ToolClass.SCHEDULING.value
    if name in {"read_file", "write_file", "patch", "search_files"}:
        return ToolClass.FILE_ARTIFACT.value
    if name == "image_generate":
        return ToolClass.IMAGE_GENERATION.value
    if name.startswith("mcp_gbrain_knowledge_"):
        return ToolClass.GBRAIN.value
    return ToolClass.OTHER.value


def routeguard_synthetic_result(decision: RouteGuardDecision) -> str:
    return json.dumps(
        {
            "success": False,
            "blocked_by": "route_guard",
            "decision": decision.code,
            "route": decision.route,
            "tool": decision.tool_name,
            "tool_class": decision.tool_class,
            "reason": decision.reason,
            "required_next_tools": list(decision.required_next_tools),
            "escalation_allowed_if": list(decision.escalation_allowed_if),
        },
        ensure_ascii=False,
    )


def append_routeguard_guidance(result: str, decision: RouteGuardDecision) -> str:
    """Return compact LLM-visible warning only when warn-mode explicitly asks.

    Observe-mode decisions are trace-only to avoid token burn on ordinary turns.
    Enforce-mode blocks already return a compact synthetic result.
    """
    if decision.action != "warn" or not decision.reason:
        return result
    suffix = (
        f"\n\n[RouteGuard warn: {decision.code}; route={decision.route}; "
        f"use={','.join(decision.required_next_tools[:2]) or 'primary route'}]"
    )
    return (result or "") + suffix


def _normalize_judge_cache_key(text: str) -> str:
    return " ".join((text or "").strip().lower().split())[:2000]


def _detect_route(text: str) -> str:
    if not text:
        return ""
    lower = text.lower()
    has_notion_url = "notion.so" in lower or "notion.site" in lower
    analysis_terms = (
        "анализ", "проанализ", "транскрип", "roadmap", "роудмап", "html",
        "summary", "summar", "action item", "резюм", "сводк", "артефакт",
        "страниц", "page", "созвон", "meeting", "transcript", "прочитай",
        "read", "extract", "вытащи", "извлеки", "документ", "document", "doc",
        "задач", "tasks",
    )
    if has_notion_url and any(term in lower for term in analysis_terms):
        return _ROUTE_NOTION_PAGE_ANALYSIS
    return ""


def _user_explicitly_requested_browser(text: str) -> bool:
    lower = (text or "").lower()
    explicit = (
        "через браузер", "в браузере", "открой браузер", "browser", "browser_use",
        "ui-only", "через ui", "интерфейс notion", "notion ai",
    )
    return any(term in lower for term in explicit)


def _shell_bypasses_notion_policy(tool_name: str, args: Mapping[str, Any]) -> bool:
    haystack = ""
    if tool_name == "terminal":
        haystack = str(args.get("command") or "")
    elif tool_name == "execute_code":
        haystack = str(args.get("code") or "")
    lower = haystack.lower()
    if not lower:
        return False
    forbidden = (
        "notion.so", "notion.site", "chrome", "chromium", "google-chrome",
        "playwright", "selenium", "browser-use", "webbrowser.open", "xdg-open",
    )
    # Official API/CLI route is allowed.
    allowed = ("api.notion.com" in lower) or ("ntn-hermes" in lower)
    return (not allowed) and any(term in lower for term in forbidden)


def _delegation_bypasses_notion_policy(args: Mapping[str, Any]) -> bool:
    blob = json.dumps(args, ensure_ascii=False, default=str).lower()
    return ("notion.so" in blob or "notion.site" in blob) and any(
        term in blob for term in ("browser", "web", "mcp_browser_use", "web_extract", "web_search")
    )


def _cron_bypasses_notion_policy(args: Mapping[str, Any]) -> bool:
    blob = json.dumps(args, ensure_ascii=False, default=str).lower()
    return ("notion.so" in blob or "notion.site" in blob) and any(
        term in blob for term in ("browser", "web_extract", "web_search", "mcp_browser_use")
    )


def _looks_like_error(result: str | None) -> bool:
    if not result:
        return False
    lower = str(result)[:1000].lower()
    return "\"error\"" in lower or "permission" in lower or "unauthorized" in lower or lower.startswith("error")


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


def _mode(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip().lower() in {"observe", "warn", "enforce"}:
        return value.strip().lower()
    return default
