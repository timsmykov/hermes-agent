"""Scoped Gbrain route hints for Infinite Session Engine.

This is deliberately a routing/evidence-contract layer, not a Gbrain dump. It
only tells the agent when broader canonical retrieval is allowed after local
active state and current lineage have failed to resolve the request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List


_GBRAIN_INTENT_RE = re.compile(
    r"\b(gbrain|roadmap|project|проект|roadmap|роадмап|runbook|ранбук|meeting|встреч|"
    r"person|people|человек|компан|company|internal doc|документ|briefing|бриф|"
    r"knowledge|памят|принцип|principle)\b",
    re.IGNORECASE,
)


@dataclass
class GbrainRouteHint:
    status: str
    reason: str
    query: str
    allowed_after: List[str]
    expected_first_tool_family: str | None = None
    enforcement_mode: str = "warn"
    required_provenance: bool = True
    prompt_dump_allowed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "query": self.query,
            "allowed_after": self.allowed_after,
            "expected_first_tool_family": self.expected_first_tool_family,
            "enforcement_mode": self.enforcement_mode,
            "required_provenance": self.required_provenance,
            "prompt_dump_allowed": self.prompt_dump_allowed,
        }


def classify_gbrain_route(query: str, *, local_resolved: bool, lineage_hits: int) -> GbrainRouteHint:
    text = query or ""
    if local_resolved:
        return GbrainRouteHint(
            status="blocked",
            reason="local_reference_resolved",
            query=text,
            allowed_after=["active_state"],
        )
    if lineage_hits > 0:
        return GbrainRouteHint(
            status="defer",
            reason="current_lineage_has_evidence",
            query=text,
            allowed_after=["active_state", "session_lineage"],
        )
    if _GBRAIN_INTENT_RE.search(text):
        return GbrainRouteHint(
            status="allowed",
            reason="durable_knowledge_intent_after_local_miss",
            query=text,
            allowed_after=["active_state", "session_lineage"],
            expected_first_tool_family="gbrain",
        )
    return GbrainRouteHint(
        status="not_applicable",
        reason="no_durable_knowledge_intent",
        query=text,
        allowed_after=["active_state", "session_lineage"],
    )


def render_gbrain_route_hint(hint: GbrainRouteHint) -> str:
    if hint.status not in {"allowed", "defer"}:
        return ""
    return (
        "## Scoped Gbrain Retrieval Route\n"
        f"status: {hint.status}\n"
        f"reason: {hint.reason}\n"
        f"expected_first_tool_family: {hint.expected_first_tool_family or 'none'}\n"
        f"enforcement_mode: {hint.enforcement_mode}\n"
        "contract: use Gbrain only with source-scoped/cited evidence; do not dump raw Gbrain pages into prompt."
    )


def tool_family(tool_name: str) -> str:
    """Coarse tool-family label used for route compliance metrics."""
    if tool_name.startswith("mcp_gbrain_knowledge_"):
        return "gbrain"
    if tool_name.startswith("mcp_notion_"):
        return "notion"
    if tool_name in {"web_search", "web_extract"}:
        return "web"
    if tool_name.startswith("mcp_browser_use_"):
        return "browser"
    if tool_name in {"terminal", "process"}:
        return "terminal"
    if tool_name in {"read_file", "write_file", "search_files", "patch"}:
        return "file"
    return tool_name


def route_trace_from_hint(hint: GbrainRouteHint) -> Dict[str, Any]:
    """Convert a rendered route hint into an auditable trace record."""
    return {
        "query": hint.query,
        "route_status": hint.status,
        "route_reason": hint.reason,
        "allowed_after": hint.allowed_after,
        "expected_first_tool_family": hint.expected_first_tool_family,
        "enforcement_mode": hint.enforcement_mode,
        "required_provenance": hint.required_provenance,
        "prompt_dump_allowed": hint.prompt_dump_allowed,
    }
