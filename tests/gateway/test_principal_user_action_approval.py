from types import SimpleNamespace

from gateway.principal_toolsets import (
    principal_action_preview,
    principal_execution_tool_authorized,
    principal_user_action_approval_required,
    resolve_principal_toolsets,
)


GUILD = "guild-1"
ADMIN = "admin-1"
USER = "user-1"
PATTERNS = [
    r"(?:^|_)(?:delete|remove|trash|purge|destroy|void|refund|cancel|revoke|terminate)(?:_|$)",
    r"(?:^|_)(?:send|publish|post|submit|pay|charge|transfer|share|grant|permission|invite|public_link|disconnect)(?:_|$)",
]


def _source(user_id: str):
    return SimpleNamespace(
        user_id=user_id,
        guild_id=GUILD,
        chat_type="group",
        chat_id="channel-1",
        parent_chat_id=None,
        principal_role_ids=(),
    )


def _config():
    return {
        "discord": {"allowed_channels": ["channel-1"]},
        "platform_principal_toolsets": {
            "discord": {
                "guilds": [GUILD],
                "admin_users": [ADMIN],
                "default": ["*"],
                "users": {ADMIN: ["*"]},
                "roles": {},
                "user_action_policy": {
                    "irreversible_requires_admin_approval": True,
                    "tool_name_patterns": PATTERNS,
                },
            }
        },
    }


class _Registry:
    @staticmethod
    def get_toolset_for_tool(tool_name):
        return {
            "mcp_composio_execute": "composio-approved",
            "synkedup_jobs": "synkedup-operations-read",
        }.get(tool_name)


def test_wildcard_expands_to_only_currently_approved_toolsets():
    fallback = ["synkedup-operations-read", "composio-approved"]
    assert resolve_principal_toolsets(_config(), "discord", _source(USER), fallback) == sorted(fallback)
    assert principal_execution_tool_authorized(
        _config(),
        "discord",
        _source(USER),
        "mcp_composio_execute",
        registry=_Registry(),
        fallback_toolsets=fallback,
    )


def test_regular_user_delete_requires_admin_approval():
    assert principal_user_action_approval_required(
        _config(), "discord", _source(USER), "mcp_googledrive_delete_file", {"file_id": "123"}
    )


def test_dynamic_composio_delete_slug_requires_admin_approval():
    assert principal_user_action_approval_required(
        _config(),
        "discord",
        _source(USER),
        "mcp_composio_execute",
        {"tool_slug": "GOOGLEDRIVE_DELETE_FILE", "arguments": {"file_id": "123"}},
    )


def test_regular_user_reversible_folder_create_does_not_require_approval():
    assert not principal_user_action_approval_required(
        _config(),
        "discord",
        _source(USER),
        "mcp_composio_execute",
        {"tool_slug": "GOOGLEDRIVE_CREATE_FOLDER", "arguments": {"name": "Dale Petersen"}},
    )


def test_regular_user_permission_change_requires_admin_approval():
    assert principal_user_action_approval_required(
        _config(),
        "discord",
        _source(USER),
        "mcp_composio_execute",
        {"tool_slug": "GOOGLEDRIVE_CREATE_PERMISSION", "arguments": {"file_id": "123"}},
    )


def test_admin_bypasses_user_action_approval():
    assert not principal_user_action_approval_required(
        _config(),
        "discord",
        _source(ADMIN),
        "mcp_composio_execute",
        {"tool_slug": "GOOGLEDRIVE_DELETE_FILE", "arguments": {"file_id": "123"}},
    )


def test_action_preview_redacts_sensitive_values():
    preview = principal_action_preview(
        "mcp_composio_execute",
        {
            "tool_slug": "GOOGLEDRIVE_DELETE_FILE",
            "api_key": "should-not-appear",
            "arguments": {"file_id": "123", "access_token": "also-secret"},
        },
    )
    assert "GOOGLEDRIVE_DELETE_FILE" in preview
    assert '"file_id": "123"' in preview
    assert "should-not-appear" not in preview
    assert "also-secret" not in preview
    assert preview.count("[REDACTED]") == 2


def test_malformed_action_policy_revokes_tool_authorization():
    config = _config()
    config["platform_principal_toolsets"]["discord"]["user_action_policy"][
        "tool_name_patterns"
    ] = ["("]
    assert not principal_execution_tool_authorized(
        config,
        "discord",
        _source(USER),
        "mcp_composio_execute",
        registry=_Registry(),
        fallback_toolsets=["composio-approved"],
    )
