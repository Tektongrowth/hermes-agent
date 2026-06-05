import json
from datetime import datetime, timezone

from hermes_state import SessionDB
from tools.chat_context_tool import chat_context


def test_chat_context_requires_scope_without_current_chat(tmp_path):
    db = SessionDB(tmp_path / "state.db")

    result = json.loads(chat_context(db=db))

    assert result["success"] is False
    assert "No current messaging chat" in result["error"]


def test_chat_context_uses_current_chat_scope(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.record_platform_message(
        platform="telegram",
        chat_id="-100",
        message_id="1",
        user_name="Nick",
        text="Let’s test it",
        timestamp=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
    )

    result = json.loads(
        chat_context(
            limit=5,
            db=db,
            current_platform="telegram",
            current_chat_id="-100",
        )
    )

    assert result["success"] is True
    assert result["captured_only"] is True
    assert result["scope"]["platform"] == "telegram"
    assert result["messages"][0]["sender"] == "Nick"
    assert result["messages"][0]["text"] == "Let’s test it"


def test_chat_context_validates_partial_explicit_scope(tmp_path):
    db = SessionDB(tmp_path / "state.db")

    result = json.loads(chat_context(platform="telegram", db=db))

    assert result["success"] is False
    assert "Provide both platform and chat_id" in result["error"]
