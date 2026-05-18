"""Tests for Telegram busy-input choice prompt rendering."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, SessionSource
from gateway.platforms.telegram import TelegramAdapter


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._bot = AsyncMock()
    msg = MagicMock()
    msg.message_id = 4242
    adapter._send_message_with_thread_fallback = AsyncMock(return_value=msg)
    return adapter


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=MagicMock(value="telegram"),
            chat_id="12345",
            chat_type="dm",
            user_id="806409559",
            thread_id="463637",
        ),
        message_id="777",
    )


@pytest.mark.asyncio
async def test_busy_choice_prompt_does_not_repeat_user_message():
    adapter = _make_adapter()
    event = _make_event("это длинный prompt, который не должен повторяться в меню")

    result = await adapter.send_busy_choice_prompt(
        event,
        "telegram:12345:463637",
        status_detail="processing previous turn",
    )

    assert result.success is True
    kwargs = adapter._send_message_with_thread_fallback.call_args.kwargs
    text = kwargs["text"]
    assert "Что сделать с новым сообщением?" in text
    assert "processing previous turn" in text
    assert "это длинный prompt" not in text
    assert "<blockquote>" not in text
    assert kwargs["reply_markup"] is not None
