from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .portal.app import PortalApp, Response
from .portal.dynamo_store import DynamoPortalStore
from .portal.lambda_adapter import (
    LambdaRequestError,
    request_from_event,
    response_to_event,
)
from .portal.security import HmacTokenSigner


_TEST_TABLE = "tekton-client-connection-portal-test"
_TEST_SIGNING_PARAMETER = "/tekton/portal/test/signing-key"


def create_app_from_env(
    *,
    env: Mapping[str, str],
    boto3_module: Any,
    now: Callable[[], int | float],
    id_factory: Callable[[], str],
) -> PortalApp:
    if env.get("TEST_MODE", "").strip().casefold() != "true":
        raise RuntimeError("production mode is not enabled in this build")

    table_name = env.get("TABLE_NAME", "")
    signing_parameter = env.get("SIGNING_KEY_PARAMETER", "")
    base_url = env.get("BASE_URL", "").rstrip("/")
    if table_name != _TEST_TABLE:
        raise RuntimeError("test table configuration is invalid")
    if signing_parameter != _TEST_SIGNING_PARAMETER:
        raise RuntimeError("test signing-key configuration is invalid")
    if not base_url.startswith("https://"):
        raise RuntimeError("test base URL configuration is invalid")

    ssm = boto3_module.client("ssm")
    response = ssm.get_parameter(Name=signing_parameter, WithDecryption=True)
    signing_value = response.get("Parameter", {}).get("Value")
    if not isinstance(signing_value, str) or len(signing_value.encode("utf-8")) < 32:
        raise RuntimeError("test signing key is unavailable")

    table = boto3_module.resource("dynamodb").Table(table_name)
    store = DynamoPortalStore(table=table, now=now)
    return PortalApp(
        test_mode=True,
        store=store,
        signer=HmacTokenSigner(key=signing_value.encode("utf-8"), now=now),
        base_url=base_url,
        now=now,
        id_factory=id_factory,
        vault=None,
    )


def make_lambda_handler(app: PortalApp):
    def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
        del context
        try:
            request = request_from_event(event)
        except LambdaRequestError:
            return response_to_event(
                Response(
                    status=400,
                    body="invalid request",
                    headers={
                        "Content-Type": "text/plain; charset=utf-8",
                        "Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer",
                        "X-Content-Type-Options": "nosniff",
                        "X-Frame-Options": "DENY",
                    },
                )
            )
        return response_to_event(app.handle(request))

    return handler


_APP: PortalApp | None = None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _APP
    if _APP is None:
        import boto3

        _APP = create_app_from_env(
            env=os.environ,
            boto3_module=boto3,
            now=time.time,
            id_factory=lambda: uuid.uuid4().hex,
        )
    return make_lambda_handler(_APP)(event, context)
