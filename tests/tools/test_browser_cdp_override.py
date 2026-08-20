import json
from unittest.mock import Mock, patch

import pytest

from gateway.session_context import clear_session_vars, set_session_vars


HOST = "example-host"
PORT = 9223
WS_URL = f"ws://{HOST}:{PORT}/devtools/browser/abc123"
HTTP_URL = f"http://{HOST}:{PORT}"
VERSION_URL = f"{HTTP_URL}/json/version"


@pytest.fixture(autouse=True)
def _clean_gateway_identity():
    clear_session_vars([])
    yield
    clear_session_vars([])


class TestResolveCdpOverride:
    def test_keeps_full_devtools_websocket_url(self):
        from tools.browser_tool import _resolve_cdp_override

        assert _resolve_cdp_override(WS_URL) == WS_URL

    def test_resolves_http_discovery_endpoint_to_websocket(self):
        from tools.browser_tool import _resolve_cdp_override

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = _resolve_cdp_override(HTTP_URL)

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_resolves_bare_ws_hostport_to_discovery_websocket(self):
        from tools.browser_tool import _resolve_cdp_override

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = _resolve_cdp_override(f"ws://{HOST}:{PORT}")

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_falls_back_to_raw_url_when_discovery_fails(self):
        from tools.browser_tool import _resolve_cdp_override

        with patch("tools.browser_tool.requests.get", side_effect=RuntimeError("boom")):
            assert _resolve_cdp_override(HTTP_URL) == HTTP_URL

    def test_normalizes_provider_returned_http_cdp_url_when_creating_session(self, monkeypatch):
        import tools.browser_tool as browser_tool

        provider = Mock()
        provider.create_session.return_value = {
            "session_name": "cloud-session",
            "bb_session_id": "bu_123",
            "cdp_url": "https://cdp.browser-use.example/session",
            "features": {"browser_use": True},
        }

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        monkeypatch.setattr(browser_tool, "_active_sessions", {})
        monkeypatch.setattr(browser_tool, "_session_last_activity", {})
        monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
        monkeypatch.setattr(browser_tool, "_update_session_activity", lambda task_id: None)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            session_info = browser_tool._get_session_info("task-browser-use")

        assert session_info["cdp_url"] == WS_URL
        provider.create_session.assert_called_once_with("task-browser-use")
        mock_get.assert_called_once_with(
            "https://cdp.browser-use.example/session/json/version",
            timeout=10,
        )


class TestGetCdpOverride:
    def test_prefers_env_var_over_config(self, monkeypatch):
        import tools.browser_tool as browser_tool

        monkeypatch.setenv("BROWSER_CDP_URL", HTTP_URL)
        monkeypatch.setattr(
            browser_tool,
            "read_raw_config",
            lambda: {"browser": {"cdp_url": "http://config-host:9222"}},
            raising=False,
        )

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = browser_tool._get_cdp_override()

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_uses_config_browser_cdp_url_when_env_missing(self, monkeypatch):
        import tools.browser_tool as browser_tool

        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("hermes_cli.config.read_raw_config", return_value={"browser": {"cdp_url": HTTP_URL}}), \
             patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = browser_tool._get_cdp_override()

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)


class TestSessionCdpRouting:
    RICO_ID = "1378208835302592534"
    RICO_WS = "ws://127.0.0.1:9241/devtools/browser/rico"
    GLOBAL_WS = "ws://127.0.0.1:9230/devtools/browser/mac-mini"

    @staticmethod
    def _config(route_name="rico-windows", route_url=RICO_WS, *, routes=None):
        return {
            "browser": {
                "cdp_url": TestSessionCdpRouting.GLOBAL_WS,
                "cdp_endpoints": {route_name: {"url": route_url}},
                "cdp_routes": routes
                if routes is not None
                else {"discord": {TestSessionCdpRouting.RICO_ID: route_name}},
            }
        }

    def test_matching_discord_user_route_overrides_global_env_and_config(self, monkeypatch):
        import tools.browser_tool as browser_tool

        set_session_vars(platform="discord", user_id=self.RICO_ID)
        monkeypatch.setenv("BROWSER_CDP_URL", self.GLOBAL_WS)

        with patch("hermes_cli.config.read_raw_config", return_value=self._config()):
            assert browser_tool._get_cdp_override() == self.RICO_WS

    def test_routes_accept_json_string_from_config_cli(self, monkeypatch):
        import tools.browser_tool as browser_tool

        set_session_vars(platform="discord", user_id=self.RICO_ID)
        monkeypatch.setenv("BROWSER_CDP_URL", self.GLOBAL_WS)
        routes = json.dumps({"discord": {self.RICO_ID: "rico-windows"}})

        with patch(
            "hermes_cli.config.read_raw_config",
            return_value=self._config(routes=routes),
        ):
            assert browser_tool._get_cdp_override() == self.RICO_WS

    def test_nonmatching_user_keeps_existing_global_override(self, monkeypatch):
        import tools.browser_tool as browser_tool

        set_session_vars(platform="discord", user_id="someone-else")
        monkeypatch.setenv("BROWSER_CDP_URL", self.GLOBAL_WS)

        with patch("hermes_cli.config.read_raw_config", return_value=self._config()):
            assert browser_tool._get_cdp_override() == self.GLOBAL_WS

    def test_matching_route_without_endpoint_fails_closed(self, monkeypatch):
        import tools.browser_tool as browser_tool

        set_session_vars(platform="discord", user_id=self.RICO_ID)
        monkeypatch.setenv("BROWSER_CDP_URL", self.GLOBAL_WS)

        with patch(
            "hermes_cli.config.read_raw_config",
            return_value=self._config(route_url=""),
        ):
            with pytest.raises(browser_tool.BrowserRouteUnavailableError):
                browser_tool._get_cdp_override()

    def test_removed_list_route_cannot_opt_out_of_fail_closed(self, monkeypatch):
        import tools.browser_tool as browser_tool

        set_session_vars(platform="discord", user_id=self.RICO_ID)
        monkeypatch.setenv("BROWSER_CDP_URL", self.GLOBAL_WS)
        route = {
            "name": "rico-windows",
            "platform": "discord",
            "user_ids": [self.RICO_ID],
            "cdp_url": "",
            "fail_closed": False,
        }

        with patch(
            "hermes_cli.config.read_raw_config",
            return_value=self._config(routes=[route]),
        ):
            with pytest.raises(browser_tool.BrowserRouteUnavailableError):
                browser_tool._get_cdp_override()
