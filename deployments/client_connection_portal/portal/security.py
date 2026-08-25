from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from typing import Any


class TokenError(ValueError):
    """Raised when a signed portal token cannot be trusted."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


class HmacTokenSigner:
    """Issue and verify compact, purpose-bound, expiring HMAC tokens."""

    def __init__(self, *, key: bytes, now: Callable[[], int | float]):
        if len(key) < 32:
            raise ValueError("signing key must contain at least 32 bytes")
        self._key = key
        self._now = now

    def issue(
        self,
        *,
        purpose: str,
        ttl_seconds: int,
        claims: Mapping[str, Any],
    ) -> str:
        if not purpose:
            raise ValueError("purpose is required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if {"purpose", "iat", "exp"}.intersection(claims):
            raise ValueError("claims contain reserved token fields")

        issued_at = int(self._now())
        payload = dict(claims)
        payload.update(
            {
                "purpose": purpose,
                "iat": issued_at,
                "exp": issued_at + int(ttl_seconds),
            }
        )
        body = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _b64encode(
            hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{body}.{signature}"

    def verify(self, token: str, *, purpose: str) -> dict[str, Any]:
        try:
            body, supplied_signature = token.split(".", 1)
            expected_signature = _b64encode(
                hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise TokenError("invalid token")
            payload = json.loads(_b64decode(body).decode("utf-8"))
            if not isinstance(payload, dict):
                raise TokenError("invalid token")
            token_purpose = payload.get("purpose")
            expires_at = payload.get("exp")
        except TokenError:
            raise
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise TokenError("invalid token") from exc

        if token_purpose != purpose:
            raise TokenError("wrong token purpose")
        if not isinstance(expires_at, int):
            raise TokenError("invalid token")
        if int(self._now()) > expires_at:
            raise TokenError("expired token")
        return payload
