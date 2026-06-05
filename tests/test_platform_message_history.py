from datetime import datetime, timezone, timedelta

from hermes_state import SessionDB


def test_record_platform_message_is_idempotent(tmp_path):
    db = SessionDB(tmp_path / "state.db")

    first_id = db.record_platform_message(
        platform="telegram",
        chat_id="-100",
        message_id="42",
        user_id="536",
        user_name="Nick",
        text="first",
        timestamp=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
    )
    second_id = db.record_platform_message(
        platform="telegram",
        chat_id="-100",
        message_id="42",
        user_id="536",
        user_name="Nick",
        text="first duplicate",
        timestamp=datetime(2026, 6, 4, 12, 1, tzinfo=timezone.utc),
    )

    assert first_id == second_id
    rows = db.get_recent_platform_messages(platform="telegram", chat_id="-100", limit=10)
    assert len(rows) == 1
    assert rows[0]["text"] == "first"


def test_get_recent_platform_messages_returns_newest_chronological(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    base = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    for idx, text in enumerate(["one", "two", "three"]):
        db.record_platform_message(
            platform="telegram",
            chat_id="-100",
            message_id=str(idx + 1),
            user_name="Nick",
            text=text,
            timestamp=base + timedelta(minutes=idx),
        )

    rows = db.get_recent_platform_messages(platform="telegram", chat_id="-100", limit=2)

    assert [row["text"] for row in rows] == ["two", "three"]
    assert [row["sender"] for row in rows] == ["Nick", "Nick"]


def test_get_recent_platform_messages_scopes_to_thread(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.record_platform_message(platform="telegram", chat_id="-100", thread_id="1", message_id="1", text="thread 1")
    db.record_platform_message(platform="telegram", chat_id="-100", thread_id="2", message_id="2", text="thread 2")

    rows = db.get_recent_platform_messages(platform="telegram", chat_id="-100", thread_id="2")

    assert [row["text"] for row in rows] == ["thread 2"]


def test_prune_platform_messages_keeps_newest_per_chat(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    base = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    for idx in range(5):
        db.record_platform_message(
            platform="telegram",
            chat_id="-100",
            message_id=str(idx),
            text=str(idx),
            timestamp=base + timedelta(minutes=idx),
        )

    result = db.prune_platform_messages(retention_days=None, max_messages_per_chat=3)
    rows = db.get_recent_platform_messages(platform="telegram", chat_id="-100", limit=10)

    assert result["deleted_by_count"] == 2
    assert [row["text"] for row in rows] == ["2", "3", "4"]
