"""GatewayRunner integration contracts for principal-scoped toolsets.

These tests intentionally exercise the gateway seam rather than duplicating the
pure resolver's behavior.  Platform-wide toolsets remain the fallback, while an
authenticated event principal may narrow or replace them before agent creation
and cache-signature computation.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import Mock, call

import pytest

from gateway import run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource
from hermes_cli import tools_config


def _discord_source(*, user_id: str = "user-1", role_ids=()) -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id="channel-1",
        user_id=user_id,
        principal_role_ids=tuple(role_ids),
    )


def _patch_platform_fallback(monkeypatch, toolsets):
    get_platform_tools = Mock(return_value=set(toolsets))
    monkeypatch.setattr(tools_config, "_get_platform_tools", get_platform_tools)
    return get_platform_tools


def _signature(toolsets: list[str]) -> str:
    return gateway_run.GatewayRunner._agent_config_signature(
        model="test-model",
        runtime={
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "provider": "test-provider",
            "api_mode": "chat_completions",
        },
        enabled_toolsets=toolsets,
        ephemeral_prompt="",
    )


def test_exact_discord_user_policy_replaces_platform_fallback(monkeypatch):
    get_platform_tools = _patch_platform_fallback(
        monkeypatch, {"memory", "terminal", "web"}
    )
    source = _discord_source(user_id="discord-user-42")
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "users": {"discord-user-42": ["web", "web"]},
            }
        }
    }

    platform_wide = gateway_run._resolve_enabled_toolsets({}, source)
    principal_scoped = gateway_run._resolve_enabled_toolsets(config, source)

    assert platform_wide == ["memory", "terminal", "web"]
    assert principal_scoped == ["web"]
    get_platform_tools.assert_has_calls(
        [call({}, "discord"), call(config, "discord")]
    )


def test_authenticated_role_change_changes_toolsets_and_agent_cache_signature(monkeypatch):
    _patch_platform_fallback(monkeypatch, {"memory"})
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "roles": {
                    "role-reader": ["web"],
                    "role-admin": ["terminal", "web"],
                }
            }
        }
    }
    reader = _discord_source(role_ids=("role-reader",))
    admin = _discord_source(role_ids=("role-admin",))

    reader_toolsets = gateway_run._resolve_enabled_toolsets(config, reader)
    admin_toolsets = gateway_run._resolve_enabled_toolsets(config, admin)

    assert reader_toolsets == ["web"]
    assert admin_toolsets == ["terminal", "web"]
    assert _signature(reader_toolsets) != _signature(admin_toolsets)


def test_no_principal_policy_preserves_platform_wide_toolsets(monkeypatch):
    get_platform_tools = _patch_platform_fallback(
        monkeypatch, {"web", "memory", "terminal"}
    )
    config = {"platform_toolsets": {"discord": ["terminal", "web", "memory"]}}
    source = _discord_source(role_ids=("unmapped-authenticated-role",))

    assert gateway_run._resolve_enabled_toolsets(config, source) == [
        "memory",
        "terminal",
        "web",
    ]
    get_platform_tools.assert_called_once_with(config, "discord")


def test_empty_exact_user_mapping_disables_all_toolsets(monkeypatch):
    _patch_platform_fallback(monkeypatch, {"memory", "terminal", "web"})
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "users": {"discord-user-42": []},
                "roles": {"role-admin": ["terminal", "web"]},
            }
        }
    }
    source = _discord_source(
        user_id="discord-user-42",
        role_ids=("role-admin",),
    )

    assert gateway_run._resolve_enabled_toolsets(config, source) == []


def _call_names(function) -> set[str]:
    """Return direct/attribute call names from a function, independent of lines."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


@pytest.mark.parametrize(
    "agent_path",
    [
        gateway_run.GatewayRunner._run_agent,
        gateway_run.GatewayRunner._run_background_task,
    ],
    ids=["foreground", "background"],
)
def test_agent_paths_resolve_toolsets_through_principal_helper(agent_path):
    call_names = _call_names(agent_path)

    assert "_resolve_enabled_toolsets" in call_names
    assert "_get_platform_tools" not in call_names
