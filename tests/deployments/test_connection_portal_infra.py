from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY = (
    ROOT
    / "deployments"
    / "client_connection_portal"
    / "infra"
    / "test-role-policy.json"
)


def test_test_role_policy_can_only_read_test_signing_key_and_mutate_test_table() -> None:
    document = json.loads(POLICY.read_text(encoding="utf-8"))
    statements = document["Statement"]
    actions = {
        action
        for statement in statements
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }
    resources = {
        resource
        for statement in statements
        for resource in (
            statement["Resource"]
            if isinstance(statement["Resource"], list)
            else [statement["Resource"]]
        )
    }

    assert actions == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "ssm:GetParameter",
    }
    assert resources == {
        "arn:aws:dynamodb:us-west-2:689556676341:table/tekton-client-connection-portal-test",
        "arn:aws:ssm:us-west-2:689556676341:parameter/tekton/portal/test/signing-key",
    }
    assert all("*" not in resource for resource in resources)
    assert not any(action.startswith("secretsmanager:") for action in actions)
    assert "ssm:PutParameter" not in actions
    assert not any(action.startswith("kms:") for action in actions)


def test_portal_runtime_source_contains_no_print_or_logging_calls() -> None:
    source_root = (
        ROOT / "deployments" / "client_connection_portal" / "portal"
    )
    for path in source_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "print(" not in source, path
        assert "logging." not in source, path
        assert "logger." not in source, path
