from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from deployments.client_connection_portal.portal.oauth import build_authorization_url
from deployments.client_connection_portal.portal.registry import get_tenant


def test_google_email_authorization_uses_official_url_read_only_scope_and_pkce() -> None:
    slot = get_tenant("cjs-landscape").get_slot("gmail-primary")

    url = build_authorization_url(
        slot=slot,
        client_id="google-client-id",
        redirect_uri="https://portal.example.com/oauth/callback/google",
        state="signed-state",
        code_challenge="pkce-challenge",
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query["client_id"] == ["google-client-id"]
    assert query["redirect_uri"] == [
        "https://portal.example.com/oauth/callback/google"
    ]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["signed-state"]
    assert query["code_challenge"] == ["pkce-challenge"]
    assert query["code_challenge_method"] == ["S256"]
    assert "https://www.googleapis.com/auth/gmail.readonly" in query["scope"][0]
    assert "gmail.modify" not in query["scope"][0]
    assert "gmail.send" not in query["scope"][0]
    assert "password" not in query


def test_microsoft_email_authorization_uses_mail_read_without_send() -> None:
    slot = get_tenant("cjs-landscape").get_slot("microsoft-primary")

    url = build_authorization_url(
        slot=slot,
        client_id="microsoft-client-id",
        redirect_uri="https://portal.example.com/oauth/callback/microsoft",
        state="signed-state",
        code_challenge="pkce-challenge",
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )
    scopes = query["scope"][0].split()
    assert "Mail.Read" in scopes
    assert "Mail.Send" not in scopes
    assert "offline_access" in scopes
    assert query["response_mode"] == ["query"]
    assert query["code_challenge_method"] == ["S256"]


def test_authorization_builder_rejects_non_tls_or_mismatched_callback_hosts() -> None:
    slot = get_tenant("cjs-landscape").get_slot("gmail-primary")

    for redirect_uri in (
        "http://portal.example.com/oauth/callback/google",
        "https://evil.example/oauth/callback/google",
        "https://portal.example.com/oauth/callback/microsoft",
    ):
        try:
            build_authorization_url(
                slot=slot,
                client_id="google-client-id",
                redirect_uri=redirect_uri,
                state="signed-state",
                code_challenge="pkce-challenge",
                expected_base_url="https://portal.example.com",
            )
        except ValueError as exc:
            assert str(exc) == "invalid OAuth redirect URI"
        else:
            raise AssertionError(f"unsafe redirect URI accepted: {redirect_uri}")
