"""Current-session lineage retrieval for Infinite Session Engine.

This module intentionally searches only the active SQLite session lineage
(parent -> current compression chain). It is not a global memory/Gbrain search;
callers use it before broader knowledge retrieval to avoid cross-topic leakage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from agent.session_scope import SessionScope


_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "this", "that",
    "these", "those", "it", "its", "continue", "продолжи", "этот", "эта", "это",
    "эти", "тот", "та", "то", "те", "его", "ее", "её", "их", "там", "в", "на", "и",
    "или", "для", "по", "с", "со", "из", "к", "ко", "про",
}


@dataclass
class LineageEvidence:
    session_id: str
    role: str
    content: str
    score: int
    ordinal: int
    source: str = "session_lineage"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "score": self.score,
            "ordinal": self.ordinal,
        }


def _terms(query: str) -> List[str]:
    words = re.findall(r"[\w\u0400-\u04FF]{3,}", (query or "").lower(), flags=re.UNICODE)
    return [word for word in words if word not in _STOPWORDS]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or "")


def _session_ids_for_lineage(session_db: Any, session_id: str) -> List[str]:
    if not session_id:
        return []
    helper = getattr(session_db, "_session_lineage_root_to_tip", None)
    if callable(helper):
        try:
            result = helper(session_id)
            if isinstance(result, (list, tuple)):
                return [str(item) for item in result]
        except Exception:
            return [session_id]
    return [session_id]


def retrieve_lineage(
    session_db: Any,
    scope: SessionScope,
    query: str,
    *,
    limit: int = 5,
    roles: Optional[Iterable[str]] = ("user", "assistant"),
    max_chars_per_item: int = 500,
) -> List[LineageEvidence]:
    """Return compact evidence from the current session lineage only."""
    if session_db is None or not scope.session_id:
        return []
    allowed_roles = set(roles or []) if roles is not None else None
    lineage_ids = _session_ids_for_lineage(session_db, scope.session_id)
    if not lineage_ids:
        return []
    try:
        messages = session_db.get_messages_as_conversation(scope.session_id, include_ancestors=True)
    except Exception:
        return []
    terms = _terms(query)
    evidence: List[LineageEvidence] = []
    for ordinal, msg in enumerate(messages):
        role = msg.get("role") or "unknown"
        if allowed_roles is not None and role not in allowed_roles:
            continue
        text = _content_to_text(msg.get("content")).strip()
        if not text:
            continue
        lowered = text.lower()
        score = sum(1 for term in terms if term in lowered)
        # If the query has no discriminative terms (e.g. only "продолжи"),
        # keep recent user/assistant turns as fallback lineage context.
        if terms and score <= 0:
            continue
        session_id = msg.get("session_id") or scope.session_id
        clipped = text[:max_chars_per_item]
        evidence.append(LineageEvidence(session_id=session_id, role=role, content=clipped, score=score, ordinal=ordinal))
    if not evidence and not terms:
        # Conversation loader omits session ids; use current scope as provenance.
        for ordinal, msg in enumerate(messages[-limit:]):
            role = msg.get("role") or "unknown"
            if allowed_roles is not None and role not in allowed_roles:
                continue
            text = _content_to_text(msg.get("content")).strip()
            if text:
                evidence.append(LineageEvidence(session_id=scope.session_id, role=role, content=text[:max_chars_per_item], score=0, ordinal=ordinal))
    evidence.sort(key=lambda item: (item.score, item.ordinal), reverse=True)
    return evidence[: max(0, int(limit))]


def render_lineage_evidence(evidence: List[LineageEvidence]) -> str:
    if not evidence:
        return ""
    lines = ["## Current Session Lineage Evidence"]
    for item in evidence:
        safe = item.content.replace("\n", " ").strip()
        lines.append(f"- [{item.source} session={item.session_id} role={item.role} score={item.score}] {safe}")
    return "\n".join(lines)
