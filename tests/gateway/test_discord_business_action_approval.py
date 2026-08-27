from pathlib import Path

from plugins.platforms.discord.adapter import ExecApprovalView


def test_confirm_only_exec_view_records_action_mode():
    view = ExecApprovalView(
        session_key="session-1",
        allowed_user_ids={"admin-1"},
        confirm_only=True,
    )
    assert view.confirm_only is True


def test_standard_exec_view_keeps_command_mode():
    view = ExecApprovalView(
        session_key="session-1",
        allowed_user_ids={"admin-1"},
    )
    assert view.confirm_only is False


def test_confirm_only_view_removes_persistent_grant_buttons_and_renames_once():
    source = Path("plugins/platforms/discord/adapter.py").read_text(encoding="utf-8")
    assert 'if label in {"Allow Session", "Always Allow"}' in source
    assert 'child.label = "Confirm"' in source
    assert 'child.label = "Cancel"' in source
