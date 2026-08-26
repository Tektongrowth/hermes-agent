from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .registry import ProviderKind, RegistryError, get_tenant
from .security import HmacTokenSigner, TokenError
from .store import MemoryPortalStore, StoreConflict


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COOKIE_NAME = "portal_session"


class PortalStore(Protocol):
    def create_invitation(self, record: Mapping[str, Any]) -> None: ...

    def get_invitation(
        self, *, tenant_id: str, invitation_id: str
    ) -> dict[str, Any] | None: ...

    def claim_invitation(
        self, *, tenant_id: str, invitation_id: str, session_id: str
    ) -> dict[str, Any]: ...

    def save_connection(
        self,
        *,
        tenant_id: str,
        invitation_id: str,
        session_id: str,
        slot_id: str,
        metadata: Mapping[str, Any],
    ) -> None: ...

    def complete_invitation(
        self, *, tenant_id: str, invitation_id: str, session_id: str
    ) -> None: ...


class CredentialVault(Protocol):
    def put_oauth(self, **payload: Any) -> None: ...

    def put_login(self, **payload: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class PortalApp:
    def __init__(
        self,
        *,
        test_mode: bool,
        store: PortalStore,
        signer: HmacTokenSigner,
        base_url: str,
        now: Callable[[], int | float],
        id_factory: Callable[[], str],
        vault: CredentialVault | None = None,
    ):
        self.test_mode = bool(test_mode)
        self.store = store
        self.signer = signer
        self.base_url = base_url.rstrip("/")
        self.now = now
        self.id_factory = id_factory
        self.vault = vault

    def issue_invitation(
        self,
        *,
        tenant_id: str,
        recipient_email: str,
        slots: Sequence[str],
        ttl_seconds: int,
        expected_identities: Mapping[str, str] | None = None,
    ) -> str:
        tenant = get_tenant(tenant_id)
        recipient = _normalize_email(recipient_email)
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        slot_ids = list(dict.fromkeys(slots))
        if not slot_ids:
            raise ValueError("at least one connector slot is required")
        for slot_id in slot_ids:
            tenant.get_slot(slot_id)

        supplied_identities = dict(expected_identities or {})
        if not set(supplied_identities).issubset(slot_ids):
            raise ValueError("expected identity references an uninvited slot")
        resolved_identities: dict[str, str] = {}
        for slot_id in slot_ids:
            slot = tenant.get_slot(slot_id)
            if slot.uses_oauth:
                resolved_identities[slot_id] = _normalize_email(
                    supplied_identities.get(slot_id, recipient)
                )

        issued_at = int(self.now())
        invitation_id = self.id_factory()
        record = {
            "tenant_id": tenant_id,
            "invitation_id": invitation_id,
            "recipient_email": recipient,
            "slots": slot_ids,
            "expected_identities": resolved_identities,
            "connections": {},
            "status": "pending",
            "session_id": None,
            "issued_at": issued_at,
            "expires_at": issued_at + int(ttl_seconds),
        }
        self.store.create_invitation(record)
        return self.signer.issue(
            purpose="invitation",
            ttl_seconds=ttl_seconds,
            claims={
                "tenant_id": tenant_id,
                "invitation_id": invitation_id,
                "recipient_email": recipient,
                "slots": slot_ids,
            },
        )

    def handle(self, request: Request) -> Response:
        method = request.method.upper()
        path = request.path

        if method == "GET" and path.startswith("/s/"):
            return self._claim(path.removeprefix("/s/"))
        if method == "GET" and path == "/setup":
            return self._setup(request)
        if method == "POST" and path == "/test/connect":
            if not self.test_mode:
                return self._not_found()
            return self._test_connect(request)
        if method == "POST" and path == "/complete":
            return self._complete(request)
        if self.test_mode and (
            path == "/oauth/start"
            or path.startswith("/oauth/callback/")
            or path.startswith("/credentials/")
        ):
            return self._not_found()
        if method == "GET" and path == "/health":
            return self._response(200, "ok", content_type="text/plain; charset=utf-8")
        return self._not_found()

    def _claim(self, token: str) -> Response:
        try:
            claims = self.signer.verify(token, purpose="invitation")
            tenant_id = _claim_text(claims, "tenant_id")
            invitation_id = _claim_text(claims, "invitation_id")
            recipient_email = _claim_text(claims, "recipient_email")
            slots = claims.get("slots")
            if not isinstance(slots, list) or not all(isinstance(v, str) for v in slots):
                raise TokenError("invalid token")
            record = self.store.get_invitation(
                tenant_id=tenant_id, invitation_id=invitation_id
            )
            if (
                record is None
                or record.get("recipient_email") != recipient_email
                or record.get("slots") != slots
                or record.get("status") != "pending"
                or int(record.get("expires_at", 0)) < int(self.now())
            ):
                raise StoreConflict("invitation unavailable")
            session_id = self.id_factory()
            claimed = self.store.claim_invitation(
                tenant_id=tenant_id,
                invitation_id=invitation_id,
                session_id=session_id,
            )
            session_ttl = int(claimed["expires_at"]) - int(self.now())
            session_token = self.signer.issue(
                purpose="session",
                ttl_seconds=max(1, session_ttl),
                claims={
                    "tenant_id": tenant_id,
                    "invitation_id": invitation_id,
                    "session_id": session_id,
                },
            )
        except (TokenError, StoreConflict, RegistryError, KeyError, TypeError, ValueError):
            return self._response(410, _message_page("This setup link is unavailable."))

        cookie = (
            f"{_COOKIE_NAME}={session_token}; Path=/; Max-Age={max(1, session_ttl)}; "
            "Secure; HttpOnly; SameSite=Strict"
        )
        return self._response(
            303,
            "",
            extra_headers={"Location": "/setup", "Set-Cookie": cookie},
        )

    def _setup(self, request: Request) -> Response:
        session = self._load_session(request)
        if session is None:
            return self._response(410, _message_page("This setup session is unavailable."))
        record, claims = session
        csrf = self._issue_csrf(record, claims)
        tenant = get_tenant(str(record["tenant_id"]))
        slot_cards: list[str] = []
        connections = record.get("connections", {})
        for slot_id in record["slots"]:
            slot = tenant.get_slot(slot_id)
            connection = connections.get(slot_id)
            status = "Connected" if connection else "Not connected"
            identity = ""
            if connection and connection.get("connected_email"):
                identity = f"<small>{html.escape(str(connection['connected_email']))}</small>"
            if self.test_mode:
                if slot.provider is ProviderKind.YETI:
                    fields = (
                        '<label>Username<input name="username" autocomplete="off"></label>'
                        '<label>Password<input name="password" type="password" autocomplete="new-password"></label>'
                    )
                elif slot.provider is ProviderKind.MICROSOFT:
                    expected = record.get("expected_identities", {}).get(slot_id, "")
                    fields = (
                        '<label>Outlook email address'
                        f'<input name="connected_email" type="email" autocomplete="email" value="{html.escape(str(expected))}"></label>'
                        '<p class="security-note">Your password stays with Microsoft. '
                        'You will enter it only on Microsoft’s official sign-in page when the live connection is enabled.</p>'
                    )
                else:
                    expected = record.get("expected_identities", {}).get(slot_id, "")
                    fields = (
                        '<label>Simulated connected email'
                        f'<input name="connected_email" type="email" value="{html.escape(str(expected))}"></label>'
                    )
                action = (
                    f'<form method="post" action="/test/connect">'
                    f'<input type="hidden" name="csrf" value="{html.escape(csrf)}">'
                    f'<input type="hidden" name="slot" value="{html.escape(slot_id)}">'
                    f"{fields}<button type=\"submit\">Run safe test</button></form>"
                )
            else:
                action = "<p>Production connection is not enabled in this build.</p>"
            slot_cards.append(
                '<section class="card">'
                f"<div><p class=\"eyebrow\">{html.escape(slot.provider.value)}</p>"
                f"<h2>{html.escape(slot.label)}</h2><p class=\"status\">{status}</p>{identity}</div>"
                f"{action}</section>"
            )

        all_connected = set(record.get("connections", {})) == set(record["slots"])
        complete_disabled = "" if all_connected else " disabled"
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(tenant.display_name)} connection setup</title><style>{_CSS}</style></head>
<body><main><header><p class="kicker">Secure connection room</p><h1>Connect {html.escape(tenant.display_name)}</h1>
<p class="intro">This invitation is for <strong>{html.escape(str(record['recipient_email']))}</strong>. Test mode never contacts a provider or saves a credential.</p></header>
<div class="grid">{''.join(slot_cards)}</div>
<form method="post" action="/complete" class="complete"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><button type="submit"{complete_disabled}>Finish setup</button></form>
<footer>Tenant-isolated AWS test environment · read-only connection design</footer></main></body></html>"""
        return self._response(200, body)

    def _test_connect(self, request: Request) -> Response:
        session = self._load_session(request)
        if session is None:
            return self._response(410, _message_page("This setup session is unavailable."))
        record, claims = session
        if not self._valid_csrf(request.form.get("csrf", ""), record, claims):
            return self._response(403, _message_page("The form expired. Reload and try again."))
        slot_id = request.form.get("slot", "")
        if slot_id not in record.get("slots", []):
            return self._not_found()
        try:
            slot = get_tenant(str(record["tenant_id"])).get_slot(slot_id)
        except RegistryError:
            return self._not_found()

        metadata: dict[str, Any] = {
            "status": "connected",
            "provider": slot.provider.value,
            "simulated": True,
            "connected_at": int(self.now()),
        }
        if slot.uses_oauth:
            try:
                connected_email = _normalize_email(request.form.get("connected_email", ""))
            except ValueError:
                return self._response(409, _message_page("The connected account does not match this invitation."))
            expected = record.get("expected_identities", {}).get(slot_id)
            if connected_email != expected:
                return self._response(409, _message_page("The connected account does not match this invitation."))
            metadata["connected_email"] = connected_email
        try:
            self.store.save_connection(
                tenant_id=str(record["tenant_id"]),
                invitation_id=str(record["invitation_id"]),
                session_id=str(claims["session_id"]),
                slot_id=slot_id,
                metadata=metadata,
            )
        except StoreConflict:
            return self._response(410, _message_page("This setup session is unavailable."))
        return self._response(303, "", extra_headers={"Location": "/setup"})

    def _complete(self, request: Request) -> Response:
        session = self._load_session(request)
        if session is None:
            return self._response(410, _message_page("This setup session is unavailable."))
        record, claims = session
        if not self._valid_csrf(request.form.get("csrf", ""), record, claims):
            return self._response(403, _message_page("The form expired. Reload and try again."))
        try:
            self.store.complete_invitation(
                tenant_id=str(record["tenant_id"]),
                invitation_id=str(record["invitation_id"]),
                session_id=str(claims["session_id"]),
            )
        except StoreConflict:
            return self._response(409, _message_page("Connect every requested account before finishing."))
        return self._response(200, _message_page("Setup complete. This session is now locked."))

    def _load_session(
        self, request: Request
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        token = _read_cookie(request.headers.get("Cookie", ""), _COOKIE_NAME)
        if not token:
            return None
        try:
            claims = self.signer.verify(token, purpose="session")
            tenant_id = _claim_text(claims, "tenant_id")
            invitation_id = _claim_text(claims, "invitation_id")
            session_id = _claim_text(claims, "session_id")
            record = self.store.get_invitation(
                tenant_id=tenant_id, invitation_id=invitation_id
            )
            if (
                record is None
                or record.get("tenant_id") != tenant_id
                or record.get("invitation_id") != invitation_id
                or record.get("session_id") != session_id
                or record.get("status") != "pending"
                or int(record.get("expires_at", 0)) < int(self.now())
            ):
                return None
            return record, claims
        except (TokenError, KeyError, TypeError, ValueError):
            return None

    def _issue_csrf(
        self, record: Mapping[str, Any], session_claims: Mapping[str, Any]
    ) -> str:
        ttl = min(900, int(record["expires_at"]) - int(self.now()))
        return self.signer.issue(
            purpose="csrf",
            ttl_seconds=max(1, ttl),
            claims={
                "tenant_id": record["tenant_id"],
                "invitation_id": record["invitation_id"],
                "session_id": session_claims["session_id"],
            },
        )

    def _valid_csrf(
        self,
        token: str,
        record: Mapping[str, Any],
        session_claims: Mapping[str, Any],
    ) -> bool:
        try:
            claims = self.signer.verify(token, purpose="csrf")
        except TokenError:
            return False
        return (
            claims.get("tenant_id") == record.get("tenant_id")
            and claims.get("invitation_id") == record.get("invitation_id")
            and claims.get("session_id") == session_claims.get("session_id")
        )

    def _not_found(self) -> Response:
        return self._response(404, _message_page("Not found."))

    def _response(
        self,
        status: int,
        body: str,
        *,
        content_type: str = "text/html; charset=utf-8",
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response:
        headers = {
            "Content-Type": content_type,
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
        }
        headers.update(dict(extra_headers or {}))
        return Response(status=status, body=body, headers=headers)


def _normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if not _EMAIL_RE.fullmatch(normalized):
        raise ValueError("invalid email")
    return normalized


def _claim_text(claims: Mapping[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise TokenError("invalid token")
    return value


def _read_cookie(header: str, name: str) -> str | None:
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value
    return None


def _message_page(message: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Connection setup</title><style>{_CSS}</style></head>"
        f"<body><main><section class=\"message\"><h1>{html.escape(message)}</h1>"
        "<p>You can close this window.</p></section></main></body></html>"
    )


_CSS = """
:root{color-scheme:dark;--ink:#edf7f5;--muted:#9cb4af;--panel:#12231f;--line:#28453d;--green:#84e1bc;--amber:#f1b75b}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 85% 5%,#21473d 0,transparent 32%),#07110f;color:var(--ink);font:16px/1.55 Georgia,serif}
main{width:min(1040px,calc(100% - 32px));margin:auto;padding:64px 0 36px}header{max-width:760px;margin-bottom:34px}.kicker,.eyebrow{color:var(--green);font:700 12px/1.2 ui-monospace,monospace;letter-spacing:.15em;text-transform:uppercase}h1,h2{font-family:Georgia,serif;line-height:1.04;margin:.25em 0}h1{font-size:clamp(40px,8vw,78px);letter-spacing:-.045em}h2{font-size:26px}.intro{max-width:670px;color:var(--muted);font-size:18px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card{display:grid;gap:22px;padding:24px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(20,43,37,.95),rgba(12,27,23,.92));box-shadow:0 20px 60px rgba(0,0,0,.18)}.status{color:var(--amber);font-weight:700}.card small{display:block;color:var(--muted);overflow-wrap:anywhere}form{display:grid;gap:12px}label{display:grid;gap:6px;color:var(--muted);font-size:14px}.security-note{margin:0;color:var(--muted);font-size:13px;line-height:1.45}input{width:100%;min-height:44px;border:1px solid var(--line);border-radius:10px;background:#081713;color:var(--ink);padding:10px 12px;font:inherit}button{min-height:46px;border:0;border-radius:999px;background:var(--green);color:#06110e;padding:10px 20px;font:800 14px ui-monospace,monospace;cursor:pointer}button:disabled{cursor:not-allowed;opacity:.35}.complete{margin:28px 0;max-width:280px}footer{border-top:1px solid var(--line);padding-top:22px;color:var(--muted);font-size:13px}.message{max-width:680px;padding:30px;border:1px solid var(--line);border-radius:18px;background:var(--panel)}
@media(max-width:700px){main{width:min(100% - 24px,520px);padding-top:38px}.grid{grid-template-columns:1fr}.card{padding:20px}h1{font-size:clamp(38px,15vw,58px)}.complete{max-width:none}.complete button{width:100%}}
"""


__all__ = ["MemoryPortalStore", "PortalApp", "Request", "Response"]
