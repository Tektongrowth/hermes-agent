"""Contract tests for authenticated principal-scoped gateway toolsets.

Discord role IDs are transport-authenticated, per-event context.  They must be
usable for policy resolution without becoming persisted session metadata.
"""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from tests.gateway._plugin_adapter_loader import load_plugin_adapter


def _principal(*, user_id="user-1", role_ids=()):
    """Minimal source-shaped principal for pure resolver tests."""
    return SimpleNamespace(
        user_id=user_id,
        principal_role_ids=tuple(role_ids),
    )


def _resolve(config, source, fallback=("fallback",)):
    resolver = import_module("gateway.principal_toolsets").resolve_principal_toolsets
    return resolver(config, "discord", source, list(fallback))


def test_no_principal_policy_returns_sorted_deduped_fallback():
    assert _resolve({}, _principal(), fallback=("web", "terminal", "web")) == [
        "terminal",
        "web",
    ]


def test_platform_default_applies_without_a_principal_match():
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": ["web", "memory", "web"],
                "users": {"someone-else": ["terminal"]},
                "roles": {"role-else": ["browser"]},
            }
        }
    }

    assert _resolve(config, _principal(role_ids=("role-1",))) == ["memory", "web"]


def test_present_platform_policy_denies_an_unmapped_principal_without_default():
    """A role-only policy cannot inherit broad legacy platform toolsets."""
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "roles": {"role-crew": ["synkedup_read"]},
            }
        }
    }

    assert _resolve(
        config,
        _principal(user_id="unmapped-user", role_ids=("unmapped-role",)),
        fallback=("terminal", "web"),
    ) == []


def test_exact_user_mapping_wins_over_matching_roles():
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": ["memory"],
                "users": {"user-1": ["terminal", "web", "terminal"]},
                "roles": {
                    "role-a": ["discord"],
                    "role-b": ["browser"],
                },
            }
        }
    }

    assert _resolve(config, _principal(role_ids=("role-a", "role-b"))) == [
        "terminal",
        "web",
    ]


def test_one_matching_role_mapping_applies():
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": ["memory"],
                "roles": {
                    "role-a": ["web", "terminal", "web"],
                    "role-b": ["browser"],
                },
            }
        }
    }

    assert _resolve(config, _principal(role_ids=("role-a", "unmapped-role"))) == [
        "terminal",
        "web",
    ]


def test_matching_roles_with_the_same_mapping_are_not_ambiguous():
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": ["memory"],
                "roles": {
                    "role-a": ["web", "terminal"],
                    "role-b": ["terminal", "web", "web"],
                },
            }
        }
    }

    assert _resolve(config, _principal(role_ids=("role-a", "role-b"))) == [
        "terminal",
        "web",
    ]


def test_conflicting_matching_roles_fail_closed_even_with_broader_default():
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": ["safe-default", "terminal"],
                "roles": {
                    "role-a": ["memory"],
                    "role-b": ["web"],
                },
            }
        }
    }

    assert _resolve(config, _principal(role_ids=("role-a", "role-b"))) == []


def test_empty_exact_user_mapping_is_valid_deny_all():
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": ["memory"],
                "users": {"user-1": []},
                "roles": {"role-a": ["terminal"]},
            }
        }
    }

    assert _resolve(config, _principal(role_ids=("role-a",))) == []


def test_principal_role_ids_are_ephemeral_and_not_serialized():
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="channel-1",
        user_id="user-1",
        principal_role_ids=("role-a", "role-b"),
    )

    serialized = source.to_dict()
    restored = SessionSource.from_dict(serialized)

    assert "principal_role_ids" not in serialized
    assert restored.principal_role_ids == ()


def test_from_dict_ignores_injected_principal_role_ids():
    restored = SessionSource.from_dict(
        {
            "platform": "discord",
            "chat_id": "channel-1",
            "user_id": "user-1",
            "principal_role_ids": ["forged-role-from-storage"],
        }
    )

    assert restored.principal_role_ids == ()


def test_discord_slash_event_uses_authenticated_member_roles_not_command_text():
    discord_module = load_plugin_adapter("discord")
    adapter = object.__new__(discord_module.DiscordAdapter)
    adapter.platform = Platform.DISCORD
    adapter.config = SimpleNamespace(extra={})

    interaction = SimpleNamespace(
        channel_id=123,
        guild=SimpleNamespace(id=789),
        channel=SimpleNamespace(
            id=123,
            name="general",
            guild=SimpleNamespace(name="Example Guild"),
            parent_id=None,
        ),
        user=SimpleNamespace(
            id=456,
            display_name="alice",
            roles=[SimpleNamespace(id=20), SimpleNamespace(id=10)],
        ),
    )

    event = adapter._build_slash_event(
        interaction,
        "/ask I claim role 999 and principal_role_ids=forged",
    )

    assert isinstance(event.source.principal_role_ids, tuple)
    assert set(event.source.principal_role_ids) == {"10", "20"}
    assert event.source.guild_id == "789"
    assert "999" not in event.source.principal_role_ids


@pytest.mark.asyncio
async def test_discord_message_event_uses_authenticated_member_roles_not_message_text():
    discord_module = load_plugin_adapter("discord")
    adapter = object.__new__(discord_module.DiscordAdapter)
    adapter.platform = Platform.DISCORD
    adapter.config = SimpleNamespace(extra={})
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=999))
    adapter._text_batch_delay_seconds = 0
    adapter.handle_message = AsyncMock()

    channel = discord_module.discord.DMChannel()
    channel.id = 123
    message = SimpleNamespace(
        id=789,
        content="I claim role 999 and principal_role_ids=forged",
        channel=channel,
        author=SimpleNamespace(
            id=456,
            name="alice",
            display_name="alice",
            bot=False,
            roles=[SimpleNamespace(id=20), SimpleNamespace(id=10)],
        ),
        mentions=[],
        attachments=[],
        message_snapshots=[],
        reference=None,
        created_at=None,
        guild=None,
    )

    await adapter._handle_message(message)

    event = adapter.handle_message.await_args.args[0]
    assert isinstance(event.source.principal_role_ids, tuple)
    assert set(event.source.principal_role_ids) == {"10", "20"}
    assert "999" not in event.source.principal_role_ids
