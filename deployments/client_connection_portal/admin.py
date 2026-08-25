from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

from .portal.app import PortalApp
from .portal.dynamo_store import DynamoPortalStore
from .portal.security import HmacTokenSigner


_TEST_TABLE = "tekton-client-connection-portal-test"
_TEST_SIGNING_PARAMETER = "/tekton/portal/test/signing-key"
_TEST_HOST_SUFFIX = ".lambda-url.us-west-2.on.aws"


def issue_test_invitation(
    *,
    boto3_module: Any,
    base_url: str,
    recipient_email: str,
    slots: Sequence[str],
    ttl_seconds: int,
    now: Callable[[], int | float],
    id_factory: Callable[[], str],
) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(_TEST_HOST_SUFFIX)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("invalid test base URL")
    normalized_base_url = f"https://{parsed.hostname}"

    ssm = boto3_module.client("ssm")
    response = ssm.get_parameter(
        Name=_TEST_SIGNING_PARAMETER,
        WithDecryption=True,
    )
    signing_value = response.get("Parameter", {}).get("Value")
    if not isinstance(signing_value, str) or len(signing_value.encode("utf-8")) < 32:
        raise RuntimeError("test signing key is unavailable")

    table = boto3_module.resource("dynamodb").Table(_TEST_TABLE)
    app = PortalApp(
        test_mode=True,
        store=DynamoPortalStore(table=table, now=now),
        signer=HmacTokenSigner(key=signing_value.encode("utf-8"), now=now),
        base_url=normalized_base_url,
        now=now,
        id_factory=id_factory,
        vault=None,
    )
    token = app.issue_invitation(
        tenant_id="cjs-landscape",
        recipient_email=recipient_email,
        slots=slots,
        ttl_seconds=ttl_seconds,
    )
    return f"{normalized_base_url}/s/{token}"
