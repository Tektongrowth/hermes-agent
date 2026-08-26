from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class RegistryError(LookupError):
    """Raised when a tenant or connector slot is not predeclared."""


class ProviderKind(str, Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    INTUIT = "intuit"
    YETI = "yeti"


@dataclass(frozen=True, slots=True)
class ConnectorSlot:
    slot_id: str
    label: str
    provider: ProviderKind
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    identity_endpoint: str | None = None
    scopes: tuple[str, ...] = ()
    oauth_parameter: str | None = None
    secret_name: str | None = None
    credential_fields: tuple[str, ...] = ()
    target_mailbox: str | None = None

    @property
    def uses_oauth(self) -> bool:
        return self.authorization_endpoint is not None


@dataclass(frozen=True, slots=True)
class TenantConfig:
    tenant_id: str
    display_name: str
    slots: Mapping[str, ConnectorSlot]

    def get_slot(self, slot_id: str) -> ConnectorSlot:
        try:
            return self.slots[slot_id]
        except KeyError as exc:
            raise RegistryError("unknown connector slot") from exc


_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_IDENTITY = "https://openidconnect.googleapis.com/v1/userinfo"
_MICROSOFT_AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_MICROSOFT_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_MICROSOFT_IDENTITY = "https://graph.microsoft.com/v1.0/me"
_INTUIT_AUTH = "https://appcenter.intuit.com/connect/oauth2"
_INTUIT_TOKEN = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


def _oauth_path(slot_id: str) -> str:
    return f"/tekton/clients/cjs-landscape/runtime/oauth/{slot_id}"


_CJS_SLOTS: Mapping[str, ConnectorSlot] = MappingProxyType(
    {
        "google-drive": ConnectorSlot(
            slot_id="google-drive",
            label="Google Drive",
            provider=ProviderKind.GOOGLE,
            authorization_endpoint=_GOOGLE_AUTH,
            token_endpoint=_GOOGLE_TOKEN,
            identity_endpoint=_GOOGLE_IDENTITY,
            scopes=(
                "openid",
                "email",
                "profile",
                "https://www.googleapis.com/auth/drive.readonly",
            ),
            oauth_parameter=_oauth_path("google-drive"),
        ),
        "gmail-primary": ConnectorSlot(
            slot_id="gmail-primary",
            label="Google Workspace email",
            provider=ProviderKind.GOOGLE,
            authorization_endpoint=_GOOGLE_AUTH,
            token_endpoint=_GOOGLE_TOKEN,
            identity_endpoint=_GOOGLE_IDENTITY,
            scopes=(
                "openid",
                "email",
                "profile",
                "https://www.googleapis.com/auth/gmail.readonly",
            ),
            oauth_parameter=_oauth_path("gmail-primary"),
        ),
        "microsoft-primary": ConnectorSlot(
            slot_id="microsoft-primary",
            label="Outlook / Microsoft 365 email",
            provider=ProviderKind.MICROSOFT,
            authorization_endpoint=_MICROSOFT_AUTH,
            token_endpoint=_MICROSOFT_TOKEN,
            identity_endpoint=_MICROSOFT_IDENTITY,
            scopes=(
                "openid",
                "profile",
                "email",
                "offline_access",
                "User.Read",
                "Mail.Read",
            ),
            oauth_parameter=_oauth_path("microsoft-primary"),
        ),
        "quickbooks-cjs": ConnectorSlot(
            slot_id="quickbooks-cjs",
            label="CJS QuickBooks",
            provider=ProviderKind.INTUIT,
            authorization_endpoint=_INTUIT_AUTH,
            token_endpoint=_INTUIT_TOKEN,
            scopes=("com.intuit.quickbooks.accounting",),
            oauth_parameter=_oauth_path("quickbooks-cjs"),
        ),
        "quickbooks-whiteout": ConnectorSlot(
            slot_id="quickbooks-whiteout",
            label="Whiteout QuickBooks",
            provider=ProviderKind.INTUIT,
            authorization_endpoint=_INTUIT_AUTH,
            token_endpoint=_INTUIT_TOKEN,
            scopes=("com.intuit.quickbooks.accounting",),
            oauth_parameter=_oauth_path("quickbooks-whiteout"),
        ),
        "yeti": ConnectorSlot(
            slot_id="yeti",
            label="Yeti",
            provider=ProviderKind.YETI,
            secret_name="tekton/clients/cjs-landscape/logins/yeti",
            credential_fields=("username", "password"),
        ),
    }
)

_TENANTS: Mapping[str, TenantConfig] = MappingProxyType(
    {
        "cjs-landscape": TenantConfig(
            tenant_id="cjs-landscape",
            display_name="CJS Landscape",
            slots=_CJS_SLOTS,
        )
    }
)


def get_tenant(tenant_id: str) -> TenantConfig:
    try:
        return _TENANTS[tenant_id]
    except KeyError as exc:
        raise RegistryError("unknown tenant") from exc
