# Notion routing matrix for Tim's Hermes

## Default decision

Use Notion MCP/API first. Browser/UI is a last resort, not a fallback reflex.

## Priority order

1. `mcp_notion_*` tools
   - Search workspace: `mcp_notion_notion_search`
   - Page metadata: `mcp_notion_notion_get_page`
   - Block/page content: `mcp_notion_notion_get_block_children`
   - Append content: `mcp_notion_notion_append_blocks`
   - Replace text block: `mcp_notion_notion_update_block_text`
   - Comments: `mcp_notion_notion_get_comments`
   - Health: `mcp_notion_notion_health`
2. Direct REST API with `NOTION_API_KEY`
   - Use for endpoints not exposed by MCP, full JSON payloads, pagination/recursion, schema inspection, data-source/database query compatibility, comments inventory, or bulk transforms.
3. `ntn` CLI
   - Use only if installed and it materially helps: Workers, file uploads, markdown endpoints, or concise one-off API calls.
4. Browser/UI automation
   - Use only for login/permission sharing, Notion AI/Agent chat UI, custom-agent configuration/manual run, model picker, view/filter UI, or features unavailable through the API.

## Do not do this

- Do not open Notion in browser for ordinary search/read/create/update.
- Do not use browser clicking to compensate for an API 404 until integration access and endpoint version have been checked.
- Do not use custom Notion agents unless Tim explicitly asks for a named custom agent.
- Do not mix MCP, direct API, browser, and generic web tools in a panic loop. Diagnose one layer at a time.

## Error handling

- MCP tool missing: run `hermes mcp list` / `hermes mcp test notion`; if newly configured, gateway restart/reset may be needed for tool discovery.
- `401/403`: token or integration permission problem. Check `NOTION_API_KEY` and whether the integration is connected to the target page/database.
- `404`: usually unshared object, wrong ID, or endpoint-version mismatch. For databases/data sources, retry the legacy `/v1/databases/{id}/query` with `Notion-Version: 2022-06-28` before declaring unavailable.
- Rate limit: back off; do not switch to UI.
- Comments API returns zero: treat as an observation, not proof that human-visible comments do not exist. If comments are expected, UI verification can be justified.

## Browser-allowed examples

- Tim asks to use Notion AI / Notion Agent in the Notion chat UI.
- Tim asks to configure or run a named custom Notion agent.
- Tim asks to change a database view, visible filter, layout, or permission sharing not exposed by REST API.
- API access is blocked and the task is explicitly about visual/UI verification.
