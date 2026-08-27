from __future__ import annotations

from gateway.config import Platform
from gateway.principal_toolsets import principal_execution_tool_authorized
from gateway.session import SessionSource
from tools.registry import ToolRegistry


def _source(**overrides):
    values = {
        "platform": Platform.DISCORD,
        "chat_id": "cjs-channel",
        "chat_type": "channel",
        "user_id": "mason-user",
        "guild_id": "cjs-guild",
        "principal_role_ids": (),
    }
    values.update(overrides)
    return SessionSource(**values)


def _registry():
    registry = ToolRegistry()
    registry.register(
        name="mcp_cjs_synkedup_jobs",
        toolset="synkedup-operations-read",
        schema={"name": "mcp_cjs_synkedup_jobs", "description": "read jobs", "parameters": {}},
        handler=lambda args, task_id=None: "ok",
    )
    registry.register(
        name="mcp_cjs_synkedup_job_costing",
        toolset="synkedup-financial-read",
        schema={"name": "mcp_cjs_synkedup_job_costing", "description": "read costing", "parameters": {}},
        handler=lambda args, task_id=None: "ok",
    )
    return registry


def _config(toolsets=None):
    return {
        "discord": {"allowed_channels": "cjs-channel,cjs-other"},
        "platform_principal_toolsets": {
            "discord": {
                "guilds": ["cjs-guild"],
                "default": [],
                "users": {
                    "mason-user": (
                        ["synkedup-operations-read"] if toolsets is None else toolsets
                    )
                },
                "roles": {},
                "admin_users": [],
            }
        },
    }


def _authorized(config, source, tool):
    return principal_execution_tool_authorized(
        config,
        "discord",
        source,
        tool,
        registry=_registry(),
        fallback_toolsets=["terminal"],
    )


def test_execution_recheck_allows_only_the_tools_currently_granted():
    config = _config(["synkedup-operations-read"])
    assert _authorized(config, _source(), "mcp_cjs_synkedup_jobs") is True
    assert _authorized(config, _source(), "mcp_cjs_synkedup_job_costing") is False


def test_execution_recheck_observes_permission_revocation():
    source = _source()
    assert _authorized(_config(["synkedup-operations-read"]), source, "mcp_cjs_synkedup_jobs")
    assert not _authorized(_config([]), source, "mcp_cjs_synkedup_jobs")


def test_execution_recheck_fails_closed_for_wrong_guild_channel_dm_and_unknown_tool():
    config = _config(["synkedup-operations-read"])
    assert not _authorized(config, _source(guild_id="other-guild"), "mcp_cjs_synkedup_jobs")
    assert not _authorized(config, _source(chat_id="other-channel"), "mcp_cjs_synkedup_jobs")
    assert not _authorized(config, _source(chat_type="dm", guild_id=None), "mcp_cjs_synkedup_jobs")
    assert not _authorized(config, _source(), "missing_tool")


def test_execution_recheck_accepts_an_allowed_parent_for_a_thread():
    config = _config(["synkedup-operations-read"])
    source = _source(chat_id="thread-123", chat_type="thread", parent_chat_id="cjs-channel")
    assert _authorized(config, source, "mcp_cjs_synkedup_jobs")


def test_execution_recheck_malformed_present_policy_denies():
    config = _config(["synkedup-operations-read"])
    config["platform_principal_toolsets"]["discord"]["roles"] = []
    assert not _authorized(config, _source(), "mcp_cjs_synkedup_jobs")


def test_execution_recheck_preserves_legacy_platform_without_principal_policy():
    assert principal_execution_tool_authorized(
        {"discord": {"allowed_channels": "different"}},
        "discord",
        _source(),
        "missing_tool",
        registry=_registry(),
        fallback_toolsets=[],
    )
