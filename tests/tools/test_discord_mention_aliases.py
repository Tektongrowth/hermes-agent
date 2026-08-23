"""Regression tests for configured Discord outbound mention aliases."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.platforms.discord import adapter as discord_adapter


def test_expand_outbound_mention_alias_uses_configured_user_id():
    expand = discord_adapter._expand_outbound_mention_aliases

    result = expand(
        "{{mention:nick}} Training sync complete.",
        {"nick": "123456789012345678"},
    )

    assert result == "<@" + "123456789012345678" + "> Training sync complete."


def test_expand_outbound_mention_alias_rejects_unknown_alias():
    expand = discord_adapter._expand_outbound_mention_aliases

    with pytest.raises(ValueError, match="unknown Discord mention alias: missing"):
        expand("{{mention:missing}} Check this.", {"nick": "123456789012345678"})


def test_expand_outbound_mention_alias_rejects_invalid_user_id():
    expand = discord_adapter._expand_outbound_mention_aliases

    with pytest.raises(ValueError, match="invalid Discord user ID for mention alias: nick"):
        expand("{{mention:nick}} Check this.", {"nick": "not-a-snowflake"})


def test_standalone_send_rejects_unknown_mention_alias_without_posting():
    pconfig = SimpleNamespace(
        token="tok",
        extra={"mention_aliases": {"nick": "123456789012345678"}},
    )

    with patch("aiohttp.ClientSession") as session_cls:
        result = asyncio.run(
            discord_adapter._standalone_send(
                pconfig,
                "111222333",
                "{{mention:missing}} Check this.",
            )
        )

    assert result["error"] == "unknown Discord mention alias: missing"
    session_cls.assert_not_called()


def test_standalone_send_expands_configured_mention_alias_before_posting():
    user_id = "123456789012345678"
    pconfig = SimpleNamespace(
        token="tok",
        extra={"mention_aliases": {"nick": user_id}},
    )
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"id": "message-1"})
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=response)

    with patch("aiohttp.ClientSession", return_value=session):
        result = asyncio.run(
            discord_adapter._standalone_send(
                pconfig,
                "111222333",
                "{{mention:nick}} Training sync complete.",
            )
        )

    assert result["success"] is True
    payload = session.post.call_args.kwargs["json"]
    assert payload["content"] == "<@" + user_id + "> Training sync complete."
