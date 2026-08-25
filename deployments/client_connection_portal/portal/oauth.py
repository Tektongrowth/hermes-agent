from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from .registry import ConnectorSlot, ProviderKind


def build_authorization_url(
    *,
    slot: ConnectorSlot,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    expected_base_url: str | None = None,
) -> str:
    if not slot.authorization_endpoint or not slot.scopes:
        raise ValueError("connector does not support OAuth")
    callback_name = {
        ProviderKind.GOOGLE: "google",
        ProviderKind.MICROSOFT: "microsoft",
        ProviderKind.INTUIT: "intuit",
    }.get(slot.provider)
    if callback_name is None:
        raise ValueError("connector does not support OAuth")

    parsed = urlsplit(redirect_uri)
    expected_path = f"/oauth/callback/{callback_name}"
    valid = (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.path == expected_path
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443)
    )
    if expected_base_url:
        base = urlsplit(expected_base_url)
        valid = valid and (
            base.scheme == "https"
            and parsed.hostname == base.hostname
            and parsed.port == base.port
        )
    if not valid:
        raise ValueError("invalid OAuth redirect URI")
    if not client_id or not state or not code_challenge:
        raise ValueError("OAuth request is incomplete")

    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(slot.scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if slot.provider is ProviderKind.GOOGLE:
        query.update(
            {
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
            }
        )
    elif slot.provider is ProviderKind.MICROSOFT:
        query["response_mode"] = "query"
    return f"{slot.authorization_endpoint}?{urlencode(query)}"
