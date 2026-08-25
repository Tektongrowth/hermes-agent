from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from deployments.client_connection_portal.portal.dynamo_store import DynamoPortalStore
from deployments.client_connection_portal.portal.store import StoreConflict


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_item", deepcopy(kwargs)))
        item = deepcopy(kwargs["Item"])
        if item["pk"] in self.items:
            raise ConditionalFailure()
        self.items[item["pk"]] = item
        return {}

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_item", deepcopy(kwargs)))
        item = self.items.get(kwargs["Key"]["pk"])
        return {"Item": deepcopy(item)} if item else {}

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("update_item", deepcopy(kwargs)))
        key = kwargs["Key"]["pk"]
        item = self.items.get(key)
        values = kwargs["ExpressionAttributeValues"]
        names = kwargs["ExpressionAttributeNames"]
        if item is None:
            raise ConditionalFailure()
        if "#session_id" in names and "#slot" not in names:
            if item.get("status") != "pending" or item.get("session_id"):
                raise ConditionalFailure()
            item["session_id"] = values[":session_id"]
            item["claimed_at"] = values[":now"]
        elif "#slot" in names:
            if item.get("session_id") != values[":session_id"]:
                raise ConditionalFailure()
            item.setdefault("connections", {})[names["#slot"]] = deepcopy(values[":metadata"])
            item["updated_at"] = values[":now"]
        else:
            if item.get("session_id") != values[":session_id"]:
                raise ConditionalFailure()
            if len(item.get("connections", {})) != values[":slot_count"]:
                raise ConditionalFailure()
            item["status"] = "complete"
            item["completed_at"] = values[":now"]
        return {"Attributes": deepcopy(item)}


class ConditionalFailure(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


def _record() -> dict[str, Any]:
    return {
        "tenant_id": "cjs-landscape",
        "invitation_id": "inv-1",
        "recipient_email": "alyssa@example.com",
        "slots": ["gmail-primary"],
        "expected_identities": {"gmail-primary": "alyssa@example.com"},
        "connections": {},
        "status": "pending",
        "session_id": None,
        "issued_at": 1_700_000_000,
        "expires_at": 1_700_000_900,
    }


def test_dynamo_store_persists_claims_connections_and_completion() -> None:
    table = FakeTable()
    store = DynamoPortalStore(table=table, now=lambda: 1_700_000_010)
    record = _record()

    store.create_invitation(record)
    claimed = store.claim_invitation(
        tenant_id="cjs-landscape", invitation_id="inv-1", session_id="session-1"
    )
    store.save_connection(
        tenant_id="cjs-landscape",
        invitation_id="inv-1",
        session_id="session-1",
        slot_id="gmail-primary",
        metadata={
            "status": "connected",
            "provider": "google",
            "simulated": True,
            "connected_email": "alyssa@example.com",
        },
    )
    store.complete_invitation(
        tenant_id="cjs-landscape", invitation_id="inv-1", session_id="session-1"
    )
    persisted = store.get_invitation(
        tenant_id="cjs-landscape", invitation_id="inv-1"
    )

    assert claimed["session_id"] == "session-1"
    assert persisted is not None
    assert persisted["status"] == "complete"
    assert persisted["connections"]["gmail-primary"]["simulated"] is True


def test_dynamo_store_uses_conditional_writes_and_consistent_reads() -> None:
    table = FakeTable()
    store = DynamoPortalStore(table=table, now=lambda: 1_700_000_010)
    store.create_invitation(_record())
    store.get_invitation(tenant_id="cjs-landscape", invitation_id="inv-1")
    store.claim_invitation(
        tenant_id="cjs-landscape", invitation_id="inv-1", session_id="session-1"
    )

    put_call = next(payload for name, payload in table.calls if name == "put_item")
    get_call = next(payload for name, payload in table.calls if name == "get_item")
    claim_call = [payload for name, payload in table.calls if name == "update_item"][0]

    assert put_call["ConditionExpression"] == "attribute_not_exists(pk)"
    assert "session_id" not in put_call["Item"]
    assert get_call["ConsistentRead"] is True
    assert "attribute_not_exists(#session_id)" in claim_call["ConditionExpression"]
    assert "expires_at >= :now" in claim_call["ConditionExpression"]


def test_dynamo_store_maps_conditional_failures_to_store_conflict() -> None:
    table = FakeTable()
    store = DynamoPortalStore(table=table, now=lambda: 1_700_000_010)
    store.create_invitation(_record())
    store.claim_invitation(
        tenant_id="cjs-landscape", invitation_id="inv-1", session_id="session-1"
    )

    with pytest.raises(StoreConflict, match="portal state conflict"):
        store.claim_invitation(
            tenant_id="cjs-landscape", invitation_id="inv-1", session_id="session-2"
        )
