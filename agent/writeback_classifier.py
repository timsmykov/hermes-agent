"""Writeback candidate classifier for Infinite Session Engine.

This module decides whether a completed session/turn should produce a durable
Gbrain writeback candidate, an expiry-aware open loop, or an explicit skip. It
intentionally does not perform canonical promotion: canonical Gbrain knowledge
must still go through review-first promotion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List


_DURABLE_RE = re.compile(
    r"\b(decision|decided|architecture|runbook|principle|preference|remember|"
    r"решили|решение|архитектур|ранбук|принцип|предпочт|запомни|договорил)\b",
    re.IGNORECASE,
)
_OPEN_LOOP_RE = re.compile(
    r"\b(todo|follow[- ]?up|blocked|next step|open loop|надо|потом|следующ|"
    r"заблок|проверить|доделать)\b",
    re.IGNORECASE,
)
_EPHEMERAL_RE = re.compile(
    r"\b(ok|спасибо|понял|ага|лол|test|smoke|ping)\b",
    re.IGNORECASE,
)


@dataclass
class WritebackDecision:
    action: str
    reason: str
    confidence: float
    visibility: str = "private"
    expires: bool = False
    payload: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "confidence": self.confidence,
            "visibility": self.visibility,
            "expires": self.expires,
            "payload": self.payload or {},
        }


def _join_text(turns: Iterable[Any]) -> str:
    parts: List[str] = []
    for turn in turns:
        if isinstance(turn, dict):
            content = turn.get("content") or turn.get("text") or ""
        else:
            content = str(turn or "")
        if isinstance(content, list):
            content = "\n".join(str(item.get("text") or item.get("content") or item) for item in content)
        parts.append(str(content))
    return "\n".join(part for part in parts if part).strip()


def classify_writeback_candidate(
    turns: Iterable[Any],
    *,
    artifact_count: int = 0,
    tool_count: int = 0,
    user_requested_memory: bool = False,
) -> WritebackDecision:
    """Classify whether a session slice deserves a durable writeback candidate."""
    text = _join_text(turns)
    if not text:
        return WritebackDecision(action="skip", reason="empty_session_slice", confidence=1.0)

    if user_requested_memory or re.search(r"\b(remember this|запомни это|сохрани это)\b", text, re.IGNORECASE):
        return WritebackDecision(
            action="canonical_review",
            reason="explicit_memory_request",
            confidence=0.95,
            payload={"summary_source": text[:1000]},
        )

    if _OPEN_LOOP_RE.search(text):
        return WritebackDecision(
            action="raw_capture",
            reason="expiry_aware_open_loop",
            confidence=0.78,
            expires=True,
            payload={"summary_source": text[:1000]},
        )

    if _DURABLE_RE.search(text) or artifact_count > 0 or tool_count >= 5:
        return WritebackDecision(
            action="staged_artifact",
            reason="significant_session_or_durable_claim",
            confidence=0.72,
            payload={"summary_source": text[:1000], "artifact_count": artifact_count, "tool_count": tool_count},
        )

    if len(text) < 120 or _EPHEMERAL_RE.search(text):
        return WritebackDecision(action="skip", reason="ephemeral_or_low_signal", confidence=0.86)

    return WritebackDecision(action="skip", reason="no_durable_signal", confidence=0.66)


def writeback_wrapper(decision: WritebackDecision) -> Dict[str, Any]:
    """Return a safe wrapper for downstream Gbrain writeback execution."""
    data = decision.to_dict()
    action = data["action"]
    if action == "canonical_review":
        data["target"] = "gbrain_review_queue"
        data["promotion_required"] = True
    elif action == "staged_artifact":
        data["target"] = "gbrain_staging_artifact"
        data["promotion_required"] = True
    elif action == "raw_capture":
        data["target"] = "runtime_raw_capture"
        data["promotion_required"] = False
    else:
        data["target"] = None
        data["promotion_required"] = False
    return data


def verify_writeback_retrieval_after_embed(
    wrapper: Dict[str, Any],
    retrieve: Callable[[str], Iterable[Any]],
    *,
    expected: str | None = None,
) -> Dict[str, Any]:
    """Verify that a written/embedded candidate is retrievable."""
    action = wrapper.get("action")
    if action == "skip":
        return {"status": "not_applicable", "reason": "writeback_skipped", "match_count": 0}
    payload = wrapper.get("payload") or {}
    query = expected or payload.get("summary_source") or wrapper.get("reason") or ""
    query = str(query).strip()[:240]
    if not query:
        return {"status": "failed", "reason": "missing_retrieval_query", "match_count": 0}
    try:
        matches = list(retrieve(query) or [])
    except Exception as exc:
        return {"status": "failed", "reason": "retrieval_error", "error": str(exc)[:300], "match_count": 0}
    if matches:
        return {
            "status": "verified",
            "reason": "retrievable_after_embed",
            "match_count": len(matches),
            "query": query,
        }
    return {"status": "failed", "reason": "not_retrievable_after_embed", "match_count": 0, "query": query}
