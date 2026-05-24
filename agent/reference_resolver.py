"""Reference resolution for ambiguous current-session phrases.

MVP scope: resolve short RU/EN references such as "этот отчёт", "этот файл",
"that report", and "continue" against the current SessionScope's active state.
The resolver intentionally does not query global memory first; it keeps
current-session context isolated before any broader retrieval layer is allowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.active_state import ActiveStateStore
from agent.session_scope import SessionScope


_AMBIGUOUS_REFERENCE_RE = re.compile(
    r"\b(этот|эта|это|эти|тот|та|то|те|там|его|её|ее|их|продолжи|"
    r"this|that|these|those|it|its|their|continue)\b",
    re.IGNORECASE,
)
_ARTIFACT_KIND_HINTS = {
    "report": {"отч", "report", "brief", "свод"},
    "file": {"файл", "file", "document", "doc", "документ"},
    "image": {"картин", "image", "photo", "изображ", "pic"},
    "page": {"страниц", "page", "notion", "gbrain"},
}


@dataclass
class ResolutionResult:
    status: str
    source: str
    query: str
    artifact: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] | None = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "query": self.query,
            "artifact": self.artifact,
            "candidates": self.candidates or [],
            "reason": self.reason,
        }


class ReferenceResolver:
    """Resolve ambiguous references from one isolated active session scope."""

    def __init__(self, active_state_store: ActiveStateStore):
        self.active_state_store = active_state_store

    @staticmethod
    def has_ambiguous_reference(text: str) -> bool:
        return bool(_AMBIGUOUS_REFERENCE_RE.search(text or ""))

    @staticmethod
    def _hinted_kinds(text: str) -> set[str]:
        lowered = (text or "").lower()
        kinds: set[str] = set()
        for kind, hints in _ARTIFACT_KIND_HINTS.items():
            if any(hint in lowered for hint in hints):
                kinds.add(kind)
        return kinds

    def resolve(self, scope: SessionScope, text: str) -> ResolutionResult:
        if not self.has_ambiguous_reference(text):
            return ResolutionResult(
                status="not_applicable",
                source="none",
                query=text,
                reason="no_ambiguous_reference",
            )

        state = self.active_state_store.get(scope)
        artifacts = [
            artifact
            for artifact in state.active_artifacts
            if artifact.get("status", "active") == "active"
            and artifact.get("owner_scope", scope.scope_key) == scope.scope_key
        ]
        if not artifacts:
            return ResolutionResult(
                status="needs_clarification",
                source="active_state",
                query=text,
                candidates=[],
                reason="no_active_artifacts_in_scope",
            )

        hinted_kinds = self._hinted_kinds(text)
        candidates = artifacts
        if hinted_kinds:
            candidates = [
                artifact
                for artifact in artifacts
                if artifact.get("kind") in hinted_kinds
                or any(str(artifact.get(field, "")).lower().find(hint) >= 0 for field in ("title", "local_path", "uri") for hint in hinted_kinds)
            ]
            if not candidates:
                candidates = artifacts

        if len(candidates) == 1:
            return ResolutionResult(
                status="resolved",
                source="active_state",
                query=text,
                artifact=candidates[0],
                candidates=candidates,
            )
        return ResolutionResult(
            status="needs_clarification",
            source="active_state",
            query=text,
            candidates=candidates[:5],
            reason="multiple_active_artifacts",
        )
