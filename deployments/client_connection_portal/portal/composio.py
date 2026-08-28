from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol


class BrokerPolicyError(ValueError):
    """Raised when a broker request falls outside the reviewed CJS sandbox policy."""


class SessionTransport(Protocol):
    def create_session(self, payload: dict[str, Any]) -> dict[str, Any]: ...


_GMAIL_READ_ACTIONS = (
    "GMAIL_FETCH_EMAILS",
    "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
    "GMAIL_FETCH_MESSAGE_BY_THREAD_ID",
    "GMAIL_GET_ATTACHMENT",
    "GMAIL_GET_PROFILE",
)
_ALLOWED_ACTIONS = {"gmail": _GMAIL_READ_ACTIONS}
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ComposioSandboxPilot:
    """Creates a fail-closed, metadata-only Composio sandbox session."""

    transport: SessionTransport

    def create_read_only_session(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        toolkit: str,
    ) -> dict[str, Any]:
        if tenant_id != "cjs-landscape":
            raise BrokerPolicyError("tenant is outside the CJS broker pilot")
        if toolkit not in _ALLOWED_ACTIONS:
            raise BrokerPolicyError("toolkit is outside the reviewed broker pilot")
        if not _SAFE_ID_RE.fullmatch(operator_id):
            raise BrokerPolicyError("operator id is invalid")

        actions = list(_ALLOWED_ACTIONS[toolkit])
        payload = {
            "user_id": f"tekton:{tenant_id}:{operator_id}",
            "toolkits": {"enabled": [toolkit]},
            "tools": {toolkit: {"enabled": actions}},
            "tags": {"enabled": ["readOnlyHint"]},
            "workbench": {
                "enable": False,
                "enable_proxy_execution": False,
            },
            "manage_connections": {
                "enable": False,
                "enable_wait_for_connections": False,
                "enable_connection_removal": False,
            },
            "multi_account": {
                "enable": False,
                "require_explicit_selection": True,
            },
            "preload": {"tools": actions},
        }
        response = self.transport.create_session(payload)
        session_id = response.get("session_id")
        if not isinstance(session_id, str) or not session_id.startswith("trs_"):
            raise BrokerPolicyError("broker returned an invalid sandbox session")

        return {
            "provider": "composio",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "toolkit": toolkit,
            "mode": "sandbox",
            "live_account_connected": False,
        }


__all__ = ["BrokerPolicyError", "ComposioSandboxPilot", "SessionTransport"]
