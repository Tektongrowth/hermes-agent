from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from deployments.client_connection_portal.portal.app import PortalApp, Request
from deployments.client_connection_portal.portal.security import HmacTokenSigner
from deployments.client_connection_portal.portal.store import MemoryPortalStore


@dataclass
class RecordingVault:
    oauth_writes: list[dict[str, Any]] = field(default_factory=list)
    login_writes: list[dict[str, Any]] = field(default_factory=list)

    def put_oauth(self, **payload: Any) -> None:
        self.oauth_writes.append(payload)

    def put_login(self, **payload: Any) -> None:
        self.login_writes.append(payload)


@pytest.fixture
def clock() -> dict[str, int]:
    return {"now": 1_700_000_000}


@pytest.fixture
def store(clock: dict[str, int]) -> MemoryPortalStore:
    return MemoryPortalStore(now=lambda: clock["now"])


@pytest.fixture
def vault() -> RecordingVault:
    return RecordingVault()


@pytest.fixture
def app(
    clock: dict[str, int], store: MemoryPortalStore, vault: RecordingVault
) -> PortalApp:
    counter = iter(range(1, 100))
    return PortalApp(
        test_mode=True,
        store=store,
        signer=HmacTokenSigner(
            key=b"test-signing-key-that-is-long-enough", now=lambda: clock["now"]
        ),
        base_url="https://portal-test.example.test",
        now=lambda: clock["now"],
        id_factory=lambda: f"id-{next(counter)}",
        vault=vault,
    )


def _claim(app: PortalApp, *, slots: list[str] | None = None) -> tuple[str, str]:
    token = app.issue_invitation(
        tenant_id="cjs-landscape",
        recipient_email="alyssa@example.com",
        slots=slots or ["gmail-primary"],
        ttl_seconds=900,
    )
    response = app.handle(Request(method="GET", path=f"/s/{token}"))
    assert response.status == 303
    return token, response.headers["Set-Cookie"].split(";", 1)[0]


def _csrf(body: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', body)
    assert match, body
    return match.group(1)


def _setup(app: PortalApp, cookie: str):
    return app.handle(
        Request(method="GET", path="/setup", headers={"Cookie": cookie})
    )


def test_invitation_link_can_be_claimed_only_once(app: PortalApp) -> None:
    token, cookie = _claim(app)

    replay = app.handle(Request(method="GET", path=f"/s/{token}"))
    setup = _setup(app, cookie)

    assert replay.status == 410
    assert setup.status == 200
    assert "alyssa@example.com" in setup.body


def test_setup_page_has_no_cache_third_party_scripts_or_referrer_leakage(
    app: PortalApp,
) -> None:
    _, cookie = _claim(app)

    response = _setup(app, cookie)

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "<script" not in response.body.lower()
    assert "http://" not in response.body
    assert "https://" not in response.body


def test_test_mode_connects_invited_email_with_matching_identity(app: PortalApp) -> None:
    _, cookie = _claim(app)
    csrf = _csrf(_setup(app, cookie).body)

    response = app.handle(
        Request(
            method="POST",
            path="/test/connect",
            headers={"Cookie": cookie},
            form={
                "csrf": csrf,
                "slot": "gmail-primary",
                "connected_email": "Alyssa@Example.com",
            },
        )
    )
    setup = _setup(app, cookie)

    assert response.status == 303
    assert "Connected" in setup.body
    assert "alyssa@example.com" in setup.body


def test_test_mode_rejects_wrong_identity_and_uninvited_slot(app: PortalApp) -> None:
    _, cookie = _claim(app)
    csrf = _csrf(_setup(app, cookie).body)

    wrong_identity = app.handle(
        Request(
            method="POST",
            path="/test/connect",
            headers={"Cookie": cookie},
            form={
                "csrf": csrf,
                "slot": "gmail-primary",
                "connected_email": "someone-else@example.com",
            },
        )
    )
    uninvited = app.handle(
        Request(
            method="POST",
            path="/test/connect",
            headers={"Cookie": cookie},
            form={
                "csrf": csrf,
                "slot": "microsoft-primary",
                "connected_email": "alyssa@example.com",
            },
        )
    )

    assert wrong_identity.status == 409
    assert uninvited.status == 404
    assert "Connected" not in _setup(app, cookie).body


def test_test_mode_requires_session_bound_csrf(app: PortalApp) -> None:
    _, first_cookie = _claim(app)
    _, second_cookie = _claim(app)
    first_csrf = _csrf(_setup(app, first_cookie).body)

    missing = app.handle(
        Request(
            method="POST",
            path="/test/connect",
            headers={"Cookie": first_cookie},
            form={"slot": "gmail-primary", "connected_email": "alyssa@example.com"},
        )
    )
    crossed = app.handle(
        Request(
            method="POST",
            path="/test/connect",
            headers={"Cookie": second_cookie},
            form={
                "csrf": first_csrf,
                "slot": "gmail-primary",
                "connected_email": "alyssa@example.com",
            },
        )
    )

    assert missing.status == 403
    assert crossed.status == 403


def test_test_mode_hides_real_oauth_and_credential_routes_and_never_calls_vault(
    app: PortalApp, vault: RecordingVault
) -> None:
    _, cookie = _claim(app, slots=["gmail-primary", "yeti"])
    csrf = _csrf(_setup(app, cookie).body)

    for request in (
        Request(
            method="POST",
            path="/oauth/start",
            headers={"Cookie": cookie},
            form={"csrf": csrf, "slot": "gmail-primary"},
        ),
        Request(
            method="GET",
            path="/oauth/callback/google",
            headers={"Cookie": cookie},
            query={"code": "must-not-be-processed", "state": "invalid"},
        ),
        Request(
            method="POST",
            path="/credentials/yeti",
            headers={"Cookie": cookie},
            form={"csrf": csrf, "username": "secret-user", "password": "secret-pass"},
        ),
    ):
        response = app.handle(request)
        assert response.status == 404
        assert "secret" not in response.body
        assert "must-not-be-processed" not in response.body

    assert vault.oauth_writes == []
    assert vault.login_writes == []


def test_test_mode_discards_simulated_yeti_values(
    app: PortalApp, store: MemoryPortalStore, vault: RecordingVault
) -> None:
    _, cookie = _claim(app, slots=["yeti"])
    csrf = _csrf(_setup(app, cookie).body)

    response = app.handle(
        Request(
            method="POST",
            path="/test/connect",
            headers={"Cookie": cookie},
            form={
                "csrf": csrf,
                "slot": "yeti",
                "username": "sensitive-user",
                "password": "sensitive-password",
            },
        )
    )
    serialized_state = repr(store.snapshot())

    assert response.status == 303
    assert "sensitive-user" not in response.body
    assert "sensitive-password" not in response.body
    assert "sensitive-user" not in serialized_state
    assert "sensitive-password" not in serialized_state
    assert vault.login_writes == []


def test_completed_session_persists_across_app_restart_and_locks(
    app: PortalApp,
    store: MemoryPortalStore,
    vault: RecordingVault,
    clock: dict[str, int],
) -> None:
    token, cookie = _claim(app)
    csrf = _csrf(_setup(app, cookie).body)
    connected = app.handle(
        Request(
            method="POST",
            path="/test/connect",
            headers={"Cookie": cookie},
            form={
                "csrf": csrf,
                "slot": "gmail-primary",
                "connected_email": "alyssa@example.com",
            },
        )
    )
    assert connected.status == 303
    completed = app.handle(
        Request(
            method="POST",
            path="/complete",
            headers={"Cookie": cookie},
            form={"csrf": csrf},
        )
    )

    restarted = PortalApp(
        test_mode=True,
        store=store,
        signer=HmacTokenSigner(
            key=b"test-signing-key-that-is-long-enough", now=lambda: clock["now"]
        ),
        base_url="https://portal-test.example.test",
        now=lambda: clock["now"],
        id_factory=lambda: "unused",
        vault=vault,
    )

    assert completed.status == 200
    assert _setup(restarted, cookie).status == 410
    assert restarted.handle(Request(method="GET", path=f"/s/{token}")).status == 410


def test_expired_invitation_and_session_are_rejected(
    app: PortalApp, clock: dict[str, int]
) -> None:
    token, cookie = _claim(app)
    clock["now"] += 901

    assert app.handle(Request(method="GET", path=f"/s/{token}")).status == 410
    assert _setup(app, cookie).status == 410
