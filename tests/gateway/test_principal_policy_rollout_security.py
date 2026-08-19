"""Release-blocking security contracts for the CJS Discord RBAC rollout."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock

from gateway import run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource
from tests.gateway._plugin_adapter_loader import load_plugin_adapter


def _discord_source(*, guild_id: str | None, user_id: str = "crew") -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id="approved-channel",
        chat_type="channel" if guild_id else "dm",
        user_id=user_id,
        guild_id=guild_id,
    )


def _policy():
    return {
        "platform_principal_toolsets": {
            "discord": {
                "guilds": ["cjs-guild"],
                "admin_users": ["nick"],
                "roles": {"crew": ["cjs_employee"]},
                "users": {"nick": ["cjs_employee"]},
            }
        }
    }


def test_principal_policy_blocks_legacy_discord_allowlists_outside_the_approved_guild(
    monkeypatch,
):
    """A policy-present Discord bot cannot fall through to DM/other-guild env grants."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.pairing_store = Mock()
    monkeypatch.setattr(gateway_run, "_load_gateway_config", _policy)
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "nick")

    assert runner._is_user_authorized(_discord_source(guild_id="cjs-guild", user_id="crew"))
    assert not runner._is_user_authorized(_discord_source(guild_id="other-guild", user_id="nick"))
    assert not runner._is_user_authorized(_discord_source(guild_id=None, user_id="nick"))


def test_session_commands_use_the_principal_scoped_session_helper():
    """Commands must not reopen legacy shared transcripts under principal policy."""
    protected_handlers = (
        gateway_run.GatewayRunner._handle_status_command,
        gateway_run.GatewayRunner._handle_retry_command,
        gateway_run.GatewayRunner._handle_undo_command,
        gateway_run.GatewayRunner._handle_compress_command,
        gateway_run.GatewayRunner._handle_title_command,
        gateway_run.GatewayRunner._handle_resume_command,
        gateway_run.GatewayRunner._handle_branch_command,
    )
    for handler in protected_handlers:
        source = inspect.getsource(handler)
        assert "self._get_or_create_session_for_source(source)" in source
        assert "self.session_store.get_or_create_session(source)" not in source


def test_exec_and_slash_confirmation_views_use_the_dedicated_approval_allowlist():
    """Opening normal conversation by role must not grant button approval to that role."""
    discord_module = load_plugin_adapter("discord")
    send_exec = inspect.getsource(discord_module.DiscordAdapter.send_exec_approval)
    send_slash = inspect.getsource(discord_module.DiscordAdapter.send_slash_confirm)
    assert "self._approval_allowed_user_ids" in send_exec
    assert "self._approval_allowed_role_ids" in send_exec
    assert "self._approval_allowed_user_ids" in send_slash
    assert "self._approval_allowed_role_ids" in send_slash


def test_thread_creation_and_update_interception_require_principal_admin():
    discord_module = load_plugin_adapter("discord")
    thread_source = inspect.getsource(discord_module.DiscordAdapter._handle_thread_create_slash)
    dispatch_source = inspect.getsource(discord_module.DiscordAdapter._dispatch_thread_session)
    update_source = inspect.getsource(gateway_run.GatewayRunner._handle_message)
    assert "_principal_control_authorized" in thread_source
    assert "guild_id=" in dispatch_source
    assert "Only a Mason Admin can answer a pending update prompt" in update_source


def test_principal_policy_disables_auto_threading_and_protects_model_cancel():
    discord_module = load_plugin_adapter("discord")
    message_source = inspect.getsource(discord_module.DiscordAdapter._handle_message)
    cancel_source = inspect.getsource(discord_module.ModelPickerView._on_cancel)
    assert "not self._principal_policy_active()" in message_source
    assert "self._check_auth(interaction)" in cancel_source


def test_principal_policy_requires_an_exact_admin_for_slash_commands(monkeypatch):
    """Crew may converse but cannot operate gateway slash commands."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = SimpleNamespace(platforms={})
    monkeypatch.setattr(gateway_run, "_load_gateway_config", _policy)

    assert runner._check_slash_access(_discord_source(guild_id="cjs-guild", user_id="crew"), "restart")
    assert runner._check_slash_access(_discord_source(guild_id="cjs-guild", user_id="nick"), "restart") is None
