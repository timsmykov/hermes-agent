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


_STRONG_REFERENCE_RE = re.compile(r"\b(продолжи|continue)\b", re.IGNORECASE)
_DEICTIC_RE = re.compile(
    r"\b(этот|эта|это|эти|тот|та|то|те|там|его|её|ее|их|"
    r"this|that|these|those|it|its|their)\b",
    re.IGNORECASE,
)
_ARTIFACT_KIND_HINTS = {
    "report": {"отч", "report", "brief", "свод"},
    "file": {"файл", "file", "document", "doc", "документ"},
    "image": {"картин", "image", "photo", "изображ", "pic"},
    "page": {"страниц", "page", "notion", "gbrain"},
}
_ARTIFACT_KIND_COMPATIBILITY = {
    "report": {"file", "page", "report"},
    "file": {"file"},
    "image": {"image"},
    "page": {"page"},
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

    def __init__(self, active_state_store: ActiveStateStore, session_db: Any = None):
        self.active_state_store = active_state_store
        self.session_db = session_db or getattr(active_state_store, "session_db", None)

    @staticmethod
    def has_ambiguous_reference(text: str) -> bool:
        text = text or ""
        if _STRONG_REFERENCE_RE.search(text):
            return True
        # Generic pronouns like "это/её/it" are common in architecture
        # questions and should not cause active-artifact dumps by themselves.
        # Resolve only when the turn also contains an artifact-type hint.
        return bool(_DEICTIC_RE.search(text) and ReferenceResolver._hinted_kinds(text))

    @staticmethod
    def _artifact_is_live(artifact: Dict[str, Any]) -> bool:
        path = artifact.get("local_path") or artifact.get("path")
        if not path:
            return True
        try:
            from pathlib import Path

            return Path(str(path)).exists()
        except Exception:
            return True

    @staticmethod
    def _hinted_kinds(text: str) -> set[str]:
        lowered = (text or "").lower()
        kinds: set[str] = set()
        for kind, hints in _ARTIFACT_KIND_HINTS.items():
            if any(hint in lowered for hint in hints):
                kinds.add(kind)
        return kinds

    @staticmethod
    def _hint_strings(kinds: set[str]) -> set[str]:
        hints: set[str] = set()
        for kind in kinds:
            hints.update(_ARTIFACT_KIND_HINTS.get(kind, set()))
        return hints

    @staticmethod
    def _compatible_kinds(kinds: set[str]) -> set[str]:
        compatible: set[str] = set()
        for kind in kinds:
            compatible.update(_ARTIFACT_KIND_COMPATIBILITY.get(kind, {kind}))
        return compatible

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
            and self._artifact_is_live(artifact)
        ]
        priority_sources = ("reply", "attachment", "current_turn")
        priority_artifacts = [
            artifact for artifact in artifacts
            if artifact.get("source") in priority_sources or artifact.get("source_kind") in priority_sources
        ]
        if priority_artifacts:
            artifacts = priority_artifacts
        if not artifacts:
            lineage_candidates = self._lineage_candidates(scope, text)
            if lineage_candidates:
                if len(lineage_candidates) == 1:
                    return ResolutionResult(
                        status="resolved",
                        source="session_lineage",
                        query=text,
                        artifact=lineage_candidates[0],
                        candidates=lineage_candidates,
                    )
                return ResolutionResult(
                    status="needs_clarification",
                    source="session_lineage",
                    query=text,
                    candidates=lineage_candidates[:5],
                    reason="multiple_lineage_candidates",
                )
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
            lexical_hints = self._hint_strings(hinted_kinds)
            compatible_kinds = self._compatible_kinds(hinted_kinds)
            candidates = [
                artifact
                for artifact in artifacts
                if artifact.get("kind") in compatible_kinds
                or any(str(artifact.get(field, "")).lower().find(hint) >= 0 for field in ("title", "local_path", "uri") for hint in lexical_hints)
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

    def _lineage_candidates(self, scope: SessionScope, text: str) -> List[Dict[str, Any]]:
        if self.session_db is None:
            return []
        try:
            from agent.lineage_retrieval import retrieve_lineage

            evidence = retrieve_lineage(self.session_db, scope, text, limit=5)
        except Exception:
            return []
        return [
            {
                "artifact_id": f"lineage:{item.session_id}:{item.ordinal}",
                "kind": "lineage_evidence",
                "title": item.content[:120],
                "content": item.content,
                "source": item.source,
                "session_id": item.session_id,
                "role": item.role,
                "score": item.score,
                "ordinal": item.ordinal,
            }
            for item in evidence
        ]
