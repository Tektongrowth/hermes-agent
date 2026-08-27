from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from run_agent import AIAgent


def _agent(callback):
    agent = AIAgent.__new__(AIAgent)
    agent.tool_authorization_callback = callback
    agent.session_id = "session"
    agent._current_turn_id = "turn"
    agent._current_api_request_id = "request"
    agent.valid_tool_names = {"mcp_cjs_synkedup_jobs"}
    agent.enabled_toolsets = ["synkedup-operations-read"]
    agent.disabled_toolsets = []
    agent._memory_manager = None
    return agent


def test_concurrent_dispatch_bridge_blocks_when_execution_authorizer_revokes():
    agent = _agent(lambda name, args: False)
    with patch("run_agent.handle_function_call", side_effect=AssertionError("must not execute")):
        result = agent._invoke_tool("mcp_cjs_synkedup_jobs", {}, "task")
    assert json.loads(result) == {
        "error": "Tool authorization changed or no longer permits this action."
    }


def test_execution_authorizer_exception_fails_closed():
    def broken(name, args):
        raise RuntimeError("config reload failed")

    agent = _agent(broken)
    with patch("run_agent.handle_function_call", side_effect=AssertionError("must not execute")):
        result = agent._invoke_tool("mcp_cjs_synkedup_jobs", {}, "task")
    assert "authorization changed" in json.loads(result)["error"]
    assert "config reload failed" not in result


def test_authorized_tool_reaches_the_normal_dispatcher():
    agent = _agent(lambda name, args: True)
    with patch("run_agent.handle_function_call", return_value="ok") as dispatch:
        result = agent._invoke_tool("mcp_cjs_synkedup_jobs", {"query": "MS-044"}, "task")
    assert result == "ok"
    dispatch.assert_called_once()


def test_execution_authorizer_can_return_a_specific_block_message():
    agent = _agent(lambda name, args: "BLOCKED: Administrator denied this action.")
    with patch("run_agent.handle_function_call", side_effect=AssertionError("must not execute")):
        result = agent._invoke_tool("mcp_cjs_synkedup_jobs", {}, "task")
    assert json.loads(result) == {"error": "BLOCKED: Administrator denied this action."}


def test_sequential_dispatch_path_contains_the_same_execution_gate():
    source = Path("agent/tool_executor.py").read_text(encoding="utf-8")
    assert "execution_authorization_block_message" in source
    assert source.index("execution_authorization_block_message") < source.index(
        "elif function_name == \"todo\""
    )
