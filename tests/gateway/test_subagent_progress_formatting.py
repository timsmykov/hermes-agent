from gateway.run import _format_subagent_progress_line


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

    assert start == '🤖 agent 1/2 start: "Investigate issue"'
    assert done == "✅ agent 1/2 done: done"


def test_formats_subagent_tool_event_with_preview_cap():
    line = _format_subagent_progress_line(
        "subagent.tool",
        tool_name="terminal",
        preview="hermes kanban boards list --very-long-extra-detail",
        task_index=1,
        task_count=2,
        preview_cap=24,
    )

    assert line.startswith("  ↳ 💻 agent 2/2 terminal: ")
    assert "hermes kanban boards ..." in line


def test_ignores_non_subagent_events():
    assert _format_subagent_progress_line("tool.started", tool_name="terminal") is None
