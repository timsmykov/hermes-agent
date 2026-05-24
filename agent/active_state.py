"""Per-session active state storage for Hermes Infinite Session Engine."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.session_scope import SessionScope


_LOW_SIGNAL_USER_PREFIXES = (
    "ok",
    "ок",
    "спасибо",
    "thanks",
    "ага",
    "понял",
)
_STATUS_QUESTION_RE = re.compile(
    r"\b(статус|status|как дела|what'?s up|что по|как там|progress|прогресс)\b",
    re.IGNORECASE,
)
_COMPLETION_RE = re.compile(
    r"\b(done|completed|implemented)\b|\b(готово|завершено|исправлено)\b",
    re.IGNORECASE,
)
_NEGATED_COMPLETION_RE = re.compile(
    r"\b(not|isn'?t|wasn'?t|не|ещ[её] не|пока не)\s+\b(done|completed|implemented|готово|завершено|исправлено)\b",
    re.IGNORECASE,
)


def _looks_like_task_text(text: str) -> bool:
    """Conservatively decide whether a user turn is an active task."""
    normalized = " ".join((text or "").strip().split())
    if len(normalized) < 8:
        return False
    lowered = normalized.lower()
    if any(lowered == marker or lowered.startswith(marker + " ") for marker in _LOW_SIGNAL_USER_PREFIXES):
        return False
    if _STATUS_QUESTION_RE.search(lowered) and ("?" in normalized or lowered.startswith(("что", "как", "where", "what"))):
        return False
    return True


def _assistant_completion_status(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    if _NEGATED_COMPLETION_RE.search(lowered):
        return None
    if _COMPLETION_RE.search(lowered):
        return "completed"
    return None


@dataclass
class ActiveSessionState:
    """Materialized working state for one isolated session lane."""

    scope: SessionScope
    current_task: Optional[Dict[str, Any]] = None
    latest_user_request: Optional[Dict[str, Any]] = None
    active_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    unresolved_asks: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    running_processes: List[Dict[str, Any]] = field(default_factory=list)
    files_touched: List[Dict[str, Any]] = field(default_factory=list)
    route_traces: List[Dict[str, Any]] = field(default_factory=list)
    writeback_decisions: List[Dict[str, Any]] = field(default_factory=list)
    handoff: Optional[Dict[str, Any]] = None
    updated_at: Optional[float] = None
    version: int = 1

    @classmethod
    def empty(cls, scope: SessionScope) -> "ActiveSessionState":
        return cls(scope=scope, updated_at=time.time())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActiveSessionState":
        scope = SessionScope.from_dict(data.get("scope") or {})
        return cls(
            scope=scope,
            current_task=data.get("current_task"),
            latest_user_request=data.get("latest_user_request"),
            active_artifacts=list(data.get("active_artifacts") or []),
            unresolved_asks=list(data.get("unresolved_asks") or []),
            constraints=list(data.get("constraints") or []),
            running_processes=list(data.get("running_processes") or []),
            files_touched=list(data.get("files_touched") or []),
            route_traces=list(data.get("route_traces") or []),
            writeback_decisions=list(data.get("writeback_decisions") or []),
            handoff=data.get("handoff"),
            updated_at=data.get("updated_at"),
            version=int(data.get("version") or 1),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "scope": self.scope.to_dict(),
            "current_task": self.current_task,
            "latest_user_request": self.latest_user_request,
            "active_artifacts": self.active_artifacts,
            "unresolved_asks": self.unresolved_asks,
            "constraints": self.constraints,
            "running_processes": self.running_processes,
            "files_touched": self.files_touched,
            "route_traces": self.route_traces,
            "writeback_decisions": self.writeback_decisions,
            "handoff": self.handoff,
            "updated_at": self.updated_at,
        }

    def render_for_prompt(self, *, max_artifacts: int = 5) -> str:
        """Render compact prompt context for the current scope only."""
        lines = ["## Active Session State", f"scope: {self.scope.scope_key}"]
        if self.current_task:
            lines.append(f"current_task: {self.current_task.get('text') or self.current_task}")
        if self.latest_user_request:
            lines.append(f"latest_user_request: {self.latest_user_request.get('text') or self.latest_user_request}")
        if self.active_artifacts:
            lines.append("active_artifacts:")
            for artifact in self.active_artifacts[:max_artifacts]:
                title = artifact.get("title") or artifact.get("local_path") or artifact.get("uri") or artifact.get("artifact_id")
                lines.append(f"- {artifact.get('kind', 'artifact')}: {title}")
        if self.unresolved_asks:
            lines.append("unresolved_asks:")
            for ask in self.unresolved_asks[:5]:
                lines.append(f"- {ask.get('text') or ask}")
        if self.route_traces:
            latest = self.route_traces[0]
            lines.append(
                "latest_route_trace: "
                f"expected={latest.get('expected_first_tool_family') or 'none'} "
                f"actual={latest.get('actual_first_tool') or 'pending'} "
                f"status={latest.get('compliance') or 'pending'}"
            )
        return "\n".join(lines)


class ActiveStateStore:
    """Persistence adapter backed by SessionDB active-state tables."""

    def __init__(self, session_db: Any):
        self.session_db = session_db

    def get(self, scope: SessionScope) -> ActiveSessionState:
        data = self.session_db.get_active_session_state(scope.scope_key)
        if not data:
            return ActiveSessionState.empty(scope)
        state = ActiveSessionState.from_dict(data)
        # Keep caller-provided session_id/current scope fresh even if the
        # persisted materialized state was created by an earlier continuation.
        if state.scope.scope_key != scope.scope_key or state.scope.session_id != scope.session_id:
            state.scope = scope
        return state

    def save(self, state: ActiveSessionState, *, event_type: str = "save") -> ActiveSessionState:
        state.updated_at = time.time()
        self.session_db.set_active_session_state(
            state.scope.scope_key,
            state.to_dict(),
            session_id=state.scope.session_id,
            lineage_id=state.scope.lineage_id,
            event_type=event_type,
        )
        return state

    def update_latest_user_request(
        self,
        scope: SessionScope,
        *,
        text: str,
        message_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> ActiveSessionState:
        state = self.get(scope)
        state.latest_user_request = {
            "text": text,
            "message_id": message_id,
            "timestamp": timestamp or time.time(),
        }
        current_status = (state.current_task or {}).get("status")
        if _looks_like_task_text(text) and (not state.current_task or current_status == "completed"):
            state.current_task = {
                "text": text,
                "status": "in_progress",
                "source_message_id": message_id,
                "updated_at": timestamp or time.time(),
            }
        return self.save(state, event_type="latest_user_request")

    def update_current_task_from_assistant(
        self,
        scope: SessionScope,
        *,
        text: str,
        message_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> ActiveSessionState:
        """Update active task status from final assistant output only."""
        state = self.get(scope)
        if not state.current_task:
            return state
        status = _assistant_completion_status(text)
        state.current_task = dict(state.current_task)
        if status:
            state.current_task["status"] = status
            state.current_task["completed_message_id"] = message_id
            state.current_task["completed_at"] = timestamp or time.time()
            return self.save(state, event_type="current_task_completed")
        state.current_task["last_assistant_message_id"] = message_id
        state.current_task["last_assistant_at"] = timestamp or time.time()
        return self.save(state, event_type="current_task_observed")

    def register_artifact(self, scope: SessionScope, artifact: Dict[str, Any]) -> ActiveSessionState:
        state = self.get(scope)
        artifact = dict(artifact)
        artifact.setdefault("status", "active")
        artifact.setdefault("owner_scope", scope.scope_key)
        artifact.setdefault("created_at", time.time())
        artifact.setdefault("updated_at", time.time())
        existing = [a for a in state.active_artifacts if a.get("artifact_id") != artifact.get("artifact_id")]
        state.active_artifacts = [artifact, *existing]
        return self.save(state, event_type="artifact_registered")

    def mark_artifact_status(
        self,
        scope: SessionScope,
        artifact_id: str,
        status: str,
        *,
        reason: Optional[str] = None,
    ) -> ActiveSessionState:
        state = self.get(scope)
        now = time.time()
        for artifact in state.active_artifacts:
            if artifact.get("artifact_id") == artifact_id:
                artifact["status"] = status
                artifact["updated_at"] = now
                if reason:
                    artifact["status_reason"] = reason
                break
        return self.save(state, event_type=f"artifact_{status}")

    def archive_stale_artifacts(
        self,
        scope: SessionScope,
        *,
        older_than_seconds: float,
        now: Optional[float] = None,
    ) -> ActiveSessionState:
        state = self.get(scope)
        cutoff_now = now or time.time()
        changed = False
        for artifact in state.active_artifacts:
            if artifact.get("status", "active") != "active":
                continue
            updated_at = float(artifact.get("updated_at") or artifact.get("created_at") or cutoff_now)
            if cutoff_now - updated_at >= older_than_seconds:
                artifact["status"] = "archived"
                artifact["updated_at"] = cutoff_now
                artifact["status_reason"] = "stale"
                changed = True
        if changed:
            return self.save(state, event_type="artifacts_archived_stale")
        return state

    def record_handoff(self, scope: SessionScope, handoff: Dict[str, Any]) -> ActiveSessionState:
        state = self.get(scope)
        state.handoff = dict(handoff)
        return self.save(state, event_type="handoff_recorded")

    def record_route_trace(self, scope: SessionScope, trace: Dict[str, Any]) -> ActiveSessionState:
        state = self.get(scope)
        trace = dict(trace)
        trace.setdefault("created_at", time.time())
        trace.setdefault("status", "pending")
        trace.setdefault("compliance", "pending")
        state.route_traces = [trace, *state.route_traces[:19]]
        return self.save(state, event_type="route_trace_recorded")

    def record_first_tool(self, scope: SessionScope, *, tool_name: str, tool_family: str) -> ActiveSessionState:
        state = self.get(scope)
        if not state.route_traces:
            trace = {
                "query": None,
                "route_status": "unknown",
                "expected_first_tool_family": None,
                "actual_first_tool": tool_name,
                "actual_first_tool_family": tool_family,
                "compliance": "not_applicable",
                "bypass_reason": "no_route_trace",
                "created_at": time.time(),
                "completed_at": time.time(),
            }
            state.route_traces = [trace]
            return self.save(state, event_type="route_first_tool_recorded")

        latest = dict(state.route_traces[0])
        if latest.get("actual_first_tool"):
            return state
        latest["actual_first_tool"] = tool_name
        latest["actual_first_tool_family"] = tool_family
        expected = latest.get("expected_first_tool_family")
        if latest.get("route_status") == "defer" and tool_family == "gbrain":
            latest["compliance"] = "warn"
            latest["bypass_reason"] = "gbrain_used_while_lineage_deferred"
        elif expected is None:
            latest["compliance"] = "not_applicable"
            latest["bypass_reason"] = "no_expected_first_tool"
        elif expected == tool_family:
            latest["compliance"] = "matched"
            latest["bypass_reason"] = None
        else:
            latest["compliance"] = "warn"
            latest["bypass_reason"] = f"expected_{expected}_got_{tool_family}"
        latest["completed_at"] = time.time()
        state.route_traces = [latest, *state.route_traces[1:]]
        return self.save(state, event_type="route_first_tool_recorded")

    def record_writeback_decision(self, scope: SessionScope, decision: Dict[str, Any]) -> ActiveSessionState:
        state = self.get(scope)
        decision = dict(decision)
        decision.setdefault("created_at", time.time())
        state.writeback_decisions = [decision, *state.writeback_decisions[:19]]
        return self.save(state, event_type="writeback_decision_recorded")

    def session_health(self, scope: SessionScope) -> Dict[str, Any]:
        state = self.get(scope)
        traces = state.route_traces
        completed = [trace for trace in traces if trace.get("actual_first_tool")]
        warnings = [trace for trace in completed if trace.get("compliance") == "warn"]
        pending_writebacks = [
            decision for decision in state.writeback_decisions
            if decision.get("action") in {"raw_capture", "staged_artifact", "canonical_review"}
        ]
        return {
            "scope_key": scope.scope_key,
            "session_id": scope.session_id,
            "active_artifacts": len([a for a in state.active_artifacts if a.get("status", "active") == "active"]),
            "unresolved_asks": len(state.unresolved_asks),
            "route_traces": len(traces),
            "route_warnings": len(warnings),
            "route_pending": len([trace for trace in traces if not trace.get("actual_first_tool")]),
            "writeback_decisions": len(state.writeback_decisions),
            "writeback_pending": len(pending_writebacks),
            "handoff_kind": (state.handoff or {}).get("kind"),
            "updated_at": state.updated_at,
        }

    def snapshot(self, scope: SessionScope) -> Dict[str, Any]:
        return self.get(scope).to_dict()
