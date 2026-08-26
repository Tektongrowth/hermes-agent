from __future__ import annotations

from typing import Any

from deployments.client_connection_portal.admin import issue_test_invitation


class FakeTable:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        self.items.append(kwargs["Item"])
        return {}


class FakeBoto3:
    def __init__(self) -> None:
        self.table = FakeTable()
        self.clients: list[str] = []
        self.resources: list[str] = []

    def client(self, name: str):
        self.clients.append(name)
        assert name == "ssm"
        return self

    def resource(self, name: str):
        self.resources.append(name)
        assert name == "dynamodb"
        return self

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "Name": "/tekton/portal/test/signing-key",
            "WithDecryption": True,
        }
        return {"Parameter": {"Value": "k" * 64}}

    def Table(self, name: str) -> FakeTable:
        assert name == "tekton-client-connection-portal-test"
        return self.table


def test_admin_issuer_creates_cjs_only_short_lived_test_link() -> None:
    boto3 = FakeBoto3()

    result = issue_test_invitation(
        boto3_module=boto3,
        base_url="https://abc.lambda-url.us-west-2.on.aws",
        recipient_email="nick@example.com",
        slots=["gmail-primary", "microsoft-primary", "yeti"],
        ttl_seconds=900,
        now=lambda: 1_700_000_000,
        id_factory=lambda: "inv-1",
    )

    assert result.startswith("https://abc.lambda-url.us-west-2.on.aws/s/")
    assert "nick@example.com" not in result
    assert boto3.clients == ["ssm"]
    assert boto3.resources == ["dynamodb"]
    assert len(boto3.table.items) == 1
    item = boto3.table.items[0]
    assert item["tenant_id"] == "cjs-landscape"
    assert item["recipient_email"] == "nick@example.com"
    assert item["expires_at"] == 1_700_000_900
    assert item["slots"] == ["gmail-primary", "microsoft-primary", "yeti"]
    assert "session_id" not in item


def test_admin_issuer_rejects_non_test_or_non_tls_base_urls() -> None:
    for base_url in (
        "http://abc.lambda-url.us-west-2.on.aws",
        "https://portal.example.com",
        "https://abc.lambda-url.us-east-1.on.aws",
    ):
        try:
            issue_test_invitation(
                boto3_module=FakeBoto3(),
                base_url=base_url,
                recipient_email="nick@example.com",
                slots=["gmail-primary"],
                ttl_seconds=900,
                now=lambda: 1_700_000_000,
                id_factory=lambda: "inv-1",
            )
        except ValueError as exc:
            assert str(exc) == "invalid test base URL"
        else:
            raise AssertionError(f"unsafe test base URL accepted: {base_url}")


def test_admin_issuer_binds_outlook_mailbox_identity_separately_from_recipient() -> None:
    boto3 = FakeBoto3()

    issue_test_invitation(
        boto3_module=boto3,
        base_url="https://abc.lambda-url.us-west-2.on.aws",
        recipient_email="nick@example.com",
        slots=["microsoft-primary"],
        expected_identities={"microsoft-primary": "info@cjslandscape.com"},
        ttl_seconds=900,
        now=lambda: 1_700_000_000,
        id_factory=lambda: "inv-1",
    )

    item = boto3.table.items[0]
    assert item["recipient_email"] == "nick@example.com"
    assert item["expected_identities"] == {
        "microsoft-primary": "info@cjslandscape.com"
    }
