"""Gateway lifecycle control tools.

These tools are only available inside a live messaging gateway process.  They
let the agent request the same controlled restart path as the user-facing
/restart command without trying to emit a slash command as normal text.
"""

from __future__ import annotations

import json
from typing import Any

from tools.registry import registry


def _get_gateway_runner() -> Any | None:
    """Return the active GatewayRunner, if this process owns one."""
    try:
        from gateway.run import _gateway_runner_ref  # imported lazily: avoid import cycles

        return _gateway_runner_ref()
    except Exception:
        return None


def check_gateway_control_requirements() -> bool:
    """Expose gateway-control tools only inside an active gateway process."""
    runner = _get_gateway_runner()
    if runner is None:
        return False
    return bool(getattr(runner, "_running", False) or getattr(runner, "adapters", None))


def gateway_restart_tool(
    *,
    reason: str,
    resume_current_task: bool = True,
    cooldown_seconds: int | None = None,
    gateway_session_key: str | None = None,
) -> str:
    """Request a controlled gateway restart through GatewayRunner."""
    runner = _get_gateway_runner()
    if runner is None:
        return json.dumps(
            {"success": False, "error": "gateway runner is not available in this process"},
            ensure_ascii=False,
        )

    request_fn = getattr(runner, "request_agent_restart", None)
    if request_fn is None:
        return json.dumps(
            {"success": False, "error": "gateway runner does not support agent restarts"},
            ensure_ascii=False,
        )

    try:
        result = request_fn(
            session_key=gateway_session_key,
            reason=reason,
            resume_current_task=resume_current_task,
            cooldown_seconds=cooldown_seconds,
        )
    except Exception as exc:
        return json.dumps(
            {"success": False, "error": f"gateway restart request failed: {type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )
    return json.dumps(result, ensure_ascii=False)


_GATEWAY_RESTART_SCHEMA = {
    "name": "gateway_restart",
    "description": (
        "Request a controlled restart of the Hermes messaging gateway. Use this only when "
        "a restart is required for gateway/Hermes code, config, env, toolset, skill, or "
        "provider changes to take effect, or when the gateway is in a known degraded state "
        "that a restart fixes. Do not use for ordinary task errors. The gateway will preserve "
        "the current session and can auto-resume after startup."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short operator-facing reason for the restart.",
            },
            "resume_current_task": {
                "type": "boolean",
                "description": (
                    "When true, mark the current gateway session for restart recovery and "
                    "interrupt the current agent turn so startup auto-resume continues the task."
                ),
                "default": True,
            },
            "cooldown_seconds": {
                "type": "integer",
                "description": "Optional restart-loop guard. Defaults to 300 seconds.",
                "default": 300,
            },
        },
        "required": ["reason"],
    },
}


registry.register(
    name="gateway_restart",
    toolset="gateway_control",
    schema=_GATEWAY_RESTART_SCHEMA,
    handler=lambda args, **kw: gateway_restart_tool(
        reason=args.get("reason", "agent requested gateway restart"),
        resume_current_task=bool(args.get("resume_current_task", True)),
        cooldown_seconds=args.get("cooldown_seconds"),
        gateway_session_key=kw.get("gateway_session_key"),
    ),
    check_fn=check_gateway_control_requirements,
    description=_GATEWAY_RESTART_SCHEMA["description"],
    emoji="♻️",
)
