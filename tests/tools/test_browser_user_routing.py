"""Fail-closed per-gateway-user CDP routing and browser-state isolation."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from gateway.session_context import clear_session_vars, set_session_vars
from tools import browser_cdp_tool, browser_dialog_tool, browser_supervisor, browser_tool


RICO = "1378208835302592534"
JAKOB = "998877665544332211"
RICO_WS = "ws://rico.invalid:9241/devtools/browser/rico-secret"
JAKOB_WS = "wss://jakob.invalid/devtools/browser/jakob-secret"
GLOBAL_WS = "ws://global.invalid:9230/devtools/browser/global"


def _config(*, routes=None, endpoints=None, global_url=GLOBAL_WS):
    return {
        "browser": {
            "cdp_url": global_url,
            "cdp_endpoints": endpoints
            if endpoints is not None
            else {"rico_local": {"url": RICO_WS}, "jakob_local": {"url": JAKOB_WS}},
            "cdp_routes": routes
            if routes is not None
            else {"discord": {RICO: "rico_local", JAKOB: "jakob_local"}},
        }
    }


@pytest.fixture(autouse=True)
def _clean_context_and_state(monkeypatch):
    clear_session_vars([])
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    monkeypatch.setattr(browser_tool, "_active_sessions", {})
    monkeypatch.setattr(browser_tool, "_session_last_activity", {})
    monkeypatch.setattr(browser_tool, "_recording_sessions", set())
    monkeypatch.setattr(browser_tool, "_last_active_session_key", {})
    monkeypatch.setattr(browser_tool, "_scoped_session_keys_by_base", {}, raising=False)
    yield
    clear_session_vars([])


def _set_config(monkeypatch, config):
    monkeypatch.setattr("hermes_cli.config.read_raw_config", lambda: config)


def test_named_route_normalizes_platform_and_string_user_id_and_expands_env(monkeypatch):
    set_session_vars(platform="  DISCORD  ", user_id=int(RICO))  # type: ignore[arg-type]
    monkeypatch.setenv("RICO_BROWSER_CDP_URL", RICO_WS)
    _set_config(
        monkeypatch,
        _config(
            routes={"Discord": {int(RICO): "rico_local"}},
            endpoints={"rico_local": {"url": "${RICO_BROWSER_CDP_URL}"}},
        ),
    )

    assert browser_tool._get_cdp_override() == RICO_WS


def test_mapped_route_wins_over_environment_and_global(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    _set_config(monkeypatch, _config())

    assert browser_tool._get_cdp_override() == RICO_WS


@pytest.mark.parametrize(
    ("platform", "user_id"),
    [("discord", "unmapped"), ("", RICO), ("discord", "")],
)
def test_unmapped_or_incomplete_gateway_identity_preserves_global_behavior(
    monkeypatch, platform, user_id
):
    set_session_vars(platform=platform, user_id=user_id)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    _set_config(monkeypatch, _config())

    assert browser_tool._get_cdp_override() == GLOBAL_WS
    assert browser_tool._route_scoped_task_key("same-task") == "same-task"


def test_display_name_is_never_used_for_route_matching(monkeypatch):
    set_session_vars(platform="discord", user_id="unmapped", user_name=RICO)
    _set_config(monkeypatch, _config())

    assert browser_tool._get_cdp_override() == GLOBAL_WS


def test_environment_only_identity_is_not_treated_as_gateway_context(monkeypatch):
    from gateway import session_context

    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "discord")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    monkeypatch.setattr(
        session_context,
        "get_session_env",
        lambda name, default="": __import__("os").environ.get(name, default),
    )
    monkeypatch.setattr(session_context, "has_gateway_context", lambda: False, raising=False)
    _set_config(monkeypatch, _config())

    assert browser_tool._get_cdp_override() == GLOBAL_WS


@pytest.mark.parametrize(
    "config",
    [
        _config(routes={"discord": {RICO: None}}),
        _config(routes={"discord": {RICO: "missing"}}),
        _config(endpoints={"rico_local": {"url": ""}}),
        _config(endpoints={"rico_local": {"url": "${MISSING_ROUTE_ENV}"}}),
        _config(routes={"discord": [RICO]}),
        _config(routes=[{"platform": "discord", "user_ids": [RICO], "fail_closed": False}]),
    ],
)
def test_matched_or_malformed_route_configuration_fails_closed(monkeypatch, config):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    _set_config(monkeypatch, config)

    with pytest.raises(browser_tool.BrowserRouteUnavailableError) as exc:
        browser_tool._get_cdp_override()
    assert RICO not in str(exc.value)
    assert GLOBAL_WS not in str(exc.value)


@pytest.mark.parametrize(
    "response_setup",
    [
        lambda response: setattr(response, "raise_for_status", Mock(side_effect=OSError("offline"))),
        lambda response: setattr(response, "json", Mock(side_effect=ValueError("bad json"))),
        lambda response: setattr(response, "json", Mock(return_value={})),
        lambda response: setattr(
            response, "json", Mock(return_value={"webSocketDebuggerUrl": "http://not-ws"})
        ),
    ],
)
def test_mapped_http_discovery_failures_are_strict_and_safe(monkeypatch, response_setup):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(
        monkeypatch,
        _config(endpoints={"rico_local": {"url": "http://rico.invalid:9241"}}),
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"webSocketDebuggerUrl": RICO_WS}
    response_setup(response)

    with patch("tools.browser_tool.requests.get", return_value=response):
        with pytest.raises(browser_tool.BrowserRouteUnavailableError) as exc:
            browser_tool._get_cdp_override()
    assert "rico.invalid" not in str(exc.value)


def test_mapped_http_discovery_returns_websocket(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(
        monkeypatch,
        _config(endpoints={"rico_local": {"url": "http://rico.invalid:9241"}}),
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"webSocketDebuggerUrl": RICO_WS}

    with patch("tools.browser_tool.requests.get", return_value=response) as get:
        assert browser_tool._get_cdp_override() == RICO_WS
    get.assert_called_once_with("http://rico.invalid:9241/json/version", timeout=10)


def test_mapped_http_discovery_pins_advertised_path_to_assigned_authority(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(
        monkeypatch,
        _config(endpoints={"rico_local": {"url": "http://127.0.0.1:9241"}}),
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "webSocketDebuggerUrl": "ws://127.0.0.1:9227/devtools/browser/opaque-token"
    }

    with patch("tools.browser_tool.requests.get", return_value=response):
        assert (
            browser_tool._get_cdp_override()
            == "ws://127.0.0.1:9241/devtools/browser/opaque-token"
        )


def test_same_base_task_is_scoped_by_opaque_identity_hash(monkeypatch):
    _set_config(monkeypatch, _config())
    set_session_vars(platform="discord", user_id=RICO)
    rico_key = browser_tool._route_scoped_task_key("same-task")
    set_session_vars(platform="discord", user_id=JAKOB)
    jakob_key = browser_tool._route_scoped_task_key("same-task")

    assert rico_key != jakob_key
    assert rico_key.startswith("same-task::route:")
    assert RICO not in rico_key and JAKOB not in jakob_key
    assert browser_tool._scoped_session_keys_by_base["same-task"] == {rico_key, jakob_key}


def test_same_task_navigation_and_actions_do_not_share_last_active_state(monkeypatch):
    _set_config(monkeypatch, _config())
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(
        browser_tool,
        "_get_session_info",
        lambda key: {"session_name": key, "cdp_url": RICO_WS, "features": {}, "_first_nav": False},
    )
    calls = []

    def run(key, command, args=None, timeout=None, **kwargs):
        calls.append((key, command))
        if command == "open":
            return {"success": True, "data": {"url": args[0], "title": "ok"}}
        return {"success": True, "data": {"snapshot": "ok", "refs": {}}}

    monkeypatch.setattr(browser_tool, "_run_browser_command", run)

    set_session_vars(platform="discord", user_id=RICO)
    json.loads(browser_tool.browser_navigate("https://example.com", task_id="same-task"))
    json.loads(browser_tool.browser_snapshot(task_id="same-task"))
    set_session_vars(platform="discord", user_id=JAKOB)
    json.loads(browser_tool.browser_navigate("https://example.org", task_id="same-task"))
    json.loads(browser_tool.browser_snapshot(task_id="same-task"))

    rico_keys = {key for key, _ in calls[:3]}
    jakob_keys = {key for key, _ in calls[3:]}
    assert len(rico_keys) == 1 and len(jakob_keys) == 1
    assert rico_keys.isdisjoint(jakob_keys)


def test_supervisor_attachment_failure_is_safe_and_never_falls_back(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config())
    monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
    cloud = Mock(side_effect=AssertionError("cloud fallback called"))
    local = Mock(side_effect=AssertionError("local fallback called"))
    monkeypatch.setattr(browser_tool, "_get_cloud_provider", cloud)
    monkeypatch.setattr(browser_tool, "_create_local_session", local)
    monkeypatch.setattr(
        browser_supervisor.SUPERVISOR_REGISTRY,
        "get_or_start",
        Mock(side_effect=ConnectionError(f"secret endpoint {RICO_WS}")),
    )

    with pytest.raises(browser_tool.BrowserRouteUnavailableError) as exc:
        browser_tool._get_session_info(browser_tool._route_scoped_task_key("task"))
    assert RICO_WS not in str(exc.value)
    cloud.assert_not_called()
    local.assert_not_called()
    assert browser_tool._active_sessions == {}


def test_browser_navigate_returns_structured_safe_route_error(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config(endpoints={"rico_local": {"url": ""}}))
    run = Mock(side_effect=AssertionError("fallback command called"))
    monkeypatch.setattr(browser_tool, "_run_browser_command", run)

    result = json.loads(browser_tool.browser_navigate("https://example.com", task_id="task"))

    assert result == {
        "success": False,
        "error": "browser_route_unavailable",
        "message": "The browser assigned to this gateway user is unavailable.",
    }
    run.assert_not_called()


def test_stateless_browser_cdp_uses_mapped_endpoint(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config())
    cdp_call = Mock(return_value={"targetInfos": []})
    monkeypatch.setattr(browser_cdp_tool, "_run_async", lambda coro: coro)
    monkeypatch.setattr(browser_cdp_tool, "_cdp_call", cdp_call)

    result = json.loads(browser_cdp_tool.browser_cdp("Target.getTargets", task_id="same-task"))

    assert result["success"] is True
    assert cdp_call.call_args.args[0] == RICO_WS


def test_stateless_browser_cdp_reads_one_atomic_routing_snapshot(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    read_config = Mock(return_value=_config()["browser"])
    monkeypatch.setattr(browser_tool, "_read_browser_config", read_config)
    cdp_call = Mock(return_value={"targetInfos": []})
    monkeypatch.setattr(browser_cdp_tool, "_run_async", lambda coro: coro)
    monkeypatch.setattr(browser_cdp_tool, "_cdp_call", cdp_call)

    result = json.loads(browser_cdp_tool.browser_cdp("Target.getTargets", task_id="same-task"))

    assert result["success"] is True
    assert cdp_call.call_args.args[0] == RICO_WS
    read_config.assert_called_once_with()


def test_frame_and_dialog_lookup_only_scoped_expected_supervisor(monkeypatch):
    _set_config(monkeypatch, _config())
    registry_get = Mock(return_value=None)
    monkeypatch.setattr(browser_supervisor.SUPERVISOR_REGISTRY, "get", registry_get)

    set_session_vars(platform="discord", user_id=RICO)
    json.loads(
        browser_cdp_tool.browser_cdp(
            "Runtime.evaluate", frame_id="frame", task_id="same-task"
        )
    )
    rico_call = registry_get.call_args
    registry_get.reset_mock()
    json.loads(browser_dialog_tool.browser_dialog("dismiss", task_id="same-task"))
    dialog_call = registry_get.call_args

    assert rico_call.args[0].startswith("same-task::route:")
    assert rico_call.kwargs["expected_cdp_url"] == RICO_WS
    assert dialog_call == rico_call


def test_cleanup_base_task_closes_only_current_identity_scoped_session(monkeypatch):
    _set_config(monkeypatch, _config())
    set_session_vars(platform="discord", user_id=RICO)
    rico_key = browser_tool._route_scoped_task_key("same-task")
    set_session_vars(platform="discord", user_id=JAKOB)
    jakob_key = browser_tool._route_scoped_task_key("same-task")
    browser_tool._active_sessions.update(
        {
            rico_key: {"session_name": "rico"},
            jakob_key: {"session_name": "jakob"},
        }
    )
    cleanup = Mock()
    monkeypatch.setattr(browser_tool, "_cleanup_single_browser_session", cleanup)

    browser_tool.cleanup_browser("same-task")

    assert [call.args[0] for call in cleanup.call_args_list] == [jakob_key]
    assert browser_tool._scoped_session_keys_by_base["same-task"] == {rico_key}


def test_mapped_cleanup_without_scoped_session_never_closes_global_session(monkeypatch):
    _set_config(monkeypatch, _config())
    global_session = {"session_name": "global"}
    browser_tool._active_sessions["same-task"] = global_session
    set_session_vars(platform="discord", user_id=RICO)

    cleanup = Mock()
    monkeypatch.setattr(browser_tool, "_cleanup_single_browser_session", cleanup)

    browser_tool.cleanup_browser("same-task")

    cleanup.assert_called_once()
    assert cleanup.call_args.args[0].startswith("same-task::route:")
    assert cleanup.call_args.args[0] != "same-task"
    assert browser_tool._active_sessions["same-task"] is global_session


def test_registry_get_checks_url_thread_loop_and_active_state():
    registry = browser_supervisor._SupervisorRegistry()
    healthy = SimpleNamespace(
        cdp_url=RICO_WS,
        _thread=SimpleNamespace(is_alive=lambda: True),
        _loop=SimpleNamespace(is_running=lambda: True),
        _active=True,
    )
    registry._by_task["task"] = healthy

    assert registry.get("task") is healthy
    assert registry.get("task", expected_cdp_url=JAKOB_WS) is None
    healthy._active = False
    assert registry.get("task", expected_cdp_url=RICO_WS) is None


def test_mapped_navigation_ignores_camofox_and_uses_assigned_cdp(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config())
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
    monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(
        browser_tool,
        "_get_session_info",
        lambda key: {
            "session_name": key,
            "cdp_url": RICO_WS,
            "features": {"proxies": True},
            "_first_nav": False,
        },
    )
    camofox = Mock(side_effect=AssertionError("Camofox must not be used"))
    monkeypatch.setattr("tools.browser_camofox.camofox_navigate", camofox)
    run = Mock(
        side_effect=[
            {"success": True, "data": {"url": "https://example.com", "title": "ok"}},
            {"success": True, "data": {"snapshot": "ok", "refs": {}}},
        ]
    )
    monkeypatch.setattr(browser_tool, "_run_browser_command", run)

    result = json.loads(browser_tool.browser_navigate("https://example.com", task_id="same"))

    assert result["success"] is True
    assert all(call.args[0].startswith("same::route:") for call in run.call_args_list)
    camofox.assert_not_called()


@pytest.mark.parametrize(
    ("name", "invoke"),
    [
        ("snapshot", lambda: browser_tool.browser_snapshot(task_id="same")),
        ("click", lambda: browser_tool.browser_click("e1", task_id="same")),
        ("type", lambda: browser_tool.browser_type("e1", "text", task_id="same")),
        ("scroll", lambda: browser_tool.browser_scroll("down", task_id="same")),
        ("back", lambda: browser_tool.browser_back(task_id="same")),
        ("press", lambda: browser_tool.browser_press("Enter", task_id="same")),
        ("console", lambda: browser_tool.browser_console(task_id="same")),
        ("eval", lambda: browser_tool.browser_console(expression="document.title", task_id="same")),
        ("images", lambda: browser_tool.browser_get_images(task_id="same")),
        ("vision", lambda: browser_tool.browser_vision("what is shown?", task_id="same")),
    ],
)
def test_mapped_non_navigation_tools_never_use_camofox(monkeypatch, name, invoke):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config())
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
    monkeypatch.setattr(browser_tool, "_get_browser_engine", lambda: "auto")
    for attr in (
        "camofox_snapshot",
        "camofox_click",
        "camofox_type",
        "camofox_scroll",
        "camofox_back",
        "camofox_press",
        "camofox_console",
        "camofox_get_images",
        "camofox_vision",
    ):
        monkeypatch.setattr(
            f"tools.browser_camofox.{attr}",
            Mock(side_effect=AssertionError(f"{name} used Camofox")),
        )
    monkeypatch.setattr(
        browser_tool,
        "_camofox_eval",
        Mock(side_effect=AssertionError("eval used Camofox")),
    )
    run = Mock(return_value={"success": False, "error": "assigned CDP command failed"})
    monkeypatch.setattr(browser_tool, "_run_browser_command", run)

    result = json.loads(invoke())

    assert result["success"] is False or name == "console"
    assert run.called
    assert all(call.args[0].startswith("same::route:") for call in run.call_args_list)


def test_same_task_two_mapped_users_stay_isolated_when_camofox_enabled(monkeypatch):
    _set_config(monkeypatch, _config())
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
    camofox = Mock(side_effect=AssertionError("Camofox must not be used"))
    monkeypatch.setattr("tools.browser_camofox.camofox_snapshot", camofox)
    run = Mock(return_value={"success": True, "data": {"snapshot": "ok", "refs": {}}})
    monkeypatch.setattr(browser_tool, "_run_browser_command", run)

    set_session_vars(platform="discord", user_id=RICO)
    json.loads(browser_tool.browser_snapshot(task_id="same"))
    set_session_vars(platform="discord", user_id=JAKOB)
    json.loads(browser_tool.browser_snapshot(task_id="same"))

    keys = [call.args[0] for call in run.call_args_list]
    assert len(set(keys)) == 2
    assert all(key.startswith("same::route:") for key in keys)
    camofox.assert_not_called()


@pytest.mark.parametrize(
    "routes",
    [
        "{not valid json",
        {"discord": "malformed-platform-routes"},
        {"discord": ["not-a-user-map"]},
    ],
)
def test_malformed_route_for_current_platform_fails_closed(monkeypatch, routes):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    _set_config(monkeypatch, _config(routes=routes))

    with pytest.raises(browser_tool.BrowserRouteUnavailableError):
        browser_tool._get_cdp_override()


def test_duplicate_normalized_platform_keys_fail_closed(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    _set_config(
        monkeypatch,
        _config(
            routes={
                " Discord ": {"someone-else": "jakob_local"},
                "discord": {RICO: "rico_local"},
            }
        ),
    )

    with pytest.raises(browser_tool.BrowserRouteUnavailableError):
        browser_tool._get_cdp_override()


def test_duplicate_normalized_user_ids_fail_closed(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    _set_config(
        monkeypatch,
        _config(
            routes={
                "discord": {
                    RICO: "rico_local",
                    f" {RICO} ": "jakob_local",
                }
            }
        ),
    )

    with pytest.raises(browser_tool.BrowserRouteUnavailableError):
        browser_tool._get_cdp_override()


@pytest.mark.parametrize("endpoints", ["malformed", [RICO_WS], {"rico_local": RICO_WS}])
def test_malformed_endpoint_map_for_matched_identity_fails_closed(monkeypatch, endpoints):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    _set_config(monkeypatch, _config(endpoints=endpoints))

    with pytest.raises(browser_tool.BrowserRouteUnavailableError):
        browser_tool._get_cdp_override()


@pytest.mark.parametrize("routes", [None, {}])
def test_absent_or_empty_route_map_preserves_legacy_global_fallback(monkeypatch, routes):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)
    config = _config(routes={})
    if routes is None:
        config["browser"].pop("cdp_routes")
    _set_config(monkeypatch, config)

    assert browser_tool._get_cdp_override() == GLOBAL_WS


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: browser_tool.browser_snapshot(task_id="task"),
        lambda: browser_tool.browser_click("e1", task_id="task"),
    ],
)
def test_non_navigation_route_config_errors_are_stable_and_structured(monkeypatch, invoke):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config(routes="{broken"))
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
    run = Mock(side_effect=AssertionError("fallback command called"))
    monkeypatch.setattr(browser_tool, "_run_browser_command", run)

    result = json.loads(invoke())

    assert result == browser_tool._route_error_result()
    run.assert_not_called()


def test_camofox_does_not_satisfy_requirements_for_broken_mapped_route(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config(endpoints={"rico_local": {"url": ""}}))
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)

    assert browser_tool.check_browser_requirements() is False


@pytest.mark.parametrize("command", ["open", "snapshot", "screenshot"])
def test_mapped_run_command_never_uses_lightpanda_chrome_fallback(
    monkeypatch, tmp_path, command
):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config())
    monkeypatch.setattr(browser_tool, "_find_agent_browser", lambda: "/bin/agent-browser")
    monkeypatch.setattr(browser_tool, "_is_local_mode", lambda: False)
    monkeypatch.setattr(browser_tool, "_chromium_installed", lambda: True)
    monkeypatch.setattr(browser_tool, "_get_browser_engine", lambda: "lightpanda")
    monkeypatch.setattr(
        browser_tool,
        "_get_session_info",
        lambda task_id: {"session_name": "mapped", "cdp_url": RICO_WS},
    )
    monkeypatch.setattr(browser_tool, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(browser_tool, "_write_owner_pid", lambda *args: None)
    proc = MagicMock(returncode=1)
    proc.wait.return_value = None
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: proc)
    fallback = Mock(side_effect=AssertionError("Chrome fallback called"))
    screenshot_fallback = Mock(side_effect=AssertionError("Chrome screenshot fallback called"))
    monkeypatch.setattr(browser_tool, "_run_chrome_fallback_command", fallback)
    monkeypatch.setattr(browser_tool, "_chrome_fallback_screenshot", screenshot_fallback)

    with patch("builtins.open", mock_open(read_data=f"failed to connect {RICO_WS}")):
        result = browser_tool._run_browser_command("task::route:test", command, [])

    fallback.assert_not_called()
    screenshot_fallback.assert_not_called()
    assert RICO_WS not in json.dumps(result)
    assert "rico-secret" not in json.dumps(result)


def test_cached_mapped_session_is_replaced_when_named_endpoint_changes(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    config = _config()
    _set_config(monkeypatch, config)
    monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
    monkeypatch.setattr(browser_tool, "_ensure_cdp_supervisor", lambda *args, **kwargs: None)
    stopped = Mock()
    monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", stopped)
    key = browser_tool._route_scoped_task_key("task")

    old_session = browser_tool._get_session_info(key)
    new_url = "wss://rico-new.invalid/devtools/browser/new-token"
    config["browser"]["cdp_endpoints"]["rico_local"]["url"] = new_url
    new_session = browser_tool._get_session_info(key)

    assert old_session["cdp_url"] == RICO_WS
    assert new_session["cdp_url"] == new_url
    assert new_session is not old_session
    stopped.assert_called_with(key)


def test_mapped_session_creation_log_does_not_include_endpoint(monkeypatch, caplog):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config())

    with caplog.at_level(logging.INFO, logger="tools.browser_tool"):
        browser_tool._create_cdp_session("task", RICO_WS)

    assert RICO_WS not in caplog.text
    assert "rico-secret" not in caplog.text


def test_mapped_discovery_log_does_not_include_endpoint(monkeypatch, caplog):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(
        monkeypatch,
        _config(endpoints={"rico_local": {"url": "http://rico.invalid:9241"}}),
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"webSocketDebuggerUrl": RICO_WS}

    with caplog.at_level(logging.INFO, logger="tools.browser_tool"):
        with patch("tools.browser_tool.requests.get", return_value=response):
            assert browser_tool._get_cdp_override() == RICO_WS

    assert "rico.invalid" not in caplog.text
    assert "rico-secret" not in caplog.text


def test_direct_mapped_cdp_connection_error_does_not_leak_endpoint(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    _set_config(monkeypatch, _config())
    def fail_connection(coro):
        coro.close()
        raise browser_cdp_tool.WebSocketException(f"cannot connect {RICO_WS}")

    monkeypatch.setattr(browser_cdp_tool, "_run_async", fail_connection)

    result = browser_cdp_tool.browser_cdp("Target.getTargets", task_id="task")

    assert RICO_WS not in result
    assert "rico-secret" not in result


def test_supervisor_registry_stops_concurrent_different_url_loser(monkeypatch):
    registry = browser_supervisor._SupervisorRegistry()

    class FakeSupervisor:
        def __init__(self, cdp_url, on_start=None):
            self.cdp_url = cdp_url
            self._thread = SimpleNamespace(is_alive=lambda: True)
            self._loop = SimpleNamespace(is_running=lambda: True)
            self._active = True
            self._on_start = on_start
            self.stopped = False

        def start(self, timeout=15.0):
            if self._on_start:
                self._on_start()

        def stop(self):
            self.stopped = True

    competitor = FakeSupervisor(JAKOB_WS)

    def install_competitor():
        registry._by_task["task"] = competitor  # type: ignore[assignment]

    candidate = FakeSupervisor(RICO_WS, on_start=install_competitor)
    monkeypatch.setattr(browser_supervisor, "CDPSupervisor", lambda **kwargs: candidate)

    chosen = registry.get_or_start("task", RICO_WS)

    assert chosen is candidate
    assert registry.get("task", expected_cdp_url=RICO_WS) is candidate
    assert competitor.stopped is True


def test_cleanup_exact_routed_key_removes_reverse_and_last_active_indexes(monkeypatch):
    scoped = "task::route:opaque"
    browser_tool._scoped_session_keys_by_base["task"] = {scoped}
    browser_tool._last_active_session_key[scoped] = scoped
    monkeypatch.setattr(browser_tool, "_cleanup_single_browser_session", lambda key: None)

    browser_tool.cleanup_browser(scoped)

    assert browser_tool._scoped_session_keys_by_base == {}
    assert scoped not in browser_tool._last_active_session_key


def test_unreadable_browser_config_fails_closed_for_gateway_identity(monkeypatch):
    set_session_vars(platform="discord", user_id=RICO)
    monkeypatch.setenv("BROWSER_CDP_URL", GLOBAL_WS)

    def fail_read():
        raise ValueError("malformed config")

    monkeypatch.setattr("hermes_cli.config.read_raw_config", fail_read)

    with pytest.raises(browser_tool.BrowserRouteUnavailableError):
        browser_tool._get_cdp_override()
