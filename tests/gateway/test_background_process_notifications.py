"""Tests for configurable background process notification modes.

The gateway process watcher pushes status updates to users' chats when
background terminal commands run.  ``display.background_process_notifications``
controls verbosity: off | result | error | all (default).

Contributed by @PeterFile (PR #593), reimplemented on current main.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner, _parse_session_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeRegistry:
    """Return pre-canned sessions, then None once exhausted."""

    def __init__(self, sessions):
        self._sessions = list(sessions)

    def get(self, session_id):
        if self._sessions:
            return self._sessions.pop(0)
        return None

    def is_completion_consumed(self, session_id):
        return False


def _build_runner(monkeypatch, tmp_path, mode: str) -> GatewayRunner:
    """Create a GatewayRunner with a fake config for the given mode."""
    (tmp_path / "config.yaml").write_text(
        f"display:\n  background_process_notifications: {mode}\n",
        encoding="utf-8",
    )

    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock(), handle_message=AsyncMock())
    runner.adapters[Platform.TELEGRAM] = adapter
    return runner


def _watcher_dict(session_id="proc_test", thread_id=""):
    d = {
        "session_id": session_id,
        "check_interval": 0,
        "platform": "telegram",
        "chat_id": "123",
    }
    if thread_id:
        d["thread_id"] = thread_id
    return d


# ---------------------------------------------------------------------------
# _load_background_notifications_mode unit tests
# ---------------------------------------------------------------------------

class TestLoadBackgroundNotificationsMode:

    def test_defaults_to_all(self, monkeypatch, tmp_path):
        import gateway.run as gw
        monkeypatch.setattr(gw, "_hermes_home", tmp_path)
        monkeypatch.delenv("HERMES_BACKGROUND_NOTIFICATIONS", raising=False)
        assert GatewayRunner._load_background_notifications_mode() == "all"

    def test_reads_config_yaml(self, monkeypatch, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "display:\n  background_process_notifications: error\n"
        )
        import gateway.run as gw
        monkeypatch.setattr(gw, "_hermes_home", tmp_path)
        monkeypatch.delenv("HERMES_BACKGROUND_NOTIFICATIONS", raising=False)
        assert GatewayRunner._load_background_notifications_mode() == "error"

    def test_env_var_overrides_config(self, monkeypatch, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "display:\n  background_process_notifications: error\n"
        )
        import gateway.run as gw
        monkeypatch.setattr(gw, "_hermes_home", tmp_path)
        monkeypatch.setenv("HERMES_BACKGROUND_NOTIFICATIONS", "off")
        assert GatewayRunner._load_background_notifications_mode() == "off"

    def test_false_value_maps_to_off(self, monkeypatch, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "display:\n  background_process_notifications: false\n"
        )
        import gateway.run as gw
        monkeypatch.setattr(gw, "_hermes_home", tmp_path)
        monkeypatch.delenv("HERMES_BACKGROUND_NOTIFICATIONS", raising=False)
        assert GatewayRunner._load_background_notifications_mode() == "off"

    def test_invalid_value_defaults_to_all(self, monkeypatch, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "display:\n  background_process_notifications: banana\n"
        )
        import gateway.run as gw
        monkeypatch.setattr(gw, "_hermes_home", tmp_path)
        monkeypatch.delenv("HERMES_BACKGROUND_NOTIFICATIONS", raising=False)
        assert GatewayRunner._load_background_notifications_mode() == "all"


# ---------------------------------------------------------------------------
# _run_process_watcher integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "sessions", "expected_calls", "expected_fragment"),
    [
        # all mode: running output → sends update
        (
            "all",
            [
                SimpleNamespace(output_buffer="building...\n", exited=False, exit_code=None),
                None,  # process disappears → watcher exits
            ],
            1,
            "Background task is still running",
        ),
        # result mode: running output → no update
        (
            "result",
            [
                SimpleNamespace(output_buffer="building...\n", exited=False, exit_code=None),
                None,
            ],
            0,
            None,
        ),
        # off mode: exited process → no notification
        (
            "off",
            [SimpleNamespace(output_buffer="done\n", exited=True, exit_code=0)],
            0,
            None,
        ),
        # result mode: exited → notifies
        (
            "result",
            [SimpleNamespace(output_buffer="done\n", exited=True, exit_code=0)],
            1,
            "Background task completed",
        ),
        # error mode: exit 0 → no notification
        (
            "error",
            [SimpleNamespace(output_buffer="done\n", exited=True, exit_code=0)],
            0,
            None,
        ),
        # error mode: exit 1 → notifies
        (
            "error",
            [SimpleNamespace(output_buffer="traceback\n", exited=True, exit_code=1)],
            1,
            "Background task failed (exit code 1)",
        ),
        # all mode: exited → notifies
        (
            "all",
            [SimpleNamespace(output_buffer="ok\n", exited=True, exit_code=0)],
            1,
            "Background task completed",
        ),
    ],
)
async def test_run_process_watcher_respects_notification_mode(
    monkeypatch, tmp_path, mode, sessions, expected_calls, expected_fragment
):
    import tools.process_registry as pr_module

    monkeypatch.setattr(pr_module, "process_registry", _FakeRegistry(sessions))

    # Patch asyncio.sleep to avoid real delays
    async def _instant_sleep(*_a, **_kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    runner = _build_runner(monkeypatch, tmp_path, mode)
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_process_watcher(_watcher_dict())

    assert adapter.send.await_count == expected_calls, (
        f"mode={mode}: expected {expected_calls} sends, got {adapter.send.await_count}"
    )
    if expected_fragment is not None:
        sent_message = adapter.send.await_args.args[1]
        assert expected_fragment in sent_message
        assert "proc_test" not in sent_message
        assert "[Background process" not in sent_message


def test_format_background_process_notification_is_user_facing():
    message = GatewayRunner._format_background_process_notification(
        exit_code=0,
        output="\x1b[32mFiles changed:\x1b[0m\ngateway/run.py\n",
    )

    assert message.startswith("✅ Background task completed.")
    assert "Files changed:" in message
    assert "\x1b" not in message
    assert "proc_" not in message
    assert "[Background process" not in message


def test_format_background_process_notification_summarizes_traceback_qa_failure():
    output = """group2-20260521
Traceback (most recent call last):
  File "/root/hermes-workspace/rudn-student-repositories/grading-runs-20260521/grade_all_repositories.py", line 245, in <module>
    main()
  File "/root/hermes-workspace/rudn-student-repositories/grading-runs-20260521/grade_all_repositories.py", line 238, in main
    raise RuntimeError(f'QA failed for {item} {group_name}: {json.dumps(qa,ensure_ascii=False)}')
RuntimeError: QA failed for Unit 6 Group 2: {"item":"Unit 6","group":"Group 2","run_id":"rudn-unit6-group2-20260521","expected_present":17,"graded":16,"coverage":{"total_bundles":16,"processed_bundles":16,"missing_bundles":[],"artifact_count":16,"all_artifacts_covered":true},"all_scores_integer":true,"confidence_0_100":true,"missing_students":["Ахсан Шеикх Мд Джуборадж","Полякова Елизавета Дмитриевна","Акопян Георгий Даниилович","Абшилава Константин Константинович","Романов Дэни Вадимович"],"passed":false}
"""

    message = GatewayRunner._format_background_process_notification(
        exit_code=1,
        output=output,
    )

    assert message.startswith("⚠️ Background task failed (exit code 1).")
    assert "Summary:" in message
    assert "Failure summary:" in message
    assert "exception: RuntimeError" in message
    assert "reason: QA failed for Unit 6 Group 2" in message
    assert "scope: Unit 6 Group 2" in message
    assert "graded: 16 / expected 17" in message
    assert "missing students: 5" in message
    assert "Ахсан Шеикх Мд Джуборадж" in message
    assert "Traceback" not in message
    assert "File \"/root/hermes-workspace" not in message
    assert "json.dumps" not in message
    assert "coverage" not in message


def test_format_background_process_notification_summarizes_jsonl_artifacts():
    output = "\n".join([
        '{"ready":"Wei Zihang-PW11.docx","unit":11,"path":"/secret/path/Wei Zihang-PW11.docx"}',
        '{"discovered":"rudn-additional-g1-unit-11-20260520","bundle_count":1,"artifact_count":1,"queued":["abc"],"warnings":[]}',
        '{"ready":"Vaganov Alexander case 12.docx","unit":12,"path":"/secret/path/Vaganov Alexander case 12.docx"}',
        '{"ready":"Mezhenina case 12.docx.pdf","unit":12,"path":"/secret/path/Mezhenina case 12.docx.pdf"}',
        '{"discovered":"rudn-additional-g1-unit-12-20260520","bundle_count":2,"artifact_count":2,"queued":["def","ghi"],"warnings":[]}',
    ])

    message = GatewayRunner._format_background_process_notification(
        exit_code=0,
        output=output,
    )

    assert "Summary:" in message
    assert "Structured result:" in message
    assert "ready files: 3" in message
    assert "Wei Zihang-PW11.docx — unit 11" in message
    assert "Vaganov Alexander case 12.docx — unit 12" in message
    assert "Mezhenina case 12.docx.pdf — unit 12" in message
    assert "discovered batches: 2" in message
    assert "queued artifacts: 3" in message
    assert "/secret/path" not in message
    assert '{"ready"' not in message


@pytest.mark.asyncio
async def test_thread_id_passed_to_send(monkeypatch, tmp_path):
    """thread_id from watcher dict is forwarded as metadata to adapter.send()."""
    import tools.process_registry as pr_module

    sessions = [SimpleNamespace(output_buffer="done\n", exited=True, exit_code=0)]
    monkeypatch.setattr(pr_module, "process_registry", _FakeRegistry(sessions))

    async def _instant_sleep(*_a, **_kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_process_watcher(_watcher_dict(thread_id="42"))

    assert adapter.send.await_count == 1
    _, kwargs = adapter.send.call_args
    assert kwargs["metadata"] == {"thread_id": "42"}


@pytest.mark.asyncio
async def test_no_thread_id_sends_no_metadata(monkeypatch, tmp_path):
    """When thread_id is empty, metadata should be None (general topic)."""
    import tools.process_registry as pr_module

    sessions = [SimpleNamespace(output_buffer="done\n", exited=True, exit_code=0)]
    monkeypatch.setattr(pr_module, "process_registry", _FakeRegistry(sessions))

    async def _instant_sleep(*_a, **_kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_process_watcher(_watcher_dict())

    assert adapter.send.await_count == 1
    _, kwargs = adapter.send.call_args
    assert kwargs["metadata"] is None


@pytest.mark.asyncio
async def test_inject_watch_notification_routes_from_session_store_origin(monkeypatch, tmp_path):
    from gateway.session import SessionSource

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]
    runner.session_store._entries["agent:main:telegram:group:-100:42"] = SimpleNamespace(
        origin=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-100",
            chat_type="group",
            thread_id="42",
            user_id="123",
            user_name="Emiliyan",
        )
    )

    evt = {
        "session_id": "proc_watch",
        "session_key": "agent:main:telegram:group:-100:42",
    }

    await runner._inject_watch_notification("[SYSTEM: Background process matched]", evt)

    adapter.handle_message.assert_awaited_once()
    synth_event = adapter.handle_message.await_args.args[0]
    assert synth_event.internal is True
    assert synth_event.source.platform == Platform.TELEGRAM
    assert synth_event.source.chat_id == "-100"
    assert synth_event.source.chat_type == "group"
    assert synth_event.source.thread_id == "42"
    assert synth_event.source.user_id == "123"
    assert synth_event.source.user_name == "Emiliyan"


@pytest.mark.asyncio
async def test_agent_notification_carries_message_id_reply_anchor(monkeypatch, tmp_path):
    """notify_on_complete injection carries the triggering message_id so the
    synthetic event can be reply-anchored back into a Telegram DM topic.

    Without an anchor, Telegram private-chat topic sends fall back to the main
    chat (see _thread_kwargs_for_send / telegram_dm_topic_reply_fallback)."""
    import tools.process_registry as pr_module

    sessions = [SimpleNamespace(
        output_buffer="SMOKE_OK\n", exited=True, exit_code=0, command="sleep 1",
    )]
    monkeypatch.setattr(pr_module, "process_registry", _FakeRegistry(sessions))

    async def _instant_sleep(*_a, **_kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]

    watcher = {
        "session_id": "proc_anchor",
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123:24296",
        "platform": "telegram",
        "chat_id": "123",
        "thread_id": "24296",
        "message_id": "555",
        "notify_on_complete": True,
    }
    await runner._run_process_watcher(watcher)

    adapter.handle_message.assert_awaited_once()
    synth_event = adapter.handle_message.await_args.args[0]
    assert synth_event.internal is True
    assert synth_event.message_id == "555"
    assert synth_event.source.thread_id == "24296"


@pytest.mark.asyncio
async def test_agent_notification_no_message_id_is_tolerated(monkeypatch, tmp_path):
    """A watcher dict without message_id (CLI spawn, pre-upgrade checkpoint)
    still injects — message_id is simply None."""
    import tools.process_registry as pr_module

    sessions = [SimpleNamespace(
        output_buffer="done\n", exited=True, exit_code=0, command="sleep 1",
    )]
    monkeypatch.setattr(pr_module, "process_registry", _FakeRegistry(sessions))

    async def _instant_sleep(*_a, **_kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]

    watcher = {
        "session_id": "proc_anchorless",
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123:24296",
        "platform": "telegram",
        "chat_id": "123",
        "thread_id": "24296",
        "notify_on_complete": True,
    }
    await runner._run_process_watcher(watcher)

    adapter.handle_message.assert_awaited_once()
    synth_event = adapter.handle_message.await_args.args[0]
    assert synth_event.message_id is None


@pytest.mark.asyncio
async def test_inject_watch_notification_carries_message_id_reply_anchor(monkeypatch, tmp_path):
    from gateway.session import SessionSource

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]
    runner.session_store._entries["agent:main:telegram:dm:123:24296"] = SimpleNamespace(
        origin=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="123",
            chat_type="dm",
            thread_id="24296",
            user_id="1",
            user_name="Fabio",
        )
    )

    evt = {
        "session_id": "proc_watch",
        "session_key": "agent:main:telegram:dm:123:24296",
        "message_id": "777",
    }

    await runner._inject_watch_notification("[SYSTEM: Background process matched]", evt)

    adapter.handle_message.assert_awaited_once()
    synth_event = adapter.handle_message.await_args.args[0]
    assert synth_event.message_id == "777"
    assert synth_event.source.thread_id == "24296"


def test_build_process_event_source_falls_back_to_session_key_chat_type(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path, "all")

    evt = {
        "session_id": "proc_watch",
        "session_key": "agent:main:telegram:group:-100:42",
        "platform": "telegram",
        "chat_id": "-100",
        "thread_id": "42",
        "user_id": "123",
        "user_name": "Emiliyan",
    }

    source = runner._build_process_event_source(evt)

    assert source is not None
    assert source.platform == Platform.TELEGRAM
    assert source.chat_id == "-100"
    assert source.chat_type == "group"
    assert source.thread_id == "42"
    assert source.user_id == "123"
    assert source.user_name == "Emiliyan"


def test_build_process_event_source_uses_cached_live_source_before_session_key_parse(
    monkeypatch, tmp_path
):
    from gateway.session import SessionSource

    runner = _build_runner(monkeypatch, tmp_path, "all")
    runner._cache_session_source(
        "agent:main:telegram:group:-100:42",
        SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-100",
            chat_type="group",
            thread_id="42",
            user_id="proc_owner",
            user_name="alice",
        ),
    )

    source = runner._build_process_event_source(
        {
            "session_id": "proc_watch",
            "session_key": "agent:main:telegram:group:-100:42",
        }
    )

    assert source is not None
    assert source.platform == Platform.TELEGRAM
    assert source.chat_id == "-100"
    assert source.chat_type == "group"
    assert source.thread_id == "42"
    assert source.user_id == "proc_owner"
    assert source.user_name == "alice"


@pytest.mark.asyncio
async def test_inject_watch_notification_ignores_foreground_event_source(monkeypatch, tmp_path):
    """Negative test: watch notification must NOT route to the foreground thread."""
    from gateway.session import SessionSource

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]

    # Session store has the process's original thread (thread 42)
    runner.session_store._entries["agent:main:telegram:group:-100:42"] = SimpleNamespace(
        origin=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-100",
            chat_type="group",
            thread_id="42",
            user_id="proc_owner",
            user_name="alice",
        )
    )

    # The evt dict carries the correct session_key — NOT a foreground event
    evt = {
        "session_id": "proc_cross_thread",
        "session_key": "agent:main:telegram:group:-100:42",
    }

    await runner._inject_watch_notification("[SYSTEM: watch match]", evt)

    adapter.handle_message.assert_awaited_once()
    synth_event = adapter.handle_message.await_args.args[0]
    # Must route to thread 42 (process origin), NOT some other thread
    assert synth_event.source.thread_id == "42"
    assert synth_event.source.user_id == "proc_owner"


def test_build_process_event_source_returns_none_for_empty_evt(monkeypatch, tmp_path):
    """Missing session_key and no platform metadata → None (drop notification)."""
    runner = _build_runner(monkeypatch, tmp_path, "all")

    source = runner._build_process_event_source({"session_id": "proc_orphan"})
    assert source is None


def test_build_process_event_source_returns_none_for_invalid_platform(monkeypatch, tmp_path):
    """Invalid platform string → None."""
    runner = _build_runner(monkeypatch, tmp_path, "all")

    evt = {
        "session_id": "proc_bad",
        "platform": "not_a_real_platform",
        "chat_type": "dm",
        "chat_id": "123",
    }
    source = runner._build_process_event_source(evt)
    assert source is None


def test_build_process_event_source_returns_none_for_short_session_key(monkeypatch, tmp_path):
    """Session key with <5 parts doesn't parse, falls through to empty metadata → None."""
    runner = _build_runner(monkeypatch, tmp_path, "all")

    evt = {
        "session_id": "proc_short",
        "session_key": "agent:main:telegram",  # Too few parts
    }
    source = runner._build_process_event_source(evt)
    assert source is None


# ---------------------------------------------------------------------------
# _parse_session_key helper
# ---------------------------------------------------------------------------

def test_parse_session_key_valid():
    result = _parse_session_key("agent:main:telegram:group:-100")
    assert result == {"platform": "telegram", "chat_type": "group", "chat_id": "-100"}


def test_parse_session_key_with_extra_parts():
    """6th part in a group key may be a user_id, not a thread_id — omit it."""
    result = _parse_session_key("agent:main:discord:group:chan123:thread456")
    assert result == {"platform": "discord", "chat_type": "group", "chat_id": "chan123"}


def test_parse_session_key_with_user_id_part():
    """Group keys with per-user isolation have user_id as 6th part — don't return as thread_id."""
    result = _parse_session_key("agent:main:telegram:group:chat1:user99")
    assert result == {"platform": "telegram", "chat_type": "group", "chat_id": "chat1"}


def test_parse_session_key_dm_with_thread():
    """DM keys use parts[5] as thread_id unambiguously."""
    result = _parse_session_key("agent:main:telegram:dm:chat1:topic42")
    assert result == {"platform": "telegram", "chat_type": "dm", "chat_id": "chat1", "thread_id": "topic42"}


def test_parse_session_key_thread_chat_type():
    """Thread-typed keys use parts[5] as thread_id unambiguously."""
    result = _parse_session_key("agent:main:discord:thread:chan1:thread99")
    assert result == {"platform": "discord", "chat_type": "thread", "chat_id": "chan1", "thread_id": "thread99"}


def test_parse_session_key_too_short():
    assert _parse_session_key("agent:main:telegram") is None
    assert _parse_session_key("") is None


def test_parse_session_key_wrong_prefix():
    assert _parse_session_key("cron:main:telegram:dm:123") is None
    assert _parse_session_key("agent:cron:telegram:dm:123") is None
