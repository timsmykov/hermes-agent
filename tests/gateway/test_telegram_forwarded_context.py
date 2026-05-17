"""Tests for Telegram forwarded-message context handling."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._text_batch_delay_seconds = 10
    adapter._text_batch_split_delay_seconds = 10
    adapter._TEXT_BATCH_FAST_DELAY_S = 10
    adapter._TEXT_BATCH_SHORT_DELAY_S = 10
    return adapter


def _source(adapter):
    return adapter.build_source(
        chat_id="111",
        chat_name="Tim",
        chat_type="dm",
        user_id="42",
        user_name="Tim",
    )


def test_forwarded_text_is_marked_as_forwarded_context():
    adapter = _make_adapter()
    msg = SimpleNamespace(forward_origin=object())

    result = adapter._decorate_forwarded_text(msg, "пересланный текст")

    assert result == "[Forwarded Telegram message]\nпересланный текст"


def test_forwarded_file_without_caption_gets_context_marker():
    adapter = _make_adapter()
    msg = SimpleNamespace(forward_origin=object())

    result = adapter._decorate_forwarded_text(msg, "", media_label="document")

    assert result == "[Forwarded Telegram document]"


@pytest.mark.asyncio
async def test_forwarded_file_event_merges_into_pending_text_prompt():
    adapter = _make_adapter()
    source = _source(adapter)

    prompt = MessageEvent(
        text="что в этом файле?",
        message_type=MessageType.TEXT,
        source=source,
        message_id="10",
    )
    adapter._enqueue_text_event(prompt)

    forwarded_file = MessageEvent(
        text="[Forwarded Telegram document]",
        message_type=MessageType.DOCUMENT,
        source=source,
        message_id="11",
        media_urls=["/tmp/forwarded.pdf"],
        media_types=["application/pdf"],
    )

    merged = adapter._enqueue_media_with_pending_text_if_any(forwarded_file)

    assert merged is True
    pending = adapter._pending_text_batches[adapter._text_batch_key(prompt)]
    assert pending.text == "что в этом файле?\n[Forwarded Telegram document]"
    assert pending.media_urls == ["/tmp/forwarded.pdf"]
    assert pending.media_types == ["application/pdf"]

    for task in list(adapter._pending_text_batch_tasks.values()):
        task.cancel()
    await asyncio.gather(*adapter._pending_text_batch_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_forwarded_file_first_waits_for_sibling_prompt_update():
    adapter = _make_adapter()
    source = _source(adapter)

    forwarded_file = MessageEvent(
        text="[Forwarded Telegram document]",
        message_type=MessageType.DOCUMENT,
        source=source,
        raw_message=SimpleNamespace(forward_origin=object()),
        message_id="10",
        media_urls=["/tmp/forwarded.pdf"],
        media_types=["application/pdf"],
    )

    merged = adapter._enqueue_media_with_text_batch_if_needed(forwarded_file)
    assert merged is True

    prompt = MessageEvent(
        text="что в этом файле?",
        message_type=MessageType.TEXT,
        source=source,
        message_id="11",
    )
    adapter._enqueue_text_event(prompt)

    pending = adapter._pending_text_batches[adapter._text_batch_key(prompt)]
    assert pending.text == "[Forwarded Telegram document]\nчто в этом файле?"
    assert pending.media_urls == ["/tmp/forwarded.pdf"]
    assert pending.media_types == ["application/pdf"]

    for task in list(adapter._pending_text_batch_tasks.values()):
        task.cancel()
    await asyncio.gather(*adapter._pending_text_batch_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_media_group_flush_merges_with_pending_prompt():
    adapter = _make_adapter()
    adapter.MEDIA_GROUP_WAIT_SECONDS = 0.01
    adapter.handle_message = AsyncMock()
    source = _source(adapter)

    prompt = MessageEvent(
        text="сравни эти картинки",
        message_type=MessageType.TEXT,
        source=source,
        message_id="20",
    )
    adapter._enqueue_text_event(prompt)

    album = MessageEvent(
        text="[Forwarded Telegram photo]",
        message_type=MessageType.PHOTO,
        source=source,
        raw_message=SimpleNamespace(forward_origin=object()),
        message_id="21",
        media_urls=["/tmp/one.jpg", "/tmp/two.jpg"],
        media_types=["image/jpeg", "image/jpeg"],
    )
    adapter._media_group_events["album-1"] = album

    await adapter._flush_media_group_event("album-1")

    adapter.handle_message.assert_not_awaited()
    pending = adapter._pending_text_batches[adapter._text_batch_key(prompt)]
    assert pending.text == "сравни эти картинки\n[Forwarded Telegram photo]"
    assert pending.media_urls == ["/tmp/one.jpg", "/tmp/two.jpg"]

    for task in list(adapter._pending_text_batch_tasks.values()):
        task.cancel()
    await asyncio.gather(*adapter._pending_text_batch_tasks.values(), return_exceptions=True)
