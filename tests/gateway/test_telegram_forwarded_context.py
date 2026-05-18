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


@pytest.fixture(autouse=True)
def _isolated_document_cache(monkeypatch, tmp_path):
    from gateway.platforms import base

    monkeypatch.setattr(base, "DOCUMENT_CACHE_DIR", tmp_path / "documents")


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


def test_falsy_forward_origin_still_counts_as_forwarded():
    class FalsyForwardOrigin:
        def __bool__(self):
            return False

    adapter = _make_adapter()
    msg = SimpleNamespace(forward_origin=FalsyForwardOrigin())

    result = adapter._decorate_forwarded_text(msg, "context")

    assert result == "[Forwarded Telegram message]\ncontext"


def _telegram_message(document, *, caption=None, forwarded=True):
    return SimpleNamespace(
        chat=SimpleNamespace(id=111, type="private", title=None, full_name="Tim", is_forum=False),
        from_user=SimpleNamespace(id=42, full_name="Tim"),
        message_thread_id=None,
        is_topic_message=False,
        reply_to_message=None,
        text=None,
        caption=caption,
        date=None,
        message_id=10,
        sticker=None,
        photo=None,
        video=None,
        audio=None,
        voice=None,
        document=document,
        media_group_id=None,
        forward_origin=object() if forwarded else None,
    )


@pytest.mark.asyncio
async def test_forwarded_too_large_document_preserves_context_marker():
    adapter = _make_adapter()
    adapter.handle_message = AsyncMock()
    doc = SimpleNamespace(
        file_name="large.pdf",
        mime_type="application/pdf",
        file_size=25 * 1024 * 1024,
    )
    update = SimpleNamespace(message=_telegram_message(doc), update_id=123)

    await adapter._handle_media_message(update, None)

    event = adapter.handle_message.await_args.args[0]
    assert event.text.startswith("[Forwarded Telegram document]")
    assert "too large" in event.text


@pytest.mark.asyncio
async def test_forwarded_unsupported_document_preserves_context_marker():
    adapter = _make_adapter()
    adapter.handle_message = AsyncMock()
    doc = SimpleNamespace(
        file_name="payload.exe",
        mime_type="application/x-msdownload",
        file_size=1024,
    )
    update = SimpleNamespace(message=_telegram_message(doc), update_id=123)

    await adapter._handle_media_message(update, None)

    event = adapter.handle_message.await_args.args[0]
    assert event.text.startswith("[Forwarded Telegram document]")
    assert "Unsupported document type '.exe'" in event.text


@pytest.mark.asyncio
async def test_forwarded_text_document_marker_precedes_injected_content():
    adapter = _make_adapter()
    adapter.handle_message = AsyncMock()
    file_obj = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"hello from file")))
    doc = SimpleNamespace(
        file_name="note.txt",
        mime_type="text/plain",
        file_size=15,
        get_file=AsyncMock(return_value=file_obj),
    )
    update = SimpleNamespace(message=_telegram_message(doc), update_id=123)

    await adapter._handle_media_message(update, None)

    event = adapter.handle_message.await_args.args[0]
    assert event.text.startswith("[Forwarded Telegram document]\n\n[Content of note.txt]:")
    assert "hello from file" in event.text


@pytest.mark.asyncio
async def test_pending_prompt_waits_for_forwarded_document_download_before_flush():
    adapter = _make_adapter()
    adapter._text_batch_delay_seconds = 0.1
    adapter._TEXT_BATCH_FAST_DELAY_S = 0.1
    adapter._TEXT_BATCH_SHORT_DELAY_S = 0.1
    adapter._media_batch_delay_seconds = 0.5
    adapter.handle_message = AsyncMock()
    source = _source(adapter)

    prompt = MessageEvent(
        text="А вот так",
        message_type=MessageType.TEXT,
        source=source,
        message_id="10",
    )
    adapter._enqueue_text_event(prompt)

    async def delayed_file():
        await asyncio.sleep(0.2)
        return SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"docx bytes")))

    doc = SimpleNamespace(
        file_name="Отчет.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size=1024,
        get_file=AsyncMock(side_effect=delayed_file),
    )
    update = SimpleNamespace(message=_telegram_message(doc), update_id=123)

    media_task = asyncio.create_task(adapter._handle_media_message(update, None))
    await asyncio.sleep(0.15)

    adapter.handle_message.assert_not_awaited()
    pending = adapter._pending_text_batches[adapter._text_batch_key(prompt)]
    assert pending.text == "А вот так\n[Forwarded Telegram document]"
    assert getattr(pending, "_awaiting_media_download") is True

    await media_task
    pending = adapter._pending_text_batches[adapter._text_batch_key(prompt)]
    assert pending.text == "А вот так\n[Forwarded Telegram document]"
    assert len(pending.media_urls) == 1
    assert pending.media_types == [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]
    assert getattr(pending, "_awaiting_media_download") is False

    for task in list(adapter._pending_text_batch_tasks.values()):
        task.cancel()
    await asyncio.gather(*adapter._pending_text_batch_tasks.values(), return_exceptions=True)


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
async def test_forwarded_file_without_pending_prompt_is_not_delayed():
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

    assert merged is False
    assert adapter._pending_text_batches == {}


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
