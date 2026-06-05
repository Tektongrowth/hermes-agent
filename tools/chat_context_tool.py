"""Read recent messaging-platform chat context captured by the gateway."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from tools.registry import registry


def _platform_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _format_time(epoch: Any) -> str:
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def chat_context(
    limit: int = 50,
    platform: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    include_bots: bool = True,
    db=None,
    current_platform: str | None = None,
    current_chat_id: str | None = None,
    current_thread_id: str | None = None,
) -> str:
    """Fetch recent captured messages for the current or explicit chat.

    This only returns messages the Hermes gateway has observed and stored. It
    cannot fetch arbitrary Telegram/Discord history from the provider.
    """
    explicit_platform = bool(platform)
    explicit_chat = bool(chat_id)
    if explicit_platform != explicit_chat:
        return json.dumps({
            "success": False,
            "error": "Provide both platform and chat_id, or omit both to use the current messaging chat.",
        })
    if not platform and not chat_id:
        platform = current_platform
        chat_id = current_chat_id
        if thread_id is None:
            thread_id = current_thread_id
    if not platform or not chat_id:
        return json.dumps({
            "success": False,
            "error": "No current messaging chat is available. Pass platform and chat_id explicitly.",
        })
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 50
    try:
        if db is None:
            from hermes_state import SessionDB
            db = SessionDB()
        rows = db.get_recent_platform_messages(
            platform=str(platform),
            chat_id=str(chat_id),
            thread_id=str(thread_id) if thread_id not in (None, "") else None,
            limit=limit,
            include_bots=bool(include_bots),
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})

    messages = []
    for row in rows:
        messages.append({
            "time": _format_time(row.get("timestamp")),
            "sender": row.get("sender") or row.get("user_name") or row.get("user_id") or "unknown",
            "text": row.get("text") or "",
            "message_id": row.get("message_id"),
            "message_type": row.get("message_type"),
            "reply_to_message_id": row.get("reply_to_message_id"),
        })
    return json.dumps({
        "success": True,
        "captured_only": True,
        "scope": {
            "platform": str(platform),
            "chat_id": str(chat_id),
            "thread_id": str(thread_id) if thread_id not in (None, "") else None,
        },
        "count": len(messages),
        "messages": messages,
    })


CHAT_CONTEXT_SCHEMA = {
    "name": "chat_context",
    "description": (
        "Fetch recent messages captured by the Hermes messaging gateway for "
        "the current chat or an explicitly specified chat. Read-only. Use this "
        "when the user references recent messages in the same Telegram/Discord "
        "chat that are not visible in the current model context. Returns only "
        "captured gateway history, not arbitrary provider backlog."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum captured messages to return, 1-200.",
                "default": 50,
                "minimum": 1,
                "maximum": 200,
            },
            "platform": {
                "type": "string",
                "description": "Optional explicit platform, e.g. telegram. Must be paired with chat_id.",
            },
            "chat_id": {
                "type": "string",
                "description": "Optional explicit chat ID. Defaults to current messaging chat when available.",
            },
            "thread_id": {
                "type": "string",
                "description": "Optional thread/topic ID. Defaults to the current thread when available.",
            },
            "include_bots": {
                "type": "boolean",
                "description": "Whether to include bot-authored captured messages.",
                "default": True,
            },
        },
    },
}


registry.register(
    name="chat_context",
    toolset="session_search",
    schema=CHAT_CONTEXT_SCHEMA,
    handler=lambda args, **kw: chat_context(
        limit=args.get("limit", 50),
        platform=args.get("platform"),
        chat_id=args.get("chat_id"),
        thread_id=args.get("thread_id"),
        include_bots=args.get("include_bots", True),
    ),
)
