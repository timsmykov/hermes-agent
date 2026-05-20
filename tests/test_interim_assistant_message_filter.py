from run_agent import AIAgent


def _agent_with_callback(calls):
    agent = object.__new__(AIAgent)
    agent.interim_assistant_callback = lambda text, *, already_streamed=False: calls.append(
        (text, already_streamed)
    )
    agent._current_streamed_assistant_text = ""
    return agent


def test_suppresses_terse_tool_call_scratchpad_fragment():
    calls = []
    agent = _agent_with_callback(calls)

    agent._emit_interim_assistant_message(
        {
            "role": "assistant",
            "content": "Need conclusion updates.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "mcp_notion_notion_update_block_text", "arguments": "{}"},
                }
            ],
        }
    )

    assert calls == []


def test_keeps_user_facing_tool_call_commentary():
    calls = []
    agent = _agent_with_callback(calls)

    agent._emit_interim_assistant_message(
        {
            "role": "assistant",
            "content": "I'll inspect the repo first.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_files", "arguments": "{}"},
                }
            ],
        }
    )

    assert calls == [("I'll inspect the repo first.", False)]


def test_keeps_non_tool_interim_message():
    calls = []
    agent = _agent_with_callback(calls)

    agent._emit_interim_assistant_message(
        {"role": "assistant", "content": "You're welcome."}
    )

    assert calls == [("You're welcome.", False)]
