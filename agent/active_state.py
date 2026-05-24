"""Per-session active state storage for Hermes Infinite Session Engine."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.session_scope import SessionScope


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
        if not state.current_task:
            state.current_task = {
                "text": text,
                "status": "in_progress",
                "source_message_id": message_id,
                "updated_at": timestamp or time.time(),
            }
        return self.save(state, event_type="latest_user_request")

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

    def snapshot(self, scope: SessionScope) -> Dict[str, Any]:
        return self.get(scope).to_dict()
