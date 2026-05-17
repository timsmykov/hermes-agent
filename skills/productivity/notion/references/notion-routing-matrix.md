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
   - Upload local file/image: `mcp_notion_notion_upload_file`
   - Upload + append media block: `mcp_notion_notion_upload_and_append_file_block`
   - Attach existing upload id: `mcp_notion_notion_append_uploaded_file_block`
   - Set page cover from local image: `mcp_notion_notion_set_page_cover_from_file`
   - Health: `mcp_notion_notion_health`
2. `ntn` CLI
   - Installed on Tim's server at `/usr/local/bin/ntn` (`ntn 0.14.0`); use `/usr/local/bin/ntn-hermes` by default so Hermes `.env` is loaded and `NOTION_API_KEY` is mapped to `NOTION_API_TOKEN` without printing secrets.
   - Use for self-documenting API exploration (`ntn-hermes api ls`, `ntn-hermes api ... --help/--docs/--spec`), Markdown page create/update, Workers, and file uploads (`ntn-hermes files create < image.png`).
   - `/usr/local/bin/ntn-hermes` handles token env; do not print the token.
3. Direct REST API with `NOTION_API_KEY`
   - Use for endpoints not exposed by MCP/CLI, full JSON payloads, pagination/recursion, schema inspection, data-source/database query compatibility, comments inventory, bulk transforms, or exact File Upload API control.
4. Browser/UI automation
   - Use only for login/permission sharing, Notion AI/Agent chat UI, custom-agent configuration/manual run, model picker, view/filter UI, or features unavailable through MCP/API/CLI.

## Do not do this

- Do not open Notion in browser for ordinary search/read/create/update.
- Do not open Notion in browser for image insertion until MCP upload, `ntn files`, and direct File Uploads API paths have been tried or ruled out.
- Do not use browser clicking to compensate for an API 404 until integration access and endpoint version have been checked.
- Do not use custom Notion agents unless Tim explicitly asks for a named custom agent.
- Do not mix MCP, direct API, browser, and generic web tools in a panic loop. Diagnose one layer at a time.

## Error handling

- MCP tool missing: run `hermes mcp list` / `hermes mcp test notion`; if newly configured, gateway restart/reset may be needed for tool discovery.
- `401/403`: token or integration permission problem. Check `NOTION_API_KEY` and whether the integration is connected to the target page/database.
- `404`: usually unshared object, wrong ID, or endpoint-version mismatch. For databases/data sources, retry the legacy `/v1/databases/{id}/query` with `Notion-Version: 2022-06-28` before declaring unavailable.
- Rate limit: back off; do not switch to UI.
- Comments API returns zero: treat as an observation, not proof that human-visible comments do not exist. If comments are expected, UI verification can be justified.

## Media and image insertion

Images/files are API-supported and should not default to UI.

Preferred paths:

1. MCP upload tools:
   - `mcp_notion_notion_upload_and_append_file_block(parent_block_id, local_file_path, block_type="image")`
   - `mcp_notion_notion_upload_file(local_file_path)` then `mcp_notion_notion_append_uploaded_file_block(...)`
   - `mcp_notion_notion_set_page_cover_from_file(page_id, local_file_path)` for covers.
2. `ntn` CLI:
   - `ntn-hermes files create < image.png`
   - `ntn-hermes files create --external-url https://example.com/photo.png`
   - attach returned `file_upload` id with `ntn-hermes api` or REST.
3. Direct REST API:
   - create file upload, send multipart data, attach as `image`/`file`/`pdf`/`audio`/`video` block, database `files` property, icon, or cover.

UI upload is allowed only when the above fail because of a concrete observed limitation, permissions problem that cannot be solved by integration sharing, unsupported local file access, or an explicitly visual drag/drop workflow requested by Tim.

## Browser-allowed examples

- Tim asks to use Notion AI / Notion Agent in the Notion chat UI.
- Tim asks to configure or run a named custom Notion agent.
- Tim asks to change a database view, visible filter, layout, or permission sharing not exposed by REST API.
- API access is blocked and the task is explicitly about visual/UI verification.
