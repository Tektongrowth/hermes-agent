"""Resolve gateway toolsets for an authenticated platform principal."""

from __future__ import annotations

import json
import re
from typing import Any, Optional


_SENSITIVE_ARGUMENT_TERMS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_ACTION_SELECTOR_KEYS = frozenset(
    {
        "action",
        "endpoint",
        "http_method",
        "method",
        "operation",
        "tool",
        "tool_name",
        "tool_slug",
    }
)


def _normalize_toolsets(value: Any) -> Optional[list[str]]:
    """Return a stable toolset set, or ``None`` when a policy value is invalid."""
    if not isinstance(value, list):
        return None
    if any(not isinstance(item, str) for item in value):
        return None
    return sorted(set(value))


def _normalize_action_policy(value: Any) -> Optional[dict[str, Any]]:
    """Validate the optional per-platform action approval policy."""
    if not isinstance(value, dict):
        return None
    allowed_keys = {"irreversible_requires_admin_approval", "tool_name_patterns"}
    if any(not isinstance(key, str) or key not in allowed_keys for key in value):
        return None
    enabled = value.get("irreversible_requires_admin_approval", False)
    if not isinstance(enabled, bool):
        return None
    patterns = value.get("tool_name_patterns", [])
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) for pattern in patterns):
        return None
    try:
        for pattern in patterns:
            re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None
    return {
        "irreversible_requires_admin_approval": enabled,
        "tool_name_patterns": list(patterns),
    }


def _valid_platform_policy(policy: Any) -> bool:
    """Validate a complete platform policy before granting any capability.

    A principal policy is an authorization boundary, not a best-effort config.
    Validate every entry, including mappings that do not match the current
    sender, so a typo cannot silently preserve a privileged default grant.
    """
    if not isinstance(policy, dict):
        return False
    allowed_keys = {
        "default",
        "users",
        "roles",
        "guilds",
        "admin_users",
        "user_action_policy",
    }
    if any(not isinstance(key, str) or key not in allowed_keys for key in policy):
        return False
    if "default" in policy and _normalize_toolsets(policy["default"]) is None:
        return False
    if "guilds" in policy:
        guilds = policy["guilds"]
        if not isinstance(guilds, list) or any(not isinstance(guild, str) for guild in guilds):
            return False
    if "admin_users" in policy:
        admins = policy["admin_users"]
        if not isinstance(admins, list) or any(not isinstance(user, str) for user in admins):
            return False
    if "user_action_policy" in policy and _normalize_action_policy(
        policy["user_action_policy"]
    ) is None:
        return False
    for mapping_name in ("users", "roles"):
        if mapping_name not in policy:
            continue
        mapping = policy[mapping_name]
        if not isinstance(mapping, dict):
            return False
        if any(
            not isinstance(key, str) or _normalize_toolsets(value) is None
            for key, value in mapping.items()
        ):
            return False
    return True


def principal_policy_present(user_config: Any, platform_key: str) -> bool:
    """Return whether this process must enforce principal policy locally.

    An explicitly configured but malformed top-level policy container is still
    policy-present: treating it as absent would let proxy mode bypass the
    fail-closed parser. A valid container with no current-platform entry remains
    absent and preserves legacy fallback behavior.
    """
    if not isinstance(user_config, dict):
        return False
    if "platform_principal_toolsets" not in user_config:
        return False

    policies = user_config["platform_principal_toolsets"]
    if not isinstance(policies, dict):
        return True

    platform_name = str(getattr(platform_key, "value", platform_key))
    return platform_name in policies



def principal_guild_authorized(user_config: Any, platform_key: str, source: Any) -> bool:
    """Return whether a present platform policy authorizes this guild for chat.

    Guild chat access is separate from tool grants. An explicit list is required
    so an open-conversation policy cannot accidentally admit another guild or a
    Discord DM. The platform adapter still enforces channel and role gates.
    """
    if not isinstance(user_config, dict):
        return False
    policies = user_config.get("platform_principal_toolsets")
    if not isinstance(policies, dict):
        return False
    platform_name = str(getattr(platform_key, "value", platform_key))
    policy = policies.get(platform_name)
    if not _valid_platform_policy(policy):
        return False
    guilds = policy.get("guilds")
    if not isinstance(guilds, list) or any(not isinstance(guild, str) for guild in guilds):
        return False
    guild_id = getattr(source, "guild_id", None)
    chat_type = str(getattr(source, "chat_type", "") or "").lower()
    return bool(guild_id) and chat_type not in {"dm", "direct", "private"} and str(guild_id) in guilds


def principal_admin_authorized(user_config: Any, platform_key: str, source: Any) -> bool:
    """Return whether an exact authenticated principal may run gateway controls."""
    if not principal_guild_authorized(user_config, platform_key, source):
        return False
    policies = user_config.get("platform_principal_toolsets") if isinstance(user_config, dict) else None
    platform_name = str(getattr(platform_key, "value", platform_key))
    policy = policies.get(platform_name) if isinstance(policies, dict) else None
    if not _valid_platform_policy(policy):
        return False
    admins = policy.get("admin_users", [])
    return isinstance(admins, list) and str(getattr(source, "user_id", "")) in admins


def principal_admin_user_ids(user_config: Any, platform_key: str) -> Optional[set[str]]:
    """Return exact component approvers for a present policy, or None if absent."""
    if not principal_policy_present(user_config, platform_key):
        return None
    if not isinstance(user_config, dict):
        return set()
    policies = user_config.get("platform_principal_toolsets")
    platform_name = str(getattr(platform_key, "value", platform_key))
    policy = policies.get(platform_name) if isinstance(policies, dict) else None
    if not _valid_platform_policy(policy):
        return set()
    admins = policy.get("admin_users", [])
    return set(admins) if isinstance(admins, list) else set()


def resolve_principal_toolsets(
    user_config: Any,
    platform_key: str,
    source: Any,
    fallback_toolsets: list[str],
) -> list[str]:
    """Resolve toolsets for ``source`` using a platform principal policy.

    Exact user mappings take precedence over role mappings. Multiple matching
    role mappings are accepted only when they normalize to the same toolset set;
    conflicting mappings fall back to the platform default.
    """
    fallback = _normalize_toolsets(fallback_toolsets)
    if fallback is None:
        fallback = []

    if not isinstance(user_config, dict):
        return fallback

    if "platform_principal_toolsets" not in user_config:
        return fallback

    policies = user_config["platform_principal_toolsets"]
    if not isinstance(policies, dict):
        return []

    platform_name = str(getattr(platform_key, "value", platform_key))
    if platform_name not in policies:
        return fallback

    policy = policies[platform_name]
    if not _valid_platform_policy(policy):
        return []

    # Once a platform policy is explicitly present, legacy platform-wide
    # toolsets must not become an implicit grant for an unmapped user, an
    # unreadable role snapshot, or a role revocation. Operators can state a
    # safe default explicitly; otherwise the policy is deny-by-default.
    default: list[str] = []
    if "default" in policy:
        configured_default = _normalize_toolsets(policy["default"])
        if configured_default is None:
            return []
        default = fallback if "*" in configured_default else configured_default

    user_id = getattr(source, "user_id", None)
    users = policy.get("users", {})
    if not isinstance(users, dict) or any(not isinstance(key, str) for key in users):
        return []
    roles = policy.get("roles", {})
    if not isinstance(roles, dict) or any(not isinstance(key, str) for key in roles):
        return []

    if user_id is not None and user_id in users:
        user_toolsets = _normalize_toolsets(users[user_id])
        if user_toolsets is None:
            return []
        return fallback if "*" in user_toolsets else user_toolsets

    source_roles = getattr(source, "principal_role_ids", ()) or ()
    if isinstance(source_roles, (str, bytes)):
        return default

    try:
        role_ids = {str(role_id) for role_id in source_roles if role_id is not None}
    except TypeError:
        return default

    matched_toolsets: set[tuple[str, ...]] = set()
    for role_id in role_ids:
        if role_id not in roles:
            continue
        role_toolsets = _normalize_toolsets(roles[role_id])
        if role_toolsets is None:
            return []
        if "*" in role_toolsets:
            role_toolsets = fallback
        matched_toolsets.add(tuple(role_toolsets))

    if len(matched_toolsets) == 1:
        return list(next(iter(matched_toolsets)))
    if len(matched_toolsets) > 1:
        # Ambiguous multi-role grants must never widen to a broader platform
        # default. Operators should map overlapping roles to the same explicit
        # toolset set or use an exact-user override.
        return []
    return default


def _action_policy_for_platform(user_config: Any, platform_key: str) -> Optional[dict[str, Any]]:
    """Return a validated action policy, or None when absent or invalid."""
    if not isinstance(user_config, dict):
        return None
    policies = user_config.get("platform_principal_toolsets")
    platform_name = str(getattr(platform_key, "value", platform_key))
    policy = policies.get(platform_name) if isinstance(policies, dict) else None
    if not isinstance(policy, dict) or not _valid_platform_policy(policy):
        return None
    if "user_action_policy" not in policy:
        return None
    return _normalize_action_policy(policy["user_action_policy"])


def _action_candidates(tool_name: str, tool_args: Any) -> list[str]:
    """Extract operation identifiers without scanning arbitrary user content."""
    candidates = [str(tool_name or "")]

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).strip().lower()
                if normalized_key in _ACTION_SELECTOR_KEYS and isinstance(
                    child, (str, int, float)
                ):
                    candidates.append(str(child))
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(tool_args)
    return candidates


def principal_user_action_approval_required(
    user_config: Any,
    platform_key: str,
    source: Any,
    tool_name: str,
    tool_args: Any,
) -> bool:
    """Return whether a non-admin tool action needs one-time admin approval."""
    if principal_admin_authorized(user_config, platform_key, source):
        return False
    action_policy = _action_policy_for_platform(user_config, platform_key)
    if not action_policy or not action_policy["irreversible_requires_admin_approval"]:
        return False
    candidates = _action_candidates(tool_name, tool_args)
    return any(
        re.search(pattern, candidate, re.IGNORECASE)
        for pattern in action_policy["tool_name_patterns"]
        for candidate in candidates
    )


def principal_action_preview(tool_name: str, tool_args: Any, max_chars: int = 1800) -> str:
    """Build a credential-safe Discord preview for an action approval prompt."""
    def scrub(value: Any, parent_key: str = "") -> Any:
        if any(term in parent_key.lower() for term in _SENSITIVE_ARGUMENT_TERMS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(key): scrub(child, str(key)) for key, child in value.items()}
        if isinstance(value, list):
            return [scrub(child, parent_key) for child in value[:20]]
        if isinstance(value, str):
            return value if len(value) <= 300 else value[:297] + "..."
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:300]

    try:
        args_text = json.dumps(scrub(tool_args), indent=2, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        args_text = "[arguments unavailable]"
    preview = f"Tool: {tool_name}\nArguments:\n{args_text}"
    return preview if len(preview) <= max_chars else preview[: max_chars - 3] + "..."


def _configured_channel_ids(user_config: Any, platform_key: str) -> Optional[set[str]]:
    """Return the platform channel allowlist, or None when no gate is configured."""
    if not isinstance(user_config, dict):
        return set()
    platform_name = str(getattr(platform_key, "value", platform_key))
    platform_config = user_config.get(platform_name)
    if not isinstance(platform_config, dict) or "allowed_channels" not in platform_config:
        return None
    raw = platform_config.get("allowed_channels")
    if isinstance(raw, str):
        return {item.strip() for item in raw.split(",") if item.strip()}
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return {item for item in raw if item}
    return set()


def principal_execution_tool_authorized(
    user_config: Any,
    platform_key: str,
    source: Any,
    tool_name: str,
    *,
    registry: Any,
    fallback_toolsets: list[str],
) -> bool:
    """Recheck principal, guild, channel, and toolset immediately before a tool.

    Legacy platforms without a principal policy retain their existing behavior.
    Once a policy is present, malformed config, a DM, a different guild or
    channel, an unknown tool, or a revoked toolset all fail closed.
    """
    if not principal_policy_present(user_config, platform_key):
        return True
    if not principal_guild_authorized(user_config, platform_key, source):
        return False

    allowed_channels = _configured_channel_ids(user_config, platform_key)
    if allowed_channels is not None:
        source_channels = {
            str(value)
            for value in (
                getattr(source, "chat_id", None),
                getattr(source, "parent_chat_id", None),
            )
            if value
        }
        if not source_channels.intersection(allowed_channels):
            return False

    allowed_toolsets = set(
        resolve_principal_toolsets(
            user_config,
            platform_key,
            source,
            fallback_toolsets,
        )
    )
    toolset = registry.get_toolset_for_tool(str(tool_name or ""))
    return bool(toolset) and toolset in allowed_toolsets
