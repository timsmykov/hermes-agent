"""Runtime continuity helpers shared by prompt injection and tool tracking."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from agent.active_state import ActiveStateStore
from agent.session_scope import SessionScope


def scope_from_agent(agent: Any) -> SessionScope:
    """Build the active runtime scope for an AIAgent instance."""
    platform = getattr(agent, "platform", None) or "local"
    session_id = getattr(agent, "session_id", None) or getattr(agent, "_gateway_session_key", None) or "default"
    return SessionScope(
        platform=platform,
        chat_id=getattr(agent, "_chat_id", None),
        thread_id=getattr(agent, "_thread_id", None),
        session_id=session_id,
        profile=getattr(agent, "_profile_name", None),
    )


def active_state_store_for_agent(agent: Any) -> Optional[ActiveStateStore]:
    db = getattr(agent, "_session_db", None)
    if db is None:
        return None
    return ActiveStateStore(db)


def render_active_state_context(agent: Any, user_message: str, *, turn_id: Optional[str] = None) -> str:
    """Render API-call-time active state and reference resolution context."""
    store = active_state_store_for_agent(agent)
    if store is None:
        return ""
    scope = scope_from_agent(agent)
    if turn_id is None:
        turn_id = getattr(agent, "_current_turn_id", None)
    if turn_id is not None:
        try:
            setattr(agent, "_current_turn_id", str(turn_id))
        except Exception:
            pass
    state = store.get(scope)
    blocks = []
    rendered = state.render_for_prompt()
    if rendered:
        blocks.append(rendered)
    try:
        from agent.reference_resolver import ReferenceResolver

        resolver = ReferenceResolver(store, getattr(agent, "_session_db", None))
        resolution = resolver.resolve(scope, user_message or "")
        if resolution.status in {"resolved", "needs_clarification"}:
            blocks.append("## Reference Resolver\n" + json.dumps(resolution.to_dict(), ensure_ascii=False, sort_keys=True))
        if resolution.status == "needs_clarification" or resolution.source == "session_lineage":
            lineage_hits = 0
            try:
                from agent.lineage_retrieval import render_lineage_evidence, retrieve_lineage

                evidence = retrieve_lineage(getattr(agent, "_session_db", None), scope, user_message or "", limit=3)
                lineage_hits = len(evidence)
                rendered_evidence = render_lineage_evidence(evidence)
                if rendered_evidence:
                    blocks.append(rendered_evidence)
            except Exception:
                pass
            try:
                from agent.gbrain_route import classify_gbrain_route, render_gbrain_route_hint, route_trace_from_hint

                hint = classify_gbrain_route(
                    user_message or "",
                    local_resolved=False,
                    lineage_hits=lineage_hits,
                )
                rendered_hint = render_gbrain_route_hint(hint)
                if rendered_hint:
                    blocks.append(rendered_hint)
                    store.record_route_trace(scope, route_trace_from_hint(hint), turn_id=turn_id)
            except Exception:
                pass
        elif resolution.status == "not_applicable":
            lineage_hits = 0
            try:
                from agent.lineage_retrieval import render_lineage_evidence, retrieve_lineage

                evidence = retrieve_lineage(getattr(agent, "_session_db", None), scope, user_message or "", limit=3)
                lineage_hits = len(evidence)
                rendered_evidence = render_lineage_evidence(evidence)
                if rendered_evidence:
                    blocks.append(rendered_evidence)
            except Exception:
                pass
            try:
                from agent.gbrain_route import classify_gbrain_route, render_gbrain_route_hint, route_trace_from_hint

                hint = classify_gbrain_route(
                    user_message or "",
                    local_resolved=False,
                    lineage_hits=lineage_hits,
                )
                rendered_hint = render_gbrain_route_hint(hint)
                if rendered_hint:
                    blocks.append(rendered_hint)
                    store.record_route_trace(scope, route_trace_from_hint(hint), turn_id=turn_id)
            except Exception:
                pass
    except Exception:
        pass
    return "\n\n".join(blocks)


def _parse_json_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {}
    try:
        parsed = json.loads(result)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def artifact_from_tool_result(tool_name: str, args: Dict[str, Any], result: Any) -> Optional[Dict[str, Any]]:
    """Extract a scoped active artifact from selected tool calls."""
    data = _parse_json_result(result)
    if data.get("error"):
        return None
    now = time.time()
    if tool_name == "write_file" and args.get("path"):
        path = str(args["path"])
        return {
            "artifact_id": f"file:{path}",
            "kind": "file",
            "title": path.rsplit("/", 1)[-1] or path,
            "local_path": path,
            "created_at": now,
        }
    if tool_name == "image_generate":
        image = data.get("image") or data.get("url") or data.get("path")
        if image:
            return {
                "artifact_id": f"image:{image}",
                "kind": "image",
                "title": args.get("prompt", "generated image")[:120],
                "uri": image,
                "created_at": now,
            }
    if tool_name == "mcp_gbrain_knowledge_get_page" and args.get("slug"):
        slug = str(args["slug"])
        return {
            "artifact_id": f"gbrain:{slug}",
            "kind": "page",
            "title": slug,
            "uri": f"gbrain://{slug}",
            "retrieval_scope": "gbrain",
            "created_at": now,
        }
    if tool_name.startswith("mcp_notion_notion_get_page") and args.get("page_id"):
        page_id = str(args["page_id"])
        return {
            "artifact_id": f"notion:{page_id}",
            "kind": "page",
            "title": page_id,
            "uri": f"notion://{page_id}",
            "created_at": now,
        }
    return None


def register_tool_artifact(agent: Any, tool_name: str, args: Dict[str, Any], result: Any, *, failed: bool = False) -> None:
    if failed:
        return
    store = active_state_store_for_agent(agent)
    if store is None:
        return
    artifact = artifact_from_tool_result(tool_name, args, result)
    if artifact is None:
        return
    scope = scope_from_agent(agent)
    artifact.setdefault("source_tool", tool_name)
    artifact.setdefault("source_session_id", getattr(agent, "session_id", None))
    store.register_artifact(scope, artifact)


def record_tool_route_observation(agent: Any, tool_name: str) -> None:
    """Record the first actual tool family for route-compliance metrics."""
    store = active_state_store_for_agent(agent)
    if store is None:
        return
    try:
        from agent.gbrain_route import tool_family

        turn_id = getattr(agent, "_current_turn_id", None)
        store.record_first_tool(scope_from_agent(agent), tool_name=tool_name, tool_family=tool_family(tool_name), turn_id=turn_id)
    except Exception:
        return


def record_compaction_handoff(
    agent: Any,
    *,
    old_session_id: str,
    new_session_id: str,
    before_count: int,
    after_count: int,
    raw_window: Optional[list] = None,
    retry_once: bool = True,
) -> bool:
    """Persist an audited handoff marker after context compression rotates sessions.

    Returns True on verified persistence. On audit write failure it retries once;
    if persistence still fails, it preserves a bounded raw-window marker on the
    agent object so the caller can abort/continue without silently losing state.
    """
    store = active_state_store_for_agent(agent)
    if store is None:
        return False
    scope = scope_from_agent(agent)
    state = store.get(scope)
    handoff = {
        "kind": "context_compaction",
        "old_session_id": old_session_id,
        "new_session_id": new_session_id,
        "before_message_count": before_count,
        "after_message_count": after_count,
        "active_artifact_count": len(state.active_artifacts),
        "unresolved_ask_count": len(state.unresolved_asks),
        "current_task": state.current_task,
        "created_at": time.time(),
    }
    attempts = 2 if retry_once else 1
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            store.record_handoff(scope, handoff)
            persisted = store.get(scope).handoff or {}
            if (
                persisted.get("kind") == "context_compaction"
                and persisted.get("old_session_id") == old_session_id
                and persisted.get("new_session_id") == new_session_id
            ):
                return True
            last_error = RuntimeError("compaction handoff audit verification failed")
        except Exception as exc:
            last_error = exc
    preserved = {
        "kind": "context_compaction_audit_failed",
        "old_session_id": old_session_id,
        "new_session_id": new_session_id,
        "before_message_count": before_count,
        "after_message_count": after_count,
        "current_task": state.current_task,
        "raw_window": list(raw_window or [])[-20:],
        "error": str(last_error)[:500] if last_error else "unknown",
        "created_at": time.time(),
    }
    try:
        setattr(agent, "_pending_compaction_raw_window", preserved)
    except Exception:
        pass
    try:
        emit_warning = getattr(agent, "_emit_warning", None)
        if callable(emit_warning):
            emit_warning("⚠️ Compaction audit failed; preserved raw window and skipped silent handoff loss.")
    except Exception:
        pass
    return False
