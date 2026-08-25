from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any, NoReturn

from .store import StoreConflict


class DynamoPortalStore:
    """DynamoDB-backed invitation state with conditional one-time mutations."""

    def __init__(self, *, table: Any, now: Callable[[], int | float]):
        self.table = table
        self.now = now

    @staticmethod
    def invitation_key(tenant_id: str, invitation_id: str) -> str:
        return f"TENANT#{tenant_id}#INVITE#{invitation_id}"

    def create_invitation(self, record: Mapping[str, Any]) -> None:
        item = copy.deepcopy(dict(record))
        item["pk"] = self.invitation_key(
            str(record["tenant_id"]), str(record["invitation_id"])
        )
        if item.get("session_id") is None:
            item.pop("session_id", None)
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except Exception as exc:
            self._raise_conflict_or_original(exc)

    def get_invitation(
        self, *, tenant_id: str, invitation_id: str
    ) -> dict[str, Any] | None:
        response = self.table.get_item(
            Key={"pk": self.invitation_key(tenant_id, invitation_id)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return copy.deepcopy(item) if isinstance(item, dict) else None

    def claim_invitation(
        self, *, tenant_id: str, invitation_id: str, session_id: str
    ) -> dict[str, Any]:
        now = int(self.now())
        try:
            response = self.table.update_item(
                Key={"pk": self.invitation_key(tenant_id, invitation_id)},
                UpdateExpression="SET #session_id = :session_id, claimed_at = :now",
                ConditionExpression=(
                    "#status = :pending AND attribute_not_exists(#session_id) "
                    "AND expires_at >= :now"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                    "#session_id": "session_id",
                },
                ExpressionAttributeValues={
                    ":pending": "pending",
                    ":session_id": session_id,
                    ":now": now,
                },
                ReturnValues="ALL_NEW",
            )
        except Exception as exc:
            self._raise_conflict_or_original(exc)
        return copy.deepcopy(response["Attributes"])

    def save_connection(
        self,
        *,
        tenant_id: str,
        invitation_id: str,
        session_id: str,
        slot_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        now = int(self.now())
        try:
            self.table.update_item(
                Key={"pk": self.invitation_key(tenant_id, invitation_id)},
                UpdateExpression=(
                    "SET connections.#slot = :metadata, updated_at = :now"
                ),
                ConditionExpression=(
                    "#status = :pending AND session_id = :session_id "
                    "AND expires_at >= :now AND contains(slots, :slot_id)"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                    "#slot": slot_id,
                },
                ExpressionAttributeValues={
                    ":pending": "pending",
                    ":session_id": session_id,
                    ":now": now,
                    ":slot_id": slot_id,
                    ":metadata": copy.deepcopy(dict(metadata)),
                },
            )
        except Exception as exc:
            self._raise_conflict_or_original(exc)

    def complete_invitation(
        self, *, tenant_id: str, invitation_id: str, session_id: str
    ) -> None:
        current = self.get_invitation(
            tenant_id=tenant_id, invitation_id=invitation_id
        )
        if current is None:
            raise StoreConflict("portal state conflict")
        slot_count = len(current.get("slots", []))
        now = int(self.now())
        try:
            self.table.update_item(
                Key={"pk": self.invitation_key(tenant_id, invitation_id)},
                UpdateExpression="SET #status = :complete, completed_at = :now",
                ConditionExpression=(
                    "#status = :pending AND session_id = :session_id "
                    "AND expires_at >= :now AND size(connections) = :slot_count"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": "pending",
                    ":complete": "complete",
                    ":session_id": session_id,
                    ":now": now,
                    ":slot_count": slot_count,
                },
                ReturnValues="ALL_NEW",
            )
        except Exception as exc:
            self._raise_conflict_or_original(exc)

    @staticmethod
    def _raise_conflict_or_original(exc: Exception) -> NoReturn:
        response = getattr(exc, "response", {})
        code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        if code == "ConditionalCheckFailedException":
            raise StoreConflict("portal state conflict") from exc
        raise exc
