from datetime import datetime, timezone

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_state import SessionDB


def test_gateway_records_incoming_platform_message(tmp_path):
    runner = object.__new__(GatewayRunner)
    runner._session_db = SessionDB(tmp_path / "state.db")
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100",
        chat_name="Design",
        chat_type="group",
        user_id="536",
        user_name="Nick",
    )
    event = MessageEvent(
        text="Ok can it read this chat?",
        message_type=MessageType.TEXT,
        source=source,
        message_id="10533",
        timestamp=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
    )

    runner._record_incoming_platform_message(event)

    rows = runner._session_db.get_recent_platform_messages(
        platform="telegram",
        chat_id="-100",
    )
    assert len(rows) == 1
    assert rows[0]["chat_name"] == "Design"
    assert rows[0]["sender"] == "Nick"
    assert rows[0]["text"] == "Ok can it read this chat?"
