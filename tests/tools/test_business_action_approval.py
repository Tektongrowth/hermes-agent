import tools.approval as approval


def _run_with_choice(monkeypatch, choice):
    session_key = "discord-action-session"
    token = approval.set_current_session_key(session_key)
    seen = []

    def notify(data):
        seen.append(data)
        approval.resolve_gateway_approval(session_key, choice)

    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: True)
    approval.register_gateway_notify(session_key, notify)
    try:
        result = approval.request_gateway_action_approval(
            "Tool: mcp_composio_execute",
            "A user requested an irreversible action.",
        )
    finally:
        approval.unregister_gateway_notify(session_key)
        approval.reset_current_session_key(token)
    return result, seen


def test_gateway_action_approval_accepts_confirm_once(monkeypatch):
    result, seen = _run_with_choice(monkeypatch, "once")
    assert result is True
    assert seen[0]["approval_mode"] == "confirm_only"
    assert seen[0]["approval_kind"] == "business_action"


def test_gateway_action_approval_rejects_session_grant(monkeypatch):
    result, _ = _run_with_choice(monkeypatch, "session")
    assert result is False


def test_gateway_action_approval_rejects_permanent_grant(monkeypatch):
    result, _ = _run_with_choice(monkeypatch, "always")
    assert result is False


def test_gateway_action_approval_rejects_deny(monkeypatch):
    result, _ = _run_with_choice(monkeypatch, "deny")
    assert result is False


def test_gateway_action_approval_fails_closed_without_notify(monkeypatch):
    token = approval.set_current_session_key("missing-notify")
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: True)
    try:
        assert not approval.request_gateway_action_approval("Tool: delete", "destructive")
    finally:
        approval.reset_current_session_key(token)
