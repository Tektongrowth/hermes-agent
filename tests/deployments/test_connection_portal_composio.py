from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from deployments.client_connection_portal.portal.composio import (
    BrokerPolicyError,
    ComposioSandboxPilot,
)


@dataclass
class RecordingTransport:
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        return {
            "session_id": "trs_sandbox_123",
            "mcp": {
                "type": "http",
                "url": "https://app.composio.dev/tool_router/v3/private-sandbox-url/mcp",
            },
            "config_version": 0,
        }


def test_cjs_composio_pilot_builds_an_exact_read_only_gmail_session() -> None:
    transport = RecordingTransport()
    pilot = ComposioSandboxPilot(transport=transport)

    result = pilot.create_read_only_session(
        tenant_id="cjs-landscape",
        operator_id="nick",
        toolkit="gmail",
    )

    assert result == {
        "provider": "composio",
        "session_id": "trs_sandbox_123",
        "tenant_id": "cjs-landscape",
        "toolkit": "gmail",
        "mode": "sandbox",
        "live_account_connected": False,
    }
    assert len(transport.requests) == 1
    payload = transport.requests[0]
    assert payload["user_id"] == "tekton:cjs-landscape:nick"
    assert payload["toolkits"] == {"enabled": ["gmail"]}
    assert payload["tools"] == {
        "gmail": {
            "enabled": [
                "GMAIL_FETCH_EMAILS",
                "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
                "GMAIL_FETCH_MESSAGE_BY_THREAD_ID",
                "GMAIL_GET_ATTACHMENT",
                "GMAIL_GET_PROFILE",
            ]
        }
    }
    assert payload["tags"] == {"enabled": ["readOnlyHint"]}
    assert payload["workbench"] == {
        "enable": False,
        "enable_proxy_execution": False,
    }
    assert payload["manage_connections"] == {
        "enable": False,
        "enable_wait_for_connections": False,
        "enable_connection_removal": False,
    }
    assert payload["multi_account"] == {
        "enable": False,
        "require_explicit_selection": True,
    }
    assert "private-sandbox-url" not in repr(result)


@pytest.mark.parametrize(
    ("tenant_id", "toolkit"),
    [
        ("another-client", "gmail"),
        ("cjs-landscape", "metaads"),
        ("cjs-landscape", "yeti"),
    ],
)
def test_cjs_composio_pilot_rejects_cross_tenant_or_unreviewed_toolkits(
    tenant_id: str, toolkit: str
) -> None:
    transport = RecordingTransport()
    pilot = ComposioSandboxPilot(transport=transport)

    with pytest.raises(BrokerPolicyError):
        pilot.create_read_only_session(
            tenant_id=tenant_id,
            operator_id="nick",
            toolkit=toolkit,
        )

    assert transport.requests == []


def test_cjs_composio_pilot_does_not_accept_browser_tool_or_account_overrides() -> None:
    pilot = ComposioSandboxPilot(transport=RecordingTransport())

    with pytest.raises(TypeError):
        pilot.create_read_only_session(
            tenant_id="cjs-landscape",
            operator_id="nick",
            toolkit="gmail",
            tools=["GMAIL_SEND_EMAIL"],
            connected_account_id="browser-controlled",
        )
