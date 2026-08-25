from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Mapping
from typing import Any


class StoreConflict(RuntimeError):
    """Raised when a conditional portal-state change cannot be applied."""


class MemoryPortalStore:
    """Thread-safe portal state store used by unit tests and local QA."""

    def __init__(self, *, now: Callable[[], int | float]):
        self._now = now
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def invitation_key(tenant_id: str, invitation_id: str) -> str:
        return f"TENANT#{tenant_id}#INVITE#{invitation_id}"

    def create_invitation(self, record: Mapping[str, Any]) -> None:
        key = self.invitation_key(
            str(record["tenant_id"]), str(record["invitation_id"])
        )
        with self._lock:
            if key in self._items:
                raise StoreConflict("invitation already exists")
            item = copy.deepcopy(dict(record))
            item["pk"] = key
            self._items[key] = item

    def get_invitation(
        self, *, tenant_id: str, invitation_id: str
    ) -> dict[str, Any] | None:
        key = self.invitation_key(tenant_id, invitation_id)
        with self._lock:
            item = self._items.get(key)
            return copy.deepcopy(item) if item is not None else None

    def claim_invitation(
        self, *, tenant_id: str, invitation_id: str, session_id: str
    ) -> dict[str, Any]:
        key = self.invitation_key(tenant_id, invitation_id)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                raise StoreConflict("invitation unavailable")
            if int(item["expires_at"]) < int(self._now()):
                raise StoreConflict("invitation unavailable")
            if item.get("status") != "pending" or item.get("session_id"):
                raise StoreConflict("invitation unavailable")
            item["session_id"] = session_id
            item["claimed_at"] = int(self._now())
            return copy.deepcopy(item)

    def save_connection(
        self,
        *,
        tenant_id: str,
        invitation_id: str,
        session_id: str,
        slot_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        key = self.invitation_key(tenant_id, invitation_id)
        with self._lock:
            item = self._items.get(key)
            if (
                item is None
                or item.get("status") != "pending"
                or item.get("session_id") != session_id
                or int(item["expires_at"]) < int(self._now())
                or slot_id not in item.get("slots", [])
            ):
                raise StoreConflict("session unavailable")
            item.setdefault("connections", {})[slot_id] = copy.deepcopy(dict(metadata))
            item["updated_at"] = int(self._now())

    def complete_invitation(
        self, *, tenant_id: str, invitation_id: str, session_id: str
    ) -> None:
        key = self.invitation_key(tenant_id, invitation_id)
        with self._lock:
            item = self._items.get(key)
            if (
                item is None
                or item.get("status") != "pending"
                or item.get("session_id") != session_id
                or int(item["expires_at"]) < int(self._now())
            ):
                raise StoreConflict("session unavailable")
            required = set(item.get("slots", []))
            connected = set(item.get("connections", {}))
            if required != connected:
                raise StoreConflict("all slots must be connected")
            item["status"] = "complete"
            item["completed_at"] = int(self._now())

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._items)
