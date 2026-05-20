import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli import goals


class _FakeSessionEntry:
    session_id = "sid-gateway-goal-config"


class _FakeSessionStore:
    def __init__(self):
        self.entry = _FakeSessionEntry()

    def get_or_create_session(self, source):
        return self.entry

    def _generate_session_key(self, source):
        return "agent:main:telegram:channel:goal-config"


class _RecordingAdapter:
    def __init__(self):
        self._pending_messages = {}
        self.goal_cards = []

    async def send_goal_card(self, chat_id, content, metadata=None):
        self.goal_cards.append(
            {"chat_id": chat_id, "content": content, "metadata": metadata or {}}
        )

        class _Result:
            success = True
            message_id = "goal-card-msg"

        return _Result()


@pytest.mark.asyncio
async def test_gateway_goal_uses_goals_max_turns_from_full_config(tmp_path, monkeypatch):
    """Gateway /goal should honor top-level goals.max_turns from config.yaml."""
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("goals:\n  max_turns: 7\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    goals._DB_CACHE.clear()

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="token")}
    )
    runner.session_store = _FakeSessionStore()
    adapter = _RecordingAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._queued_events = {}

    event = MessageEvent(
        text="/goal ship the benchmark",
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="chat-goal-config",
            chat_type="channel",
            user_id="user-goal-config",
        ),
        message_id="msg-goal-config",
    )

    response = await GatewayRunner._handle_goal_command(runner, event)

    try:
        assert response == ""
        assert len(adapter.goal_cards) == 1
        card = adapter.goal_cards[0]
        assert "Цель активирована" in card["content"]
        assert "ship the benchmark" in card["content"]
        assert card["metadata"]["goal_status"] == "active"
        state = goals.GoalManager("sid-gateway-goal-config").state
        assert state is not None
        assert state.max_turns == 7
        assert state.goal == "ship the benchmark"
    finally:
        goals._DB_CACHE.clear()
