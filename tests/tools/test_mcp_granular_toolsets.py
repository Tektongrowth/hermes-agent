from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.mcp_tool import MCPServerTask, _register_server_tools
from tools.registry import ToolRegistry


def _tool(name: str):
    return SimpleNamespace(name=name, description=f"Read {name}", inputSchema=None)


def _server(*names: str) -> MCPServerTask:
    server = MCPServerTask("cjs-synkedup")
    server._tools = [_tool(name) for name in names]
    server.session = MagicMock()
    return server


def _config(mapping):
    return {
        "tools": {
            "resources": True,
            "prompts": True,
            "toolsets": mapping,
        }
    }


def test_granular_mapping_assigns_each_tool_and_registers_no_all_tools_alias():
    registry = ToolRegistry()
    server = _server("jobs", "job_costing", "customers")
    config = _config(
        {
            "synkedup-operations-read": ["jobs"],
            "synkedup-financial-read": ["job_costing"],
            "synkedup-sales-read": ["customers"],
        }
    )

    with patch("tools.registry.registry", registry):
        registered = _register_server_tools("cjs-synkedup", server, config)

    assert set(registered) == {
        "mcp_cjs_synkedup_jobs",
        "mcp_cjs_synkedup_job_costing",
        "mcp_cjs_synkedup_customers",
    }
    assert registry.get_toolset_for_tool("mcp_cjs_synkedup_jobs") == "synkedup-operations-read"
    assert registry.get_toolset_for_tool("mcp_cjs_synkedup_job_costing") == "synkedup-financial-read"
    assert registry.get_toolset_for_tool("mcp_cjs_synkedup_customers") == "synkedup-sales-read"
    assert registry.get_toolset_alias_target("cjs-synkedup") is None
    assert not any("resource" in name or "prompt" in name for name in registered)


@pytest.mark.parametrize(
    "mapping",
    [
        {"synkedup-operations-read": ["jobs"]},
        {
            "synkedup-operations-read": ["jobs", "customers"],
            "synkedup-sales-read": ["customers"],
        },
        {
            "synkedup-operations-read": ["jobs"],
            "synkedup-sales-read": ["customers", "missing_tool"],
        },
        {"web": ["jobs", "customers"]},
        {"mcp-cjs": ["jobs", "customers"]},
        {},
        [],
    ],
)
def test_invalid_granular_mapping_fails_closed(mapping):
    registry = ToolRegistry()
    server = _server("jobs", "customers")

    with patch("tools.registry.registry", registry):
        registered = _register_server_tools("cjs-synkedup", server, _config(mapping))

    assert registered == []
    assert registry.get_all_tool_names() == []
    assert registry.get_toolset_alias_target("cjs-synkedup") is None


def test_granular_mapping_is_checked_after_include_filter():
    registry = ToolRegistry()
    server = _server("jobs", "customers")
    config = _config({"synkedup-operations-read": ["jobs", "customers"]})
    config["tools"]["include"] = ["jobs"]

    with patch("tools.registry.registry", registry):
        registered = _register_server_tools("cjs-synkedup", server, config)

    assert registered == []


def test_legacy_mcp_registration_keeps_single_server_toolset_and_alias():
    registry = ToolRegistry()
    server = _server("jobs", "customers")

    with patch("tools.registry.registry", registry):
        registered = _register_server_tools(
            "cjs-synkedup",
            server,
            {"tools": {"resources": False, "prompts": False}},
        )

    assert set(registered) == {"mcp_cjs_synkedup_jobs", "mcp_cjs_synkedup_customers"}
    assert registry.get_toolset_for_tool("mcp_cjs_synkedup_jobs") == "mcp-cjs-synkedup"
    assert registry.get_toolset_alias_target("cjs-synkedup") == "mcp-cjs-synkedup"
