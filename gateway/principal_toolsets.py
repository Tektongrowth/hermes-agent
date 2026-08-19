"""Resolve gateway toolsets for an authenticated platform principal."""

from __future__ import annotations

from typing import Any, Optional


def _normalize_toolsets(value: Any) -> Optional[list[str]]:
    """Return a stable toolset set, or ``None`` when a policy value is invalid."""
    if not isinstance(value, list):
        return None
    if any(not isinstance(item, str) for item in value):
        return None
    return sorted(set(value))


def _valid_platform_policy(policy: Any) -> bool:
    """Validate a complete platform policy before granting any capability.

    A principal policy is an authorization boundary, not a best-effort config.
    Validate every entry, including mappings that do not match the current
    sender, so a typo cannot silently preserve a privileged default grant.
    """
    if not isinstance(policy, dict):
        return False
    allowed_keys = {"default", "users", "roles", "guilds"}
    if any(not isinstance(key, str) or key not in allowed_keys for key in policy):
        return False
    if "default" in policy and _normalize_toolsets(policy["default"]) is None:
        return False
    if "guilds" in policy:
        guilds = policy["guilds"]
        if not isinstance(guilds, list) or any(not isinstance(guild, str) for guild in guilds):
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
        default = configured_default

    user_id = getattr(source, "user_id", None)
    users = policy.get("users", {})
    if not isinstance(users, dict) or any(not isinstance(key, str) for key in users):
        return []
    roles = policy.get("roles", {})
    if not isinstance(roles, dict) or any(not isinstance(key, str) for key in roles):
        return []

    if user_id is not None and user_id in users:
        user_toolsets = _normalize_toolsets(users[user_id])
        return [] if user_toolsets is None else user_toolsets

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
        matched_toolsets.add(tuple(role_toolsets))

    if len(matched_toolsets) == 1:
        return list(next(iter(matched_toolsets)))
    if len(matched_toolsets) > 1:
        # Ambiguous multi-role grants must never widen to a broader platform
        # default. Operators should map overlapping roles to the same explicit
        # toolset set or use an exact-user override.
        return []
    return default
