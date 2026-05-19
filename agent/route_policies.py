"""Built-in RouteGuard route contracts.

Route-specific knowledge belongs here as data. The runtime harness should stay
thin and evaluate generic contracts rather than growing route-specific branch
forests.
"""

from __future__ import annotations

from agent.route_judge import EscalationReason, RoutePolicy, ToolClass

ROUTE_NOTION_PAGE_ANALYSIS = "notion_page_analysis"

NOTION_PAGE_ANALYSIS_POLICY = RoutePolicy(
    route=ROUTE_NOTION_PAGE_ANALYSIS,
    primary_tool_classes=(ToolClass.NOTION_API.value,),
    required_next_tools=(
        "mcp_notion_notion_get_page",
        "mcp_notion_notion_get_block_children",
    ),
    blocked_before_primary=(
        ToolClass.BROWSER_UI.value,
        ToolClass.GENERIC_WEB.value,
    ),
    allowed_escalations=(
        EscalationReason.UI_ONLY_REQUESTED.value,
        EscalationReason.NOTION_AI_REQUESTED.value,
        EscalationReason.PERMISSION_OR_SHARE_UI_REQUESTED.value,
        EscalationReason.API_ACCESS_DENIED_AFTER_HEALTH_OK.value,
        EscalationReason.API_ENDPOINT_UNSUPPORTED.value,
        EscalationReason.COMMENTS_API_EMPTY_BUT_COMMENTS_EXPECTED.value,
        EscalationReason.USER_EXPLICITLY_REQUESTED_BROWSER.value,
    ),
    confidence_threshold=0.80,
    enforceable=True,
)

BUILTIN_ROUTE_POLICIES: dict[str, RoutePolicy] = {
    ROUTE_NOTION_PAGE_ANALYSIS: NOTION_PAGE_ANALYSIS_POLICY,
}
