import json

from agent.route_judge import (
    RouteJudgeStatus,
    parse_route_judge_json,
    validate_route_judge,
)
from agent.route_policies import BUILTIN_ROUTE_POLICIES, ROUTE_NOTION_PAGE_ANALYSIS


def _valid_raw(**overrides):
    data = {
        "route": ROUTE_NOTION_PAGE_ANALYSIS,
        "confidence": 0.91,
        "intent_summary": "Analyze a private Notion transcript.",
        "primary_tool_classes": ["notion_api"],
        "required_next_tools": ["mcp_notion_notion_get_page"],
        "blocked_before_primary": ["browser_ui", "generic_web"],
        "escalation_reason": None,
        "rationale": "Notion URL plus analysis intent.",
    }
    data.update(overrides)
    return parse_route_judge_json(json.dumps(data))


def test_route_judge_accepts_valid_notion_schema():
    raw = _valid_raw()
    envelope = validate_route_judge(
        raw,
        policies=BUILTIN_ROUTE_POLICIES,
        available_tools=["mcp_notion_notion_get_page", "mcp_notion_notion_get_block_children"],
    )

    assert envelope.accepted
    assert envelope.status == RouteJudgeStatus.ACCEPTED.value
    assert envelope.route == ROUTE_NOTION_PAGE_ANALYSIS
    assert envelope.primary_tool_classes == ("notion_api",)


def test_route_judge_rejects_malformed_json():
    envelope = validate_route_judge(parse_route_judge_json("not-json"), policies=BUILTIN_ROUTE_POLICIES)

    assert not envelope.accepted
    assert envelope.status == RouteJudgeStatus.MALFORMED_JSON.value


def test_route_judge_rejects_unknown_route():
    raw = _valid_raw(route="new_unreviewed_route")
    envelope = validate_route_judge(raw, policies=BUILTIN_ROUTE_POLICIES)

    assert not envelope.accepted
    assert envelope.status == RouteJudgeStatus.UNKNOWN_ROUTE.value


def test_route_judge_rejects_low_confidence():
    raw = _valid_raw(confidence=0.20)
    envelope = validate_route_judge(raw, policies=BUILTIN_ROUTE_POLICIES)

    assert not envelope.accepted
    assert envelope.status == RouteJudgeStatus.LOW_CONFIDENCE.value


def test_route_judge_rejects_unknown_tool_class():
    raw = _valid_raw(primary_tool_classes=["magic_private_api"])
    envelope = validate_route_judge(raw, policies=BUILTIN_ROUTE_POLICIES)

    assert not envelope.accepted
    assert envelope.status == RouteJudgeStatus.UNAUTHORIZED_TOOL_CLASS.value


def test_route_judge_rejects_required_tool_outside_policy():
    raw = _valid_raw(required_next_tools=["mcp_browser_use_browser_navigate"])
    envelope = validate_route_judge(raw, policies=BUILTIN_ROUTE_POLICIES)

    assert not envelope.accepted
    assert envelope.status == RouteJudgeStatus.UNAUTHORIZED_TOOL.value


def test_route_judge_rejects_escalation_outside_policy():
    raw = _valid_raw(escalation_reason="made_up_reason")
    envelope = validate_route_judge(raw, policies=BUILTIN_ROUTE_POLICIES)

    assert not envelope.accepted
    assert envelope.status == RouteJudgeStatus.UNAUTHORIZED_ESCALATION.value


def test_route_judge_rejects_conflict_with_deterministic_route():
    raw = _valid_raw()
    envelope = validate_route_judge(
        raw,
        policies=BUILTIN_ROUTE_POLICIES,
        deterministic_route_hint="browser_dom_operation",
    )

    assert not envelope.accepted
    assert envelope.status == RouteJudgeStatus.CONFLICTS_WITH_DETERMINISTIC_ROUTE.value
