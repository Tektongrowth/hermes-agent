import json
from types import SimpleNamespace

import pytest

from deployments.cjs_whiteout.connectors import cjs_composio_mcp as bridge


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("CJS_COMPOSIO_TOOLKITS", "googledrive")
    monkeypatch.setenv("CJS_COMPOSIO_TOOL_PREFIXES", "GOOGLEDRIVE")
    monkeypatch.setenv("CJS_COMPOSIO_ACCOUNT", "googledrive_test-account")
    monkeypatch.setenv("CJS_COMPOSIO_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(bridge, "_composio_binary", lambda: "/safe/composio")


def test_execute_pins_account_and_never_uses_shell(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"successful":true}', stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    result = bridge.composio_execute(
        "GOOGLEDRIVE_CREATE_FOLDER",
        {"name": "Dale Petersen", "parent_id": "approved-projects"},
    )
    assert result == {"successful": True}
    assert seen["argv"][:3] == ["/safe/composio", "execute", "GOOGLEDRIVE_CREATE_FOLDER"]
    assert seen["argv"][3:5] == ["--account", "googledrive_test-account"]
    assert json.loads(seen["argv"][6]) == {
        "name": "Dale Petersen",
        "parent_id": "approved-projects",
    }
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["stdin"] is bridge.subprocess.DEVNULL


def test_execute_rejects_unapproved_toolkit():
    with pytest.raises(bridge.BridgeRequestError, match="outside the approved"):
        bridge.composio_execute("GMAIL_SEND_EMAIL", {"recipient": "x@example.com"})


def test_tool_slug_rejects_shell_metacharacters():
    with pytest.raises(bridge.BridgeRequestError, match="uppercase Composio tool slug"):
        bridge.composio_execute("GOOGLEDRIVE_FIND_FILE;rm", {})


def test_search_is_forced_to_approved_toolkits(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout='{"tools":[]}', stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    bridge.composio_search("find the top down plan", limit=5)
    assert seen["argv"] == [
        "/safe/composio",
        "search",
        "find the top down plan",
        "--toolkits",
        "googledrive",
        "--limit",
        "5",
    ]


def test_sensitive_cli_error_is_redacted(monkeypatch):
    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="token=super-secret")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    with pytest.raises(bridge.BridgeRequestError) as exc:
        bridge.composio_tool_schema("GOOGLEDRIVE_FIND_FILE")
    assert "super-secret" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_arguments_have_a_hard_size_limit():
    with pytest.raises(bridge.BridgeRequestError, match="too large"):
        bridge._bounded_arguments({"body": "x" * (bridge.MAX_ARGUMENT_CHARS + 1)})


def test_missing_account_fails_closed(monkeypatch):
    monkeypatch.delenv("CJS_COMPOSIO_ACCOUNT")
    with pytest.raises(bridge.BridgeConfigurationError, match="pinned CJS"):
        bridge._account_selector()
