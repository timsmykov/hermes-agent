from collections import OrderedDict

from gateway.run import (
    _append_progress_block_line,
    _format_subagent_progress_line,
    _render_progress_blocks,
)


def test_formats_subagent_lifecycle_events():
    start = _format_subagent_progress_line(
        "subagent.start",
        preview="Investigate issue",
        task_index=0,
        task_count=2,
    )
    done = _format_subagent_progress_line(
        "subagent.complete",
        preview="done",
        task_index=0,
        task_count=2,
    )

    assert start == '🤖 start: "Investigate issue"'
    assert done == "✅ done: done"


def test_formats_subagent_tool_event_with_preview_cap():
    line = _format_subagent_progress_line(
        "subagent.tool",
        tool_name="terminal",
        preview="hermes kanban boards list --very-long-extra-detail",
        task_index=1,
        task_count=2,
        preview_cap=24,
    )

    assert line.startswith("  ↳ 💻 terminal: ")
    assert "hermes kanban boards ..." in line


def test_ignores_non_subagent_events():
    assert _format_subagent_progress_line("tool.started", tool_name="terminal") is None


def test_hides_subagent_thinking_reasoning_from_telegram_progress():
    assert _format_subagent_progress_line(
        "subagent.thinking",
        preview="Need modify loop to combine table caption.",
        task_index=0,
        task_count=1,
    ) is None

def test_renders_structured_progress_blocks_with_stable_agent_sections():
    blocks = OrderedDict()
    _append_progress_block_line(blocks, "main", "🧭 main agent", "🔀 delegate_task: \"3 agents\"", pinned=True)
    _append_progress_block_line(blocks, "agent:0", "🤖 agent 1 — Investigate", "🤖 start: \"Investigate\"", pinned=True)
    _append_progress_block_line(blocks, "agent:1", "🤖 agent 2 — Verify", "🤖 start: \"Verify\"", pinned=True)
    _append_progress_block_line(blocks, "agent:0", "🤖 agent 1 — Investigate", "  ↳ 🔎 search_files: \"RouteGuard\"")
    _append_progress_block_line(blocks, "agent:1", "🤖 agent 2 — Verify", "  ↳ 📖 read_file: \"gateway/run.py\"")

    rendered = _render_progress_blocks(blocks)

    assert "🧭 main agent" in rendered
    assert "🤖 agent 1 — Investigate" in rendered
    assert "🤖 agent 2 — Verify" in rendered
    assert rendered.index("🤖 agent 1 — Investigate") < rendered.index("🤖 agent 2 — Verify")
    assert "\n━━━━━━━━━━━━━━━━\n🤖 agent 1" in rendered
    assert "agent 1 search_files" not in rendered
    assert "agent 2 read_file" not in rendered


def test_renders_agent_blocks_in_numeric_order_even_when_events_arrive_out_of_order():
    blocks = OrderedDict()
    _append_progress_block_line(blocks, "main", "🧭 main agent", "🔀 delegate_task: \"3 agents\"", pinned=True)
    _append_progress_block_line(blocks, "agent:2", "🤖 agent 3 — Third", "🤖 start: \"Third\"", pinned=True)
    _append_progress_block_line(blocks, "agent:0", "🤖 agent 1 — First", "🤖 start: \"First\"", pinned=True)
    _append_progress_block_line(blocks, "agent:1", "🤖 agent 2 — Second", "🤖 start: \"Second\"", pinned=True)

    rendered = _render_progress_blocks(blocks)

    assert rendered.index("🧭 main agent") < rendered.index("🤖 agent 1 — First")
    assert rendered.index("🤖 agent 1 — First") < rendered.index("🤖 agent 2 — Second")
    assert rendered.index("🤖 agent 2 — Second") < rendered.index("🤖 agent 3 — Third")
    assert rendered.count("━━━━━━━━━━━━━━━━") == 3
    assert "agent 1/3" not in rendered


def test_progress_block_keeps_important_lines_while_trimming_to_visible_limit():
    blocks = OrderedDict()
    _append_progress_block_line(blocks, "agent:0", "🤖 agent 1", "🤖 start", pinned=True, visible_limit=4)
    for idx in range(8):
        _append_progress_block_line(blocks, "agent:0", "🤖 agent 1", f"step {idx}", visible_limit=4)
    _append_progress_block_line(blocks, "agent:0", "🤖 agent 1", "✅ done", pinned=True, visible_limit=4)

    rendered = _render_progress_blocks(blocks)

    assert "🤖 start" in rendered
    assert "✅ done" in rendered
    assert "step 0" not in rendered
    assert "step 7" in rendered
    assert "earlier steps" in rendered


def test_progress_block_replaces_thinking_line_instead_of_appending_noise():
    blocks = OrderedDict()
    _append_progress_block_line(blocks, "agent:0", "🤖 agent 1", "💭: \"thinking\"", replace_kind="thinking")
    _append_progress_block_line(blocks, "agent:0", "🤖 agent 1", "💭: \"analyzing\"", replace_kind="thinking")

    rendered = _render_progress_blocks(blocks)

    assert "thinking" not in rendered
    assert "analyzing" in rendered
    assert rendered.count("💭") == 1
