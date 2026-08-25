from __future__ import annotations

import pytest

from deployments.client_connection_portal.portal.security import HmacTokenSigner, TokenError


def test_signed_invitation_round_trip_preserves_tenant_recipient_and_slots() -> None:
    signer = HmacTokenSigner(key=b"test-signing-key-that-is-long-enough", now=lambda: 1_700_000_000)

    token = signer.issue(
        purpose="invitation",
        ttl_seconds=900,
        claims={
            "tenant_id": "cjs-landscape",
            "invitation_id": "inv-123",
            "recipient_email": "alyssa@example.com",
            "slots": ["gmail-primary", "microsoft-operations"],
        },
    )

    claims = signer.verify(token, purpose="invitation")

    assert claims["tenant_id"] == "cjs-landscape"
    assert claims["invitation_id"] == "inv-123"
    assert claims["recipient_email"] == "alyssa@example.com"
    assert claims["slots"] == ["gmail-primary", "microsoft-operations"]
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_000_900


def test_signed_invitation_rejects_tampering() -> None:
    signer = HmacTokenSigner(key=b"test-signing-key-that-is-long-enough", now=lambda: 1_700_000_000)
    token = signer.issue(purpose="invitation", ttl_seconds=60, claims={"tenant_id": "cjs-landscape"})
    body, signature = token.split(".")
    replacement = "A" if body[-1] != "A" else "B"

    with pytest.raises(TokenError, match="invalid token"):
        signer.verify(f"{body[:-1]}{replacement}.{signature}", purpose="invitation")


def test_signed_invitation_rejects_expired_and_wrong_purpose_tokens() -> None:
    clock = {"now": 1_700_000_000}
    signer = HmacTokenSigner(key=b"test-signing-key-that-is-long-enough", now=lambda: clock["now"])
    token = signer.issue(purpose="invitation", ttl_seconds=60, claims={"tenant_id": "cjs-landscape"})

    with pytest.raises(TokenError, match="wrong token purpose"):
        signer.verify(token, purpose="csrf")

    clock["now"] += 61
    with pytest.raises(TokenError, match="expired token"):
        signer.verify(token, purpose="invitation")
