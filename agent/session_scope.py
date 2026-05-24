"""Session scope primitives for isolated long-running Hermes conversations.

The scope key is the hard boundary for active runtime state. Shared long-term
knowledge lives in Gbrain; per-turn working context lives under one
SessionScope so Telegram topics, CLI runs, cron jobs, and API sessions cannot
accidentally share active artifacts or unresolved asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SessionScope:
    """Stable identity for a single runtime conversation lane."""

    platform: str
    session_id: str
    chat_id: Optional[str] = None
    thread_id: Optional[str] = None
    topic_id: Optional[str] = None
    lineage_id: Optional[str] = None
    profile: Optional[str] = None

    def __post_init__(self) -> None:
        platform = str(self.platform or "local").lower()
        session_id = str(self.session_id or "default")
        chat_id = str(self.chat_id) if self.chat_id is not None else None
        thread_id = str(self.thread_id) if self.thread_id is not None else None
        topic_id = str(self.topic_id) if self.topic_id is not None else thread_id
        lineage_id = self.lineage_id or self._default_lineage_id(
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            session_id=session_id,
        )
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "chat_id", chat_id)
        object.__setattr__(self, "thread_id", thread_id)
        object.__setattr__(self, "topic_id", topic_id)
        object.__setattr__(self, "lineage_id", str(lineage_id))
        if self.profile is not None:
            object.__setattr__(self, "profile", str(self.profile))

    @staticmethod
    def _default_lineage_id(
        *,
        platform: str,
        chat_id: Optional[str],
        thread_id: Optional[str],
        session_id: str,
    ) -> str:
        if platform == "telegram" and chat_id and thread_id:
            return f"telegram:{chat_id}:thread:{thread_id}"
        if chat_id and thread_id:
            return f"{platform}:{chat_id}:thread:{thread_id}"
        if chat_id:
            return f"{platform}:{chat_id}"
        return f"{platform}:session:{session_id}"

    @property
    def scope_key(self) -> str:
        """Primary storage key for active state and artifacts."""
        return self.lineage_id or f"{self.platform}:session:{self.session_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "chat_id": self.chat_id,
            "thread_id": self.thread_id,
            "topic_id": self.topic_id,
            "session_id": self.session_id,
            "lineage_id": self.lineage_id,
            "profile": self.profile,
            "scope_key": self.scope_key,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionScope":
        return cls(
            platform=data.get("platform") or "local",
            chat_id=data.get("chat_id"),
            thread_id=data.get("thread_id"),
            topic_id=data.get("topic_id"),
            session_id=data.get("session_id") or data.get("id") or "default",
            lineage_id=data.get("lineage_id") or data.get("scope_key"),
            profile=data.get("profile"),
        )

    @classmethod
    def from_session_source(
        cls,
        source: Any,
        *,
        session_id: str,
        profile: Optional[str] = None,
        lineage_id: Optional[str] = None,
    ) -> "SessionScope":
        platform_value = getattr(getattr(source, "platform", None), "value", None) or getattr(source, "platform", None) or "local"
        thread_id = getattr(source, "thread_id", None)
        return cls(
            platform=str(platform_value),
            chat_id=getattr(source, "chat_id", None),
            thread_id=thread_id,
            topic_id=thread_id,
            session_id=session_id,
            lineage_id=lineage_id,
            profile=profile,
        )
