from __future__ import annotations

from typing import Any

import pytest

from deployments.client_connection_portal.lambda_function import (
    create_app_from_env,
    make_lambda_handler,
)


class FakeParameterClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"Parameter": {"Value": "s" * 64}}


class FakeDynamoResource:
    def __init__(self) -> None:
        self.table_names: list[str] = []

    def Table(self, name: str):
        self.table_names.append(name)
        return object()


class FakeBoto3:
    def __init__(self) -> None:
        self.ssm = FakeParameterClient()
        self.dynamo = FakeDynamoResource()
        self.client_names: list[str] = []
        self.resource_names: list[str] = []

    def client(self, name: str):
        self.client_names.append(name)
        if name != "ssm":
            raise AssertionError(f"unexpected client in test mode: {name}")
        return self.ssm

    def resource(self, name: str):
        self.resource_names.append(name)
        if name != "dynamodb":
            raise AssertionError(f"unexpected resource in test mode: {name}")
        return self.dynamo


def _env() -> dict[str, str]:
    return {
        "TEST_MODE": "true",
        "TABLE_NAME": "tekton-client-connection-portal-test",
        "SIGNING_KEY_PARAMETER": "/tekton/portal/test/signing-key",
        "BASE_URL": "https://portal-test.example.test",
    }


def test_test_mode_bootstrap_reads_only_signing_key_and_test_table() -> None:
    boto3 = FakeBoto3()

    app = create_app_from_env(
        env=_env(), boto3_module=boto3, now=lambda: 1_700_000_000, id_factory=lambda: "id-1"
    )

    assert app.test_mode is True
    assert boto3.client_names == ["ssm"]
    assert boto3.resource_names == ["dynamodb"]
    assert boto3.ssm.calls == [
        {"Name": "/tekton/portal/test/signing-key", "WithDecryption": True}
    ]
    assert boto3.dynamo.table_names == ["tekton-client-connection-portal-test"]
    assert app.vault is None


def test_bootstrap_fails_closed_for_production_mode() -> None:
    env = _env()
    env["TEST_MODE"] = "false"

    with pytest.raises(RuntimeError, match="production mode is not enabled"):
        create_app_from_env(
            env=env,
            boto3_module=FakeBoto3(),
            now=lambda: 1_700_000_000,
            id_factory=lambda: "id-1",
        )


def test_lambda_handler_returns_health_without_exposing_internal_state() -> None:
    app = create_app_from_env(
        env=_env(),
        boto3_module=FakeBoto3(),
        now=lambda: 1_700_000_000,
        id_factory=lambda: "id-1",
    )
    handler = make_lambda_handler(app)

    result = handler(
        {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/health",
            "headers": {},
        },
        None,
    )

    assert result["statusCode"] == 200
    assert result["body"] == "ok"
    assert "signing" not in repr(result).lower()
    assert "table" not in repr(result).lower()


def test_lambda_handler_rejects_malformed_body_with_fixed_error() -> None:
    app = create_app_from_env(
        env=_env(),
        boto3_module=FakeBoto3(),
        now=lambda: 1_700_000_000,
        id_factory=lambda: "id-1",
    )
    handler = make_lambda_handler(app)

    result = handler(
        {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/test/connect",
            "headers": {"content-type": "application/json"},
            "body": '{"password":"sensitive-password"}',
        },
        None,
    )

    assert result["statusCode"] == 400
    assert result["body"] == "invalid request"
    assert "sensitive-password" not in repr(result)
