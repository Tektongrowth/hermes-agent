from __future__ import annotations

import pytest

from deployments.client_connection_portal.portal.registry import (
    ProviderKind,
    RegistryError,
    get_tenant,
)


def test_cjs_registry_exposes_only_predeclared_connector_slots() -> None:
    tenant = get_tenant("cjs-landscape")

    assert set(tenant.slots) == {
        "google-drive",
        "gmail-primary",
        "microsoft-primary",
        "quickbooks-cjs",
        "quickbooks-whiteout",
        "yeti",
    }
    assert tenant.slots["gmail-primary"].provider is ProviderKind.GOOGLE
    assert tenant.slots["microsoft-primary"].provider is ProviderKind.MICROSOFT
    assert tenant.slots["yeti"].provider is ProviderKind.YETI


def test_registry_rejects_unknown_tenants_and_slots() -> None:
    tenant = get_tenant("cjs-landscape")

    with pytest.raises(RegistryError, match="unknown tenant"):
        get_tenant("another-client")

    with pytest.raises(RegistryError, match="unknown connector slot"):
        tenant.get_slot("../../another-client")


def test_email_oauth_policies_use_official_hosts_and_read_only_scopes() -> None:
    tenant = get_tenant("cjs-landscape")
    gmail = tenant.get_slot("gmail-primary")
    microsoft = tenant.get_slot("microsoft-primary")

    assert gmail.authorization_endpoint == "https://accounts.google.com/o/oauth2/v2/auth"
    assert "https://www.googleapis.com/auth/gmail.readonly" in gmail.scopes
    assert all("send" not in scope.lower() for scope in gmail.scopes)
    assert all("modify" not in scope.lower() for scope in gmail.scopes)

    assert microsoft.authorization_endpoint == (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )
    assert "Mail.Read" in microsoft.scopes
    assert "Mail.Send" not in microsoft.scopes
    assert all("write" not in scope.lower() for scope in microsoft.scopes)


def test_storage_destinations_are_server_side_and_cjs_scoped() -> None:
    tenant = get_tenant("cjs-landscape")

    assert tenant.get_slot("gmail-primary").oauth_parameter == (
        "/tekton/clients/cjs-landscape/runtime/oauth/gmail-primary"
    )
    assert tenant.get_slot("microsoft-primary").oauth_parameter == (
        "/tekton/clients/cjs-landscape/runtime/oauth/microsoft-primary"
    )
    assert tenant.get_slot("yeti").secret_name == (
        "tekton/clients/cjs-landscape/logins/yeti"
    )

    for slot in tenant.slots.values():
        destinations = [slot.oauth_parameter, slot.secret_name]
        for destination in filter(None, destinations):
            assert "*" not in destination
            assert ".." not in destination
            assert "cjs-landscape" in destination


def test_password_intake_is_not_available_for_google_or_microsoft() -> None:
    tenant = get_tenant("cjs-landscape")

    assert tenant.get_slot("gmail-primary").credential_fields == ()
    assert tenant.get_slot("microsoft-primary").credential_fields == ()
    assert tenant.get_slot("yeti").credential_fields == ("username", "password")
