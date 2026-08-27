"""Execution-time tool authorization shared by all agent dispatch paths."""

from __future__ import annotations

from typing import Any


DENIED_MESSAGE = "Tool authorization changed or no longer permits this action."


def execution_authorization_block_message(
    agent: Any,
    function_name: str,
    function_args: dict,
) -> str | None:
    """Call the current turn's authorizer and fail closed on errors.

    The callback is optional so CLI and legacy gateway sessions keep their
    existing behavior. Principal-scoped gateway sessions install one each turn.
    """
    callback = getattr(agent, "tool_authorization_callback", None)
    if callback is None:
        return None
    try:
        decision = callback(function_name, function_args)
    except Exception:
        return DENIED_MESSAGE
    if decision is True:
        return None
    if isinstance(decision, str) and decision.strip():
        return decision.strip()
    return DENIED_MESSAGE
