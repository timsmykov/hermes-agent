"""RouteGuard tests for API-first Notion routing."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.route_guard import RouteGuardConfig, RouteGuardController, append_routeguard_guidance, classify_tool
from agent.route_judge import RouteJudgeConfig
from agent.route_guard_metrics import RouteGuardMetricsConfig, load_metric_events
from run_agent import AIAgent


def _make_tool_defs(*names: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _mock_tool_call(name: str, arguments: dict | None = None, call_id: str | None = None):
    return SimpleNamespace(
        id=call_id or f"call_{uuid.uuid4().hex[:8]}",
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments or {})),
    )


def _make_agent(*tool_names: str, config: dict | None = None) -> AIAgent:
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs(*tool_names)),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value=config or {}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            max_iterations=5,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def _route_guard_config(mode: str = "enforce") -> dict:
    return {
        "route_guard": {
            "enabled": True,
            "mode": mode,
            "trace_enabled": True,
            "metrics": {"enabled": False},
        }
    }


def _disabled_metrics_config():
    return RouteGuardMetricsConfig(enabled=False)


def test_classifies_mcp_and_bypass_tool_classes():
    assert classify_tool("mcp_notion_notion_get_page") == "notion_api"
    assert classify_tool("mcp_browser_use_browser_navigate") == "browser_ui"
    assert classify_tool("web_extract") == "generic_web"
    assert classify_tool("terminal", {"command": "python -m pytest"}) == "shell_escape"
    assert classify_tool("delegate_task") == "delegation"
    assert classify_tool("cronjob") == "scheduling"


def test_notion_page_analysis_blocks_browser_before_notion_api_in_enforce_mode():
    guard = RouteGuardController(RouteGuardConfig(enabled=True, mode="enforce", metrics=_disabled_metrics_config()))
    guard.reset_for_turn("https://www.notion.so/acme Внимательно проанализируй транскрипцию созвона")

    decision = guard.before_call("mcp_browser_use_browser_navigate", {"url": "https://www.notion.so/acme"})

    assert not decision.allows_execution
    assert decision.action == "block"
    assert decision.route == "notion_page_analysis"
    assert decision.tool_class == "browser_ui"
    assert "mcp_notion_notion_get_page" in decision.required_next_tools


def test_notion_page_analysis_allows_notion_api_and_then_browser_after_api_failure():
    guard = RouteGuardController(RouteGuardConfig(enabled=True, mode="enforce", metrics=_disabled_metrics_config()))
    guard.reset_for_turn("https://www.notion.so/acme сделай summary по странице")

    first = guard.before_call("mcp_notion_notion_get_page", {"page_id": "acme"})
    assert first.allows_execution

    guard.after_call("mcp_notion_notion_get_page", {"page_id": "acme"}, json.dumps({"error": "unauthorized"}), failed=True)
    fallback = guard.before_call("mcp_browser_use_browser_navigate", {"url": "https://www.notion.so/acme"})

    assert fallback.allows_execution
    assert fallback.code == "api_access_denied_after_health_ok"


def test_sequential_dispatch_blocks_notion_browser_bypass_without_calling_tool():
    agent = _make_agent(
        "mcp_browser_use_browser_navigate",
        "mcp_notion_notion_get_page",
        config=_route_guard_config("enforce"),
    )
    agent._route_guard.reset_for_turn("https://www.notion.so/acme проанализируй транскрипцию")
    msg = SimpleNamespace(
        content="",
        tool_calls=[_mock_tool_call("mcp_browser_use_browser_navigate", {"url": "https://www.notion.so/acme"}, "c-browser")],
    )
    messages = []

    with patch("run_agent.handle_function_call", return_value="SHOULD_NOT_RUN") as mock_hfc:
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    mock_hfc.assert_not_called()
    assert len(messages) == 1
    payload = json.loads(messages[0]["content"])
    assert payload["blocked_by"] == "route_guard"
    assert payload["route"] == "notion_page_analysis"
    assert payload["tool_class"] == "browser_ui"


def test_invoke_tool_blocks_terminal_notion_browser_bypass():
    agent = _make_agent("terminal", config=_route_guard_config("enforce"))
    agent._route_guard.reset_for_turn("https://www.notion.so/acme сделай HTML roadmap по странице")

    result = agent._invoke_tool(
        "terminal",
        {"command": "python - <<'PY'\nimport requests\nrequests.get('https://www.notion.so/acme')\nPY"},
        "task-1",
    )

    payload = json.loads(result)
    assert payload["blocked_by"] == "route_guard"
    assert payload["tool_class"] == "shell_escape"


ROUTE_GUARD_TRAJECTORY_FIXTURES = [
    {
        "id": "notion_browser_first_blocked",
        "user": "https://www.notion.so/acme проанализируй транскрипцию созвона",
        "mode": "enforce",
        "tools": [
            {
                "name": "mcp_browser_use_browser_navigate",
                "args": {"url": "https://www.notion.so/acme"},
                "expect": {"executed": False, "tool_class": "browser_ui", "decision": "block_wrong_route"},
            }
        ],
    },
    {
        "id": "notion_api_first_allowed",
        "user": "https://www.notion.so/acme сделай summary по странице",
        "mode": "enforce",
        "tools": [
            {
                "name": "mcp_notion_notion_get_page",
                "args": {"page_id": "acme"},
                "result": json.dumps({"title": "Acme"}),
                "expect": {"executed": True, "tool_class": "notion_api"},
            }
        ],
    },
    {
        "id": "notion_api_failure_then_browser_allowed",
        "user": "https://www.notion.so/acme сделай summary по странице",
        "mode": "enforce",
        "tools": [
            {
                "name": "mcp_notion_notion_get_page",
                "args": {"page_id": "acme"},
                "result": json.dumps({"error": "unauthorized"}),
                "expect": {"executed": True, "tool_class": "notion_api"},
            },
            {
                "name": "mcp_browser_use_browser_navigate",
                "args": {"url": "https://www.notion.so/acme"},
                "result": "browser opened",
                "expect": {"executed": True, "tool_class": "browser_ui"},
            },
        ],
    },
    {
        "id": "notion_web_extract_first_blocked",
        "user": "https://www.notion.so/acme подготовь roadmap/html артефакт по странице",
        "mode": "enforce",
        "tools": [
            {
                "name": "web_extract",
                "args": {"urls": ["https://www.notion.so/acme"]},
                "expect": {"executed": False, "tool_class": "generic_web", "decision": "block_wrong_route"},
            }
        ],
    },
    {
        "id": "notion_shell_browser_bypass_blocked",
        "user": "https://www.notion.so/acme сделай HTML roadmap по странице",
        "mode": "enforce",
        "tools": [
            {
                "name": "terminal",
                "args": {"command": "python - <<'PY'\nimport requests\nrequests.get('https://www.notion.so/acme')\nPY"},
                "expect": {"executed": False, "tool_class": "shell_escape", "decision": "block_wrong_route"},
            }
        ],
    },
]


def _fake_tool_result_for_fixture(fixture):
    def _fake(name, args, *_unused, **_kwargs):
        for step in fixture["tools"]:
            if step["name"] == name:
                return step.get("result", json.dumps({"ok": True}))
        return json.dumps({"ok": True})

    return _fake


def _run_route_fixture(fixture):
    tool_names = sorted({step["name"] for step in fixture["tools"]})
    agent = _make_agent(*tool_names, config=_route_guard_config(fixture.get("mode", "enforce")))
    agent._route_guard.reset_for_turn(fixture["user"])
    msg = SimpleNamespace(
        content="",
        tool_calls=[_mock_tool_call(step["name"], step.get("args", {}), f"call-{idx}") for idx, step in enumerate(fixture["tools"])],
    )
    messages = []
    with patch("run_agent.handle_function_call", side_effect=_fake_tool_result_for_fixture(fixture)) as mock_hfc:
        agent._execute_tool_calls_sequential(msg, messages, "route-fixture")
    return messages, mock_hfc


def test_route_guard_tool_trajectory_fixtures():
    for fixture in ROUTE_GUARD_TRAJECTORY_FIXTURES:
        messages, mock_hfc = _run_route_fixture(fixture)
        executed_names = [call.args[0] for call in mock_hfc.call_args_list]
        assert len(messages) == len(fixture["tools"]), fixture["id"]
        for step, message in zip(fixture["tools"], messages, strict=True):
            expect = step["expect"]
            assert message["name"] == step["name"], fixture["id"]
            if expect["executed"]:
                assert step["name"] in executed_names, fixture["id"]
            else:
                assert step["name"] not in executed_names, fixture["id"]
                payload = json.loads(message["content"])
                assert payload["blocked_by"] == "route_guard", fixture["id"]
                assert payload["route"] == "notion_page_analysis", fixture["id"]
                assert payload["tool_class"] == expect["tool_class"], fixture["id"]
                assert payload["decision"] == expect["decision"], fixture["id"]


def test_observe_mode_is_trace_only_and_does_not_append_tokens():
    controller = RouteGuardController(RouteGuardConfig(enabled=True, mode="observe", metrics=_disabled_metrics_config()))
    controller.reset_for_turn("https://www.notion.so/acme прочитай этот документ")
    decision = controller.before_call("mcp_browser_use_browser_navigate", {"url": "https://www.notion.so/acme"})

    assert decision.action == "observe"
    assert append_routeguard_guidance("browser result", decision) == "browser result"


def test_warn_mode_appends_compact_guidance_only():
    controller = RouteGuardController(RouteGuardConfig(enabled=True, mode="warn", metrics=_disabled_metrics_config()))
    controller.reset_for_turn("https://www.notion.so/acme read this Notion doc and tell me what it says")
    decision = controller.before_call("web_extract", {"urls": ["https://www.notion.so/acme"]})
    result = append_routeguard_guidance("web result", decision)

    assert decision.action == "warn"
    assert "RouteGuard warn" in result
    assert len(result) < 220


def test_routejudge_can_set_route_once_when_deterministic_detector_is_uncertain():
    calls = []

    def judge(text: str):
        calls.append(text)
        return {
            "route": "notion_page_analysis",
            "confidence": 0.93,
            "intent_summary": "Analyze private Notion page",
            "primary_tool_classes": ["notion_api"],
            "required_next_tools": ["mcp_notion_notion_get_page"],
            "blocked_before_primary": ["browser_ui", "generic_web"],
            "escalation_reason": None,
            "rationale": "Semantic judge saw a Notion analysis request.",
        }

    controller = RouteGuardController(
        RouteGuardConfig(
            enabled=True,
            mode="enforce",
            judge=RouteJudgeConfig(enabled=True),
            metrics=_disabled_metrics_config(),
        ),
        route_judge=judge,
    )
    controller.reset_for_turn("https://www.notion.so/acme что там?")
    decision = controller.before_call("mcp_browser_use_browser_navigate", {"url": "https://www.notion.so/acme"})

    assert calls == ["https://www.notion.so/acme что там?"]
    assert controller.state.judge_envelope.accepted
    assert controller.active_route == "notion_page_analysis"
    assert decision.action == "block"


def test_routejudge_is_not_called_when_deterministic_route_is_known():
    def judge(_text: str):
        raise AssertionError("judge should not be called for high-confidence deterministic route")

    controller = RouteGuardController(
        RouteGuardConfig(
            enabled=True,
            mode="enforce",
            judge=RouteJudgeConfig(enabled=True),
            metrics=_disabled_metrics_config(),
        ),
        route_judge=judge,
    )
    controller.reset_for_turn("https://www.notion.so/acme проанализируй транскрипцию")

    assert controller.active_route == "notion_page_analysis"
    assert controller.state.judge_envelope.status == "not_called"


def test_runtime_metrics_persist_events_and_refresh_dashboard(tmp_path):
    metrics_path = tmp_path / "metrics.jsonl"
    dashboard_path = tmp_path / "dashboard.json"
    controller = RouteGuardController(
        RouteGuardConfig(
            enabled=True,
            mode="observe",
            metrics=RouteGuardMetricsConfig(
                enabled=True,
                path=str(metrics_path),
                dashboard_path=str(dashboard_path),
            ),
        )
    )

    controller.reset_for_turn("https://www.notion.so/acme проанализируй транскрипцию")
    decision = controller.before_call("mcp_browser_use_browser_navigate", {"url": "https://www.notion.so/acme"})
    controller.after_call("mcp_browser_use_browser_navigate", {"url": "https://www.notion.so/acme"}, "opened", failed=False)

    events = load_metric_events(metrics_path)
    assert [event["event_type"] for event in events] == ["route_detected", "route_decision", "tool_result"]
    assert events[0]["route"] == "notion_page_analysis"
    assert events[1]["action"] == "observe"
    assert events[1]["code"] == "wrong_route_observed"
    assert decision.action == "observe"

    dashboard = json.loads(dashboard_path.read_text())
    assert dashboard["total_routed_turns_since_v2"] == 1
    assert dashboard["wrong_tool_first_call_rate"] == 1.0
    assert dashboard["token_budget_status"] == "ok"
