"""Per-session active state storage for Hermes Infinite Session Engine."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

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


_ADDITIVE_LIST_FIELDS = (
    "active_artifacts",
    "unresolved_asks",
    "constraints",
    "running_processes",
    "files_touched",
    "route_traces",
    "writeback_decisions",
    "mutation_audit",
)


def _item_dedupe_key(item: Dict[str, Any], *, field_name: str) -> str:
    """Return a stable merge key for additive active-state list entries."""
    for key in ("artifact_id", "trace_id", "decision_id", "ask_id", "process_id", "path", "local_path"):
        if item.get(key) is not None:
            return f"{field_name}:{key}:{item.get(key)}"
    if item.get("turn_id") is not None and item.get("created_at") is not None:
        return f"{field_name}:turn:{item.get('turn_id')}:{item.get('created_at')}"
    return f"{field_name}:repr:{repr(sorted(item.items()))}"


def _merge_list_field(field_name: str, newer_items: Iterable[Dict[str, Any]], older_items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge additive lists while preserving newest-first ordering."""
    merged: List[Dict[str, Any]] = []
    seen = set()
    for source in (newer_items, older_items):
        for raw_item in source or []:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            key = _item_dedupe_key(item, field_name=field_name)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged[:50]


def _newer_mapping(candidate: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]], *, timestamp_keys: tuple[str, ...]) -> Optional[Dict[str, Any]]:
    """Pick the mapping with the newest timestamp-like field."""
    if not candidate:
        return dict(current) if current else None
    if not current:
        return dict(candidate)
    candidate_ts = max(float(candidate.get(key) or 0) for key in timestamp_keys)
    current_ts = max(float(current.get(key) or 0) for key in timestamp_keys)
    return dict(candidate if candidate_ts >= current_ts else current)


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
    mutation_audit: List[Dict[str, Any]] = field(default_factory=list)
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
            mutation_audit=list(data.get("mutation_audit") or []),
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
            "mutation_audit": self.mutation_audit,
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

    def _merge_for_save(self, candidate: ActiveSessionState, current: Optional[ActiveSessionState], *, event_type: str) -> ActiveSessionState:
        """Merge stale candidate updates into the freshest persisted state.

        This is optimistic locking without a schema migration: every state has a
        materialized version counter. If a caller saves a stale object, additive
        fields from the stale object are merged into the current persisted row
        instead of blindly overwriting it.
        """
        now = time.time()
        if current is None:
            candidate.version = max(1, int(candidate.version or 1))
            candidate.updated_at = now
            candidate.mutation_audit = _merge_list_field(
                "mutation_audit",
                [{"kind": event_type, "version": candidate.version, "created_at": now}],
                candidate.mutation_audit,
            )[:20]
            return candidate

        stale_save = int(current.version or 1) >= int(candidate.version or 1)
        current_is_strictly_newer = int(current.version or 1) > int(candidate.version or 1)
        merged = current if current_is_strictly_newer else candidate
        other = candidate if current_is_strictly_newer else current
        for field_name in _ADDITIVE_LIST_FIELDS:
            setattr(
                merged,
                field_name,
                _merge_list_field(
                    field_name,
                    getattr(merged, field_name, []),
                    getattr(other, field_name, []),
                ),
            )
        merged.latest_user_request = _newer_mapping(
            candidate.latest_user_request,
            current.latest_user_request,
            timestamp_keys=("timestamp", "updated_at", "created_at"),
        )
        merged.current_task = _newer_mapping(
            candidate.current_task,
            current.current_task,
            timestamp_keys=("completed_at", "updated_at", "last_assistant_at", "created_at"),
        )
        merged.handoff = _newer_mapping(
            candidate.handoff,
            current.handoff,
            timestamp_keys=("created_at", "updated_at"),
        )
        merged.version = max(int(current.version or 1), int(candidate.version or 1)) + 1
        merged.updated_at = now
        merged.mutation_audit = _merge_list_field(
            "mutation_audit",
            [{
                "kind": event_type,
                "version": merged.version,
                "stale_save_merged": stale_save,
                "created_at": now,
            }],
            merged.mutation_audit,
        )[:20]
        return merged

    def save(self, state: ActiveSessionState, *, event_type: str = "save") -> ActiveSessionState:
        current_data = self.session_db.get_active_session_state(state.scope.scope_key)
        current = ActiveSessionState.from_dict(current_data) if current_data else None
        if current is not None:
            current.scope = state.scope
        state = self._merge_for_save(state, current, event_type=event_type)
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
        artifact.setdefault("lifecycle", artifact.get("status", "active"))
        artifact.setdefault("owner_scope", scope.scope_key)
        artifact.setdefault("created_at", time.time())
        artifact.setdefault("updated_at", time.time())
        artifact_id = artifact.get("artifact_id")
        artifact_path = artifact.get("local_path") or artifact.get("path")
        retained: List[Dict[str, Any]] = []
        for existing in state.active_artifacts:
            existing = dict(existing)
            same_id = artifact_id is not None and existing.get("artifact_id") == artifact_id
            same_path = artifact_path and (existing.get("local_path") or existing.get("path")) == artifact_path
            if same_id or same_path:
                existing["status"] = "superseded"
                existing["lifecycle"] = "superseded"
                existing["superseded_by"] = artifact_id
                existing["updated_at"] = artifact["updated_at"]
                retained.append(existing)
            else:
                retained.append(existing)
        state.active_artifacts = [artifact, *retained]
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
                artifact["lifecycle"] = status
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

    def record_route_trace(self, scope: SessionScope, trace: Dict[str, Any], *, turn_id: Optional[str] = None) -> ActiveSessionState:
        state = self.get(scope)
        trace = dict(trace)
        trace.setdefault("created_at", time.time())
        trace.setdefault("status", "pending")
        trace.setdefault("compliance", "pending")
        if turn_id is not None:
            trace["turn_id"] = str(turn_id)
        trace.setdefault("trace_id", f"route:{trace.get('turn_id') or 'unknown'}:{trace.get('created_at')}")
        state.route_traces = [trace, *state.route_traces[:19]]
        return self.save(state, event_type="route_trace_recorded")

    def record_first_tool(self, scope: SessionScope, *, tool_name: str, tool_family: str, turn_id: Optional[str] = None) -> ActiveSessionState:
        state = self.get(scope)
        target_index = None
        for idx, trace in enumerate(state.route_traces):
            if trace.get("actual_first_tool"):
                continue
            if turn_id is None or trace.get("turn_id") == str(turn_id):
                target_index = idx
                break
        if target_index is None:
            trace = {
                "query": None,
                "route_status": "unknown",
                "expected_first_tool_family": None,
                "actual_first_tool": tool_name,
                "actual_first_tool_family": tool_family,
                "compliance": "not_applicable",
                "bypass_reason": "no_route_trace",
                "turn_id": str(turn_id) if turn_id is not None else None,
                "created_at": time.time(),
                "completed_at": time.time(),
            }
            trace["trace_id"] = f"route:{trace.get('turn_id') or 'unknown'}:{trace['created_at']}"
            state.route_traces = [trace, *state.route_traces[:19]]
            return self.save(state, event_type="route_first_tool_recorded")

        latest = dict(state.route_traces[target_index])
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
        state.route_traces = [latest, *state.route_traces[:target_index], *state.route_traces[target_index + 1:]]
        return self.save(state, event_type="route_first_tool_recorded")

    def close_route_trace_without_tool(self, scope: SessionScope, *, turn_id: Optional[str] = None, reason: str = "final_response") -> ActiveSessionState:
        """Close a pending trace for a turn when the assistant finishes without tools."""
        state = self.get(scope)
        target_index = None
        for idx, trace in enumerate(state.route_traces):
            if trace.get("actual_first_tool") or trace.get("compliance") != "pending":
                continue
            if turn_id is None or trace.get("turn_id") == str(turn_id):
                target_index = idx
                break
        if target_index is None:
            return state
        trace = dict(state.route_traces[target_index])
        trace["compliance"] = "no_tool_used"
        trace["bypass_reason"] = reason
        trace["completed_at"] = time.time()
        state.route_traces = [trace, *state.route_traces[:target_index], *state.route_traces[target_index + 1:]]
        return self.save(state, event_type="route_trace_closed_without_tool")

    def record_writeback_decision(self, scope: SessionScope, decision: Dict[str, Any]) -> ActiveSessionState:
        state = self.get(scope)
        decision = dict(decision)
        decision.setdefault("created_at", time.time())
        state.writeback_decisions = [decision, *state.writeback_decisions[:19]]
        return self.save(state, event_type="writeback_decision_recorded")

    def session_health(self, scope: SessionScope) -> Dict[str, Any]:
        state = self.get(scope)
        traces = state.route_traces
        completed = [trace for trace in traces if trace.get("actual_first_tool") or trace.get("completed_at")]
        warnings = [trace for trace in completed if trace.get("compliance") == "warn"]
        pending_writebacks = [
            decision for decision in state.writeback_decisions
            if decision.get("action") in {"raw_capture", "staged_artifact", "canonical_review"}
        ]
        active_artifact_count = len([a for a in state.active_artifacts if a.get("status", "active") == "active"])
        route_pending_count = len([trace for trace in traces if not trace.get("completed_at") and not trace.get("actual_first_tool")])
        return {
            "scope_key": scope.scope_key,
            "session_id": scope.session_id,
            "active_artifacts": active_artifact_count,
            "stale_artifacts": len([a for a in state.active_artifacts if a.get("status") in {"superseded", "deleted", "missing", "archived"}]),
            "unresolved_asks": len(state.unresolved_asks),
            "route_traces": len(traces),
            "route_warnings": len(warnings),
            "route_no_tool_used": len([trace for trace in traces if trace.get("compliance") == "no_tool_used"]),
            "route_pending": route_pending_count,
            "writeback_decisions": len(state.writeback_decisions),
            "writeback_pending": len(pending_writebacks),
            "handoff_kind": (state.handoff or {}).get("kind"),
            "current_task_status": (state.current_task or {}).get("status"),
            "mutation_audit_entries": len(state.mutation_audit),
            "version": state.version,
            "updated_at": state.updated_at,
        }

    def render_debug_report(self, scope: SessionScope) -> str:
        """Render a compact, redacted operator view of the continuity state."""
        state = self.get(scope)
        health = self.session_health(scope)
        lines = [
            "## Active Session Debug",
            f"scope: {scope.scope_key}",
            f"version: {health.get('version')}",
            f"current_task_status: {health.get('current_task_status') or 'none'}",
            f"active_artifacts: {health.get('active_artifacts')} stale_artifacts: {health.get('stale_artifacts')}",
            f"route: pending={health.get('route_pending')} warnings={health.get('route_warnings')} no_tool={health.get('route_no_tool_used')}",
            f"writeback: pending={health.get('writeback_pending')} decisions={health.get('writeback_decisions')}",
            f"handoff: {health.get('handoff_kind') or 'none'}",
        ]
        if state.current_task:
            task = str(state.current_task.get("text") or "")[:160].replace("\n", " ")
            lines.append(f"current_task: {task}")
        for artifact in state.active_artifacts[:5]:
            title = str(artifact.get("title") or artifact.get("local_path") or artifact.get("uri") or artifact.get("artifact_id"))[:160]
            lines.append(f"artifact: {artifact.get('kind', 'artifact')} {artifact.get('status', 'active')} {title}")
        return "\n".join(lines)

    def snapshot(self, scope: SessionScope) -> Dict[str, Any]:
        return self.get(scope).to_dict()
