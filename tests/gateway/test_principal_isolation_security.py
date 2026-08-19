"""Security regression contracts for gateway principal isolation.

These tests intentionally cover boundaries where multiple authenticated principals can
share a chat/thread/session key.  Principal-scoped authorization context must never be
batched, merged, steered, persisted, or proxied across those boundaries.
"""

from __future__ import annotations

from collections import OrderedDict
import threading
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

import pytest

from gateway import run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    merge_pending_message_event,
)
from gateway.principal_toolsets import resolve_principal_toolsets
from gateway.session import SessionSource, SessionStore
from tests.gateway._plugin_adapter_loader import load_plugin_adapter


def _source(
    *,
    platform: Platform = Platform.DISCORD,
    user_id: str = "user-a",
    role_ids: tuple[str, ...] = ("role-reader",),
) -> SessionSource:
    return SessionSource(
        platform=platform,
        chat_id="channel-1",
        chat_type="thread",
        thread_id="thread-1",
        user_id=user_id,
        user_name=user_id,
        principal_role_ids=role_ids,
    )


def _event(
    *,
    source: SessionSource,
    text: str,
    message_type: MessageType = MessageType.TEXT,
    media_urls: list[str] | None = None,
) -> MessageEvent:
    urls = list(media_urls or [])
    return MessageEvent(
        source=source,
        text=text,
        message_type=message_type,
        media_urls=urls,
        media_types=["image/png"] * len(urls),
    )


def test_discord_text_batch_key_includes_authenticated_user_identity():
    """A shared Discord thread must not batch text from two different users."""
    discord_module = load_plugin_adapter("discord")
    adapter = object.__new__(discord_module.DiscordAdapter)
    adapter.config = SimpleNamespace(
        extra={
            "group_sessions_per_user": True,
            "thread_sessions_per_user": False,
        }
    )
    alice = _event(source=_source(user_id="alice"), text="alice secret")
    bob = _event(source=_source(user_id="bob"), text="bob follow-up")

    assert adapter._text_batch_key(alice) != adapter._text_batch_key(bob)


def test_discord_text_batch_key_changes_when_same_users_roles_are_revoked():
    """A role change during debounce must start a separate authenticated batch."""
    discord_module = load_plugin_adapter("discord")
    adapter = object.__new__(discord_module.DiscordAdapter)
    adapter.config = SimpleNamespace(
        extra={
            "group_sessions_per_user": True,
            "thread_sessions_per_user": False,
        }
    )
    before_revocation = _event(
        source=_source(
            user_id="alice",
            role_ids=("role-admin", "role-reader"),
        ),
        text="first chunk while admin",
    )
    same_grants_reordered = _event(
        source=_source(
            user_id="alice",
            role_ids=("role-reader", "role-admin"),
        ),
        text="same authorization context",
    )
    after_revocation = _event(
        source=_source(user_id="alice", role_ids=("role-reader",)),
        text="second chunk after admin was revoked",
    )

    privileged_key = adapter._text_batch_key(before_revocation)
    assert privileged_key == adapter._text_batch_key(same_grants_reordered)
    assert privileged_key != adapter._text_batch_key(after_revocation)


@pytest.mark.parametrize(
    ("existing_source", "incoming_source"),
    [
        pytest.param(
            _source(platform=Platform.DISCORD),
            _source(platform=Platform.TELEGRAM),
            id="platform",
        ),
        pytest.param(
            _source(user_id="user-a"),
            _source(user_id="user-b"),
            id="user-id",
        ),
        pytest.param(
            _source(role_ids=("role-admin",)),
            _source(role_ids=("role-reader",)),
            id="principal-role-ids",
        ),
    ],
)
def test_pending_message_merge_preserves_event_when_principal_differs(
    existing_source: SessionSource,
    incoming_source: SessionSource,
):
    """A different principal cannot erase or merge into an existing queued turn."""
    session_key = "deliberately-shared-session"
    existing = _event(
        source=existing_source,
        text="privileged screenshot",
        message_type=MessageType.PHOTO,
        media_urls=["/tmp/admin-only.png"],
    )
    incoming = _event(source=incoming_source, text="unprivileged follow-up")
    pending = {session_key: existing}

    merged = merge_pending_message_event(pending, session_key, incoming, merge_text=True)

    assert merged is False
    assert pending[session_key] is existing
    assert pending[session_key].text == "privileged screenshot"
    assert pending[session_key].media_urls == ["/tmp/admin-only.png"]


def test_pending_text_merge_preserves_event_from_a_different_principal():
    """The merge_text fast path cannot erase another principal's queued turn."""
    session_key = "shared-text-session"
    existing = _event(source=_source(user_id="user-a"), text="first user's text")
    incoming = _event(source=_source(user_id="user-b"), text="second user's text")
    pending = {session_key: existing}

    merged = merge_pending_message_event(pending, session_key, incoming, merge_text=True)

    assert merged is False
    assert pending[session_key] is existing
    assert pending[session_key].text == "first user's text"


def test_text_debounce_does_not_merge_a_role_revoked_users_turn():
    """A fresh reader turn must not inherit an earlier admin role snapshot."""
    before_revocation = _event(
        source=_source(user_id="same-user", role_ids=("role-admin",)),
        text="admin follow-up",
    )
    after_revocation = _event(
        source=_source(user_id="same-user", role_ids=("role-reader",)),
        text="reader follow-up",
    )

    assert not BasePlatformAdapter._can_merge_text_debounce_events(
        object(), before_revocation, after_revocation
    )


def test_runner_queues_a_different_principal_after_the_existing_pending_turn():
    """A principal conflict preserves both complete turns in FIFO order."""
    runner = object.__new__(gateway_run.GatewayRunner)
    adapter = SimpleNamespace(_pending_messages={})
    runner.adapters = {Platform.DISCORD: adapter}
    runner._queued_events = {}
    session_key = "shared-discord-thread"
    first = _event(source=_source(user_id="admin", role_ids=("role-admin",)), text="admin turn")
    second = _event(source=_source(user_id="crew", role_ids=("role-reader",)), text="crew turn")
    adapter._pending_messages[session_key] = first

    runner._queue_or_replace_pending_event(session_key, second)

    assert adapter._pending_messages[session_key] is first
    assert runner._queued_events[session_key] == [second]


@pytest.mark.asyncio
async def test_busy_steer_queues_when_incoming_principal_resolves_different_toolsets(
    monkeypatch,
):
    """A lower-privilege message cannot steer an agent running another policy."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._busy_input_mode = "steer"
    runner._busy_text_mode = "queue"
    runner._draining = False
    runner._busy_ack_ts = {}
    runner._running_agents = {}
    runner._is_user_authorized = lambda _source: True

    event = _event(
        source=_source(user_id="reader", role_ids=("role-reader",)),
        text="please run this in the privileged turn",
    )
    session_key = "shared-discord-thread"
    adapter = SimpleNamespace(
        _pending_messages={},
        _send_with_retry=AsyncMock(),
    )
    runner.adapters = {Platform.DISCORD: adapter}

    running_agent = MagicMock()
    running_agent.enabled_toolsets = ["terminal"]
    running_agent.steer = MagicMock(return_value=True)
    runner._running_agents[session_key] = running_agent

    incoming_config = {
        "platform_principal_toolsets": {
            "discord": {
                "roles": {"role-reader": ["web"]},
            }
        }
    }
    monkeypatch.setenv("HERMES_GATEWAY_BUSY_ACK_ENABLED", "false")
    with (
        patch("gateway.run._load_gateway_config", return_value=incoming_config),
        patch("gateway.run._resolve_enabled_toolsets", return_value=["web"]) as resolve,
    ):
        handled = await runner._handle_active_session_busy_message(event, session_key)

    assert handled is True
    resolve.assert_called_once_with(incoming_config, event.source)
    running_agent.steer.assert_not_called()
    assert adapter._pending_messages[session_key] is event


def _runner_with_active_agent(event: MessageEvent):
    """Minimal runner that still exercises GatewayRunner._handle_message dispatch."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = GatewayConfig(
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
    runner.session_store = SimpleNamespace()
    runner.adapters = {
        event.source.platform: SimpleNamespace(_pending_messages={}),
    }
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._draining = False
    runner._restart_requested = False
    runner._busy_input_mode = "steer"
    runner._is_user_authorized = lambda _source: True
    runner._check_slash_access = Mock(return_value=None)
    session_key = runner._session_key_for_source(event.source)
    running_agent = MagicMock()
    running_agent.enabled_toolsets = ["terminal"]
    running_agent.steer = MagicMock(return_value=True)
    runner._running_agents[session_key] = running_agent
    return runner, session_key, running_agent


@pytest.mark.asyncio
async def test_direct_steer_command_cannot_cross_principal_toolset_boundary():
    """The production /steer fast path must authorize against the running grant set."""
    event = _event(
        source=_source(user_id="reader", role_ids=("role-reader",)),
        text="/steer run the privileged command now",
    )
    runner, session_key, running_agent = _runner_with_active_agent(event)
    config = {
        "platform_principal_toolsets": {
            "discord": {"roles": {"role-reader": ["web"]}},
        }
    }

    with (
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch("gateway.run._load_gateway_config", return_value=config),
        patch("gateway.run._resolve_enabled_toolsets", return_value=["web"]) as resolve,
    ):
        session_key = runner._session_key_for_source(event.source)
        runner._running_agents = {session_key: running_agent}
        result = await runner._handle_message(event)

    resolve.assert_called_once_with(config, event.source)
    running_agent.steer.assert_not_called()
    queued = runner.adapters[Platform.DISCORD]._pending_messages.get(session_key)
    authorization_error = isinstance(result, str) and any(
        marker in result.lower()
        for marker in ("authoriz", "permission", "policy", "toolset")
    )
    assert queued is not None or authorization_error
    if queued is not None:
        assert queued.text == "run the privileged command now"
        assert queued.source is event.source


@pytest.mark.asyncio
async def test_priority_steer_path_queues_when_principal_toolsets_differ():
    """Normal busy-text priority dispatch cannot inject across an RBAC change."""
    event = _event(
        source=_source(user_id="reader", role_ids=("role-reader",)),
        text="inject this into the admin run",
    )
    runner, session_key, running_agent = _runner_with_active_agent(event)
    config = {
        "platform_principal_toolsets": {
            "discord": {"roles": {"role-reader": ["web"]}},
        }
    }

    with (
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch("gateway.run._load_gateway_config", return_value=config),
        patch("gateway.run._resolve_enabled_toolsets", return_value=["web"]) as resolve,
    ):
        session_key = runner._session_key_for_source(event.source)
        runner._running_agents = {session_key: running_agent}
        result = await runner._handle_message(event)

    assert result is None
    resolve.assert_called_once_with(config, event.source)
    running_agent.steer.assert_not_called()
    assert runner.adapters[Platform.DISCORD]._pending_messages[session_key] is event


_MALFORMED_POLICY_CASES = [
    pytest.param(
        {"platform_principal_toolsets": []},
        id="top-level-policy-container",
    ),
    pytest.param(
        {"platform_principal_toolsets": {"discord": []}},
        id="current-platform-policy",
    ),
    pytest.param(
        {
            "platform_principal_toolsets": {
                "discord": {"default": "terminal"},
            }
        },
        id="explicit-default",
    ),
    pytest.param(
        {
            "platform_principal_toolsets": {
                "discord": {
                    "default": ["web"],
                    "users": {"user-a": "terminal"},
                },
            }
        },
        id="exact-user-value",
    ),
    pytest.param(
        {
            "platform_principal_toolsets": {
                "discord": {
                    "default": ["web"],
                    "roles": ["role-reader"],
                },
            }
        },
        id="roles-map",
    ),
    pytest.param(
        {
            "platform_principal_toolsets": {
                "discord": {
                    "default": ["web"],
                    "roles": {"role-reader": "terminal"},
                },
            }
        },
        id="matched-role-value",
    ),
]


@pytest.mark.parametrize("config", _MALFORMED_POLICY_CASES)
def test_any_explicitly_present_malformed_principal_policy_fails_closed(config):
    """Every malformed structure in a present RBAC policy is deny-all."""
    assert resolve_principal_toolsets(
        config, "discord", _source(), ["terminal", "web"]
    ) == []


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({}, id="top-level-policy-absent"),
        pytest.param(
            {
                "platform_principal_toolsets": {
                    "slack": {"default": []},
                }
            },
            id="current-platform-policy-absent",
        ),
    ],
)
def test_absent_discord_principal_policy_preserves_platform_fallback(config):
    """Fail-closed parsing must not change the legacy behavior when policy is absent."""

    assert resolve_principal_toolsets(
        config, "discord", _source(), ["web", "terminal", "web"]
    ) == [
        "terminal",
        "web",
    ]


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(
            {"default": ["terminal"], "users": {"other-user": "not-a-list"}},
            id="unmatched-user-value",
        ),
        pytest.param(
            {"default": ["terminal"], "roles": {"other-role": "not-a-list"}},
            id="unmatched-role-value",
        ),
        pytest.param(
            {"default": ["terminal"], "guilds": "cjs-guild"},
            id="guilds-not-a-list",
        ),
        pytest.param(
            {"default": ["terminal"], "guilds": [123]},
            id="guild-id-not-a-string",
        ),
    ],
)
def test_any_malformed_entry_in_present_policy_denies_every_grant(policy):
    """Bad unused config must not let a privileged default survive parsing."""
    config = {"platform_principal_toolsets": {"discord": policy}}

    assert resolve_principal_toolsets(
        config,
        "discord",
        _source(user_id="crew-user", role_ids=("role-crew",)),
        ["web"],
    ) == []


def test_principal_policy_isolates_shared_discord_thread_session_keys():
    """Role-scoped tools do not make a shared owner transcript safe to reuse."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = GatewayConfig(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    runner.session_store = SimpleNamespace(
        _generate_session_key=Mock(return_value="shared-thread-session"),
    )
    config = {
        "platform_principal_toolsets": {
            "discord": {"default": [], "roles": {"role-crew": ["synkedup_read"]}},
        }
    }
    owner = _source(user_id="owner", role_ids=("role-owner",))
    crew = _source(user_id="crew", role_ids=("role-crew",))

    with patch("gateway.run._load_gateway_config", return_value=config):
        owner_key = runner._session_key_for_source(owner)
        crew_key = runner._session_key_for_source(crew)

    assert owner_key != crew_key
    assert ":owner:principal:" in owner_key
    assert ":crew:principal:" in crew_key


def test_principal_policy_rotates_session_key_when_a_users_roles_change():
    """Role revocation must create a new transcript boundary for that user."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = GatewayConfig(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    runner.session_store = SimpleNamespace(
        _generate_session_key=Mock(return_value="unsafe-shared-session"),
    )
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": [],
                "roles": {
                    "role-admin": ["terminal"],
                    "role-crew": ["synkedup_read"],
                },
            }
        }
    }
    before_revocation = _source(user_id="same-user", role_ids=("role-admin",))
    after_revocation = _source(user_id="same-user", role_ids=("role-crew",))

    with patch("gateway.run._load_gateway_config", return_value=config):
        admin_key = runner._session_key_for_source(before_revocation)
        crew_key = runner._session_key_for_source(after_revocation)

    assert admin_key != crew_key
    assert "unsafe-shared-session" not in {admin_key, crew_key}


@pytest.mark.asyncio
async def test_principal_policy_blocks_resume_of_another_users_session():
    """A Crew member cannot attach an Owner transcript to their session key."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_db = SimpleNamespace(
        get_session=Mock(return_value={"id": "owner-session", "user_id": "owner"}),
        resolve_resume_session_id=Mock(return_value="owner-session"),
        resolve_session_by_title=Mock(return_value=None),
    )
    runner.session_store = MagicMock()
    event = _event(
        source=_source(user_id="crew", role_ids=("role-crew",)),
        text="/resume owner-session",
    )
    config = {
        "platform_principal_toolsets": {
            "discord": {"default": [], "roles": {"role-crew": ["synkedup_read"]}},
        }
    }

    with patch("gateway.run._load_gateway_config", return_value=config):
        result = await runner._handle_resume_command(event)

    assert "no session found" in result.lower() or "not authorized" in result.lower()
    runner.session_store.switch_session.assert_not_called()


def test_principal_policy_allows_only_explicit_discord_guild_conversation_access(monkeypatch):
    """Open CJS mention access cannot open DMs or another Discord guild."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.pairing_store = SimpleNamespace(is_approved=Mock(return_value=False))
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "guilds": ["cjs-guild"],
                "default": [],
            }
        }
    }
    monkeypatch.setenv("DISCORD_ALLOW_ALL_USERS", "false")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "")
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "false")

    with patch("gateway.run._load_gateway_config", return_value=config):
        assert runner._is_user_authorized(SimpleNamespace(
            platform=Platform.DISCORD,
            user_id="crew-user",
            chat_type="channel",
            guild_id="cjs-guild",
            is_bot=False,
            chat_id="channel-1",
        )) is True
        assert runner._is_user_authorized(SimpleNamespace(
            platform=Platform.DISCORD,
            user_id="other-user",
            chat_type="channel",
            guild_id="another-guild",
            is_bot=False,
            chat_id="channel-1",
        )) is False
        assert runner._is_user_authorized(SimpleNamespace(
            platform=Platform.DISCORD,
            user_id="dm-user",
            chat_type="dm",
            guild_id=None,
            is_bot=False,
            chat_id="dm-1",
        )) is False


def test_live_session_source_cache_drops_ephemeral_principal_roles():
    """The routing cache can outlive a request, so it must not cache role grants."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._session_sources = OrderedDict()
    runner._session_sources_max = 8
    authenticated_source = _source(role_ids=("role-admin",))

    runner._cache_session_source("shared-session", authenticated_source)

    cached = runner._get_cached_session_source("shared-session")
    assert cached is not authenticated_source
    assert cached.principal_role_ids == ()
    assert cached.user_id == authenticated_source.user_id
    assert cached.thread_id == authenticated_source.thread_id


def test_new_session_entry_origin_drops_ephemeral_principal_roles(tmp_path):
    """Persisted auto-resume routing metadata must never retain role grants."""
    store = object.__new__(SessionStore)
    store.sessions_dir = tmp_path / "sessions"
    store.config = GatewayConfig(
        sessions_dir=store.sessions_dir,
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    store._entries = {}
    store._loaded = True
    store._lock = threading.Lock()
    store._has_active_processes_fn = None
    store._db = None
    authenticated_source = _source(role_ids=("role-admin", "role-reader"))

    entry = store.get_or_create_session(authenticated_source)

    assert entry.origin is not authenticated_source
    assert entry.origin is not None
    assert entry.origin.principal_role_ids == ()
    assert entry.origin.user_id == authenticated_source.user_id
    assert entry.origin.thread_id == authenticated_source.thread_id


def test_synthetic_process_event_drops_roles_from_session_origin():
    """Synthetic turns must not inherit the roles of the last real user event."""
    runner = object.__new__(gateway_run.GatewayRunner)
    stale_origin = _source(role_ids=("role-admin",))
    runner.session_store = SimpleNamespace(
        _ensure_loaded=Mock(),
        _entries={"shared-session": SimpleNamespace(origin=stale_origin)},
    )
    runner._session_sources = OrderedDict()

    synthetic_source = runner._build_process_event_source(
        {"session_key": "shared-session", "session_id": "process-1"}
    )

    assert synthetic_source is not stale_origin
    assert synthetic_source.principal_role_ids == ()
    assert synthetic_source.user_id == stale_origin.user_id
    assert synthetic_source.thread_id == stale_origin.thread_id


@pytest.mark.asyncio
async def test_principal_policy_forces_local_enforcement_instead_of_proxy(monkeypatch):
    """Proxy mode cannot bypass principal-scoped toolset enforcement."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._get_proxy_url = Mock(return_value="http://remote-hermes.invalid:8642")
    runner._run_agent_via_proxy = AsyncMock(
        return_value={"final_response": "unsafe remote response"}
    )
    config = {
        "platform_principal_toolsets": {
            "discord": {
                "default": [],
                "users": {"user-a": ["web"]},
            }
        }
    }

    class LocalPolicyResolutionReached(Exception):
        pass

    local_resolution = Mock(side_effect=LocalPolicyResolutionReached)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", Mock(return_value=config))
    monkeypatch.setattr(gateway_run, "_resolve_enabled_toolsets", local_resolution)

    try:
        await runner._run_agent(
            message="use a tool",
            context_prompt="",
            history=[],
            source=_source(user_id="user-a"),
            session_id="session-1",
            session_key="shared-session",
        )
    except LocalPolicyResolutionReached:
        pass

    local_resolution.assert_called_once_with(config, ANY)
    runner._run_agent_via_proxy.assert_not_awaited()


@pytest.mark.parametrize("config", _MALFORMED_POLICY_CASES)
@pytest.mark.asyncio
async def test_malformed_present_policy_forces_local_enforcement(config, monkeypatch):
    """Even malformed explicit RBAC must disable the unenforced proxy path."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._get_proxy_url = Mock(return_value="http://remote-hermes.invalid:8642")
    runner._run_agent_via_proxy = AsyncMock(
        return_value={"final_response": "unsafe remote response"}
    )

    class LocalPolicyResolutionReached(Exception):
        pass

    local_resolution = Mock(side_effect=LocalPolicyResolutionReached)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", Mock(return_value=config))
    monkeypatch.setattr(gateway_run, "_resolve_enabled_toolsets", local_resolution)

    with pytest.raises(LocalPolicyResolutionReached):
        await runner._run_agent(
            message="use a tool",
            context_prompt="",
            history=[],
            source=_source(user_id="user-a"),
            session_id="session-1",
            session_key="shared-session",
        )

    local_resolution.assert_called_once_with(config, ANY)
    runner._run_agent_via_proxy.assert_not_awaited()
