"""Regression guards for transient API retry status cleanup in gateway chats."""
from __future__ import annotations

import inspect

from agent import conversation_loop
from gateway import run as gateway_run


def test_agent_clears_api_retry_status_after_successful_retry():
    src = inspect.getsource(conversation_loop)

    assert 'agent._clear_status("api_retry")' in src, (
        "A successful API call after a retry must clear transient retry "
        "bubbles from Telegram/gateway chats."
    )
    assert src.index('has_retried_429 = False  # Reset on success') < src.index(
        'agent._clear_status("api_retry")'
    ), "api_retry cleanup should live on the successful-call path."


def test_gateway_tracks_retry_status_bubbles_by_api_retry_key():
    src = inspect.getsource(gateway_run.GatewayRunner._run_agent)

    assert '_status_futures_by_key.setdefault("api_retry", []).append(_fut)' in src
    assert 'message.startswith("⏳ Retrying in ")' in src
    assert 'message.startswith("⏱️ Rate limited. Waiting ")' in src
    assert 'event_type == "lifecycle.clear"' in src
