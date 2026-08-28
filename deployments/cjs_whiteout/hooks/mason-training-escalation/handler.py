from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRIGGERS = (
    "i couldn't complete that with mason's current tools",
    "i couldn't pull the",
    "i couldn't access the",
    "i can't access the",
    "i was unable to",
)

def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))

def _safe(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]

def _fingerprint(request: str, response: str) -> str:
    return hashlib.sha256(f"{request}\n{response}".encode()).hexdigest()[:16]

def _append_record(record: dict[str, Any]) -> None:
    state = _home() / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "mason-training-requests.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")

def _seen(fingerprint: str) -> bool:
    path = _home() / "state" / "mason-training-alerts.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    if fingerprint in data:
        return True
    data[fingerprint] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return False

def _send_alert(record: dict[str, Any]) -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel = os.environ.get("MASON_TRAINING_ALERT_CHANNEL", "").strip()
    if not token or not channel:
        return
    nick = os.environ.get("MASON_TRAINING_NICK_ID", "1412944834372829245")
    clawton = os.environ.get("MASON_TRAINING_CLAWTON_ID", "1510101675396825269")
    content = (
        f"<@{nick}> <@{clawton}> Mason training request [{record['id']}]\n"
        f"Status: New\n"
        f"Alyssa asked: {record['request']}\n"
        f"Mason reported: {record['response']}\n"
        "Boundary: Train or repair Mason. Do not complete Alyssa's business task in Mason's place."
    )[:1950]
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages",
        data=json.dumps({"content": content, "allowed_mentions": {"users": [nick, clawton]}}).encode(),
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json", "User-Agent": "MasonTrainingHook/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f"Discord alert failed with status {response.status}")

def handle(event_type: str, context: dict[str, Any]) -> None:
    if event_type != "agent:end" or str(context.get("platform", "")).casefold() != "discord":
        return
    response = _safe(context.get("response"))
    if not any(trigger in response.casefold() for trigger in TRIGGERS):
        return
    request = _safe(context.get("message"))
    fingerprint = _fingerprint(request, response)
    if _seen(fingerprint):
        return
    record = {
        "id": fingerprint,
        "status": "new",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "platform": "discord",
        "channel_id": _safe(context.get("chat_id"), 64),
        "user_id": _safe(context.get("user_id"), 64),
        "session_id": _safe(context.get("session_id"), 128),
        "request": request,
        "response": response,
        "instruction": "Train or repair Mason; do not execute the underlying business task.",
    }
    _append_record(record)
    _send_alert(record)
