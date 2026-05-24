"""Session-health report helpers for Infinite Session Engine."""

from __future__ import annotations

from typing import Any, Dict

from agent.active_state import ActiveStateStore
from agent.session_scope import SessionScope


def build_session_health_report(session_db: Any, scope: SessionScope) -> Dict[str, Any]:
    """Build a deterministic, local health snapshot for one session scope."""
    store = ActiveStateStore(session_db)
    health = store.session_health(scope)
    status = "healthy"
    if health["route_warnings"] > 0 or health["unresolved_asks"] > 0:
        status = "attention"
    if health["route_pending"] > 3:
        status = "degraded"
    return {**health, "status": status}


def render_session_health_report(report: Dict[str, Any]) -> str:
    """Render a compact report suitable for CLI, Telegram, or tests."""
    lines = [
        "## Infinite Session Health",
        f"status: {report.get('status')}",
        f"scope: {report.get('scope_key')}",
        f"session: {report.get('session_id')}",
        f"active_artifacts: {report.get('active_artifacts', 0)}",
        f"unresolved_asks: {report.get('unresolved_asks', 0)}",
        f"route_traces: {report.get('route_traces', 0)}",
        f"route_warnings: {report.get('route_warnings', 0)}",
        f"writeback_decisions: {report.get('writeback_decisions', 0)}",
        f"writeback_pending: {report.get('writeback_pending', 0)}",
    ]
    if report.get("handoff_kind"):
        lines.append(f"handoff: {report.get('handoff_kind')}")
    return "\n".join(lines)
