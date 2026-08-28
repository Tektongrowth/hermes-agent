from __future__ import annotations

import os
from pathlib import Path

import yaml

from deployments.cjs_whiteout.connectors import cjs_synkedup_mcp as connector


ROOT = Path("deployments/cjs_whiteout")
SYSTEMD = ROOT / "systemd"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_catalog_config_covers_every_connector_tool_once():
    config = yaml.safe_load(_read(ROOT / "config" / "mason-config.example.yaml"))
    server = config["mcp_servers"]["cjs-synkedup"]
    toolsets = server["tools"]["toolsets"]
    configured = [name for names in toolsets.values() for name in names]
    assert set(configured) == set(connector.ALL_TOOL_NAMES)
    assert len(configured) == len(set(configured))
    assert set(toolsets) == {
        "synkedup-reference-read",
        "synkedup-operations-read",
        "synkedup-sales-read",
        "synkedup-financial-read",
    }
    assert server["timeout"] >= 300


def test_alyssa_has_complete_admin_read_profile_and_defaults_deny():
    config = yaml.safe_load(_read(ROOT / "config" / "mason-config.example.yaml"))
    policy = config["platform_principal_toolsets"]["discord"]
    alyssa = policy["users"]["1541580058152665199"]
    assert policy["default"] == []
    assert set(alyssa) == {
        "synkedup-reference-read",
        "synkedup-operations-read",
        "synkedup-sales-read",
        "synkedup-financial-read",
        "composio-approved",
        "vision",
        "cronjob",
        "todo",
        "skills",
    }


def test_mason_executes_drive_comparisons_and_supports_reminders():
    soul = _read(ROOT / "SOUL.md")
    assert "Never reply with a plan" in soul
    assert "Do not stop after saying what you will compare" in soul
    assert "Do not also call `synkedup_labor_variance`" in soul
    assert "search for the exact project and destination first" in soul
    assert "call `cronjob`" in soul
    assert "A checklist can stay in the requester's Discord conversation" in soul
    assert "call `todo` with the complete list" in soul


def test_browser_and_mcp_ports_are_bound_to_loopback():
    browser = _read(SYSTEMD / "cjs-synkedup-browser.service")
    mcp = _read(SYSTEMD / "cjs-synkedup-mcp.service")
    assert "--remote-debugging-address=127.0.0.1" in browser
    assert "--remote-debugging-port=9341" in browser
    assert "CJS_SYNKEDUP_MCP_HOST=127.0.0.1" in mcp
    assert "CJS_SYNKEDUP_MCP_PORT=9342" in mcp
    assert "IPAddressDeny=any" in mcp
    assert "IPAddressAllow=localhost" in mcp


def test_mason_gateway_can_refresh_aws_cache_and_drain_cleanly():
    gateway = _read(SYSTEMD / "cjs-mason-gateway.service")
    assert "TimeoutStopSec=210" in gateway
    assert "/home/nick/.aws/sso/cache" in gateway
    assert "/home/nick/.aws/cli/cache" in gateway
    assert "ProtectHome=read-only" in gateway


def test_login_vnc_is_loopback_only_and_never_auto_enabled():
    vnc = _read(SYSTEMD / "cjs-synkedup-vnc.service")
    assert "-localhost" in vnc
    assert "-rfbport 5909" in vnc
    assert "[Install]" not in vnc
    installer = _read(ROOT / "install-synkedup.sh")
    assert "systemctl stop cjs-synkedup-vnc.service" in installer
    assert "enable --now cjs-synkedup-vnc" not in installer


def test_services_use_isolated_account_and_hardened_filesystem():
    for name in (
        "cjs-synkedup-display.service",
        "cjs-synkedup-browser.service",
        "cjs-synkedup-vnc.service",
        "cjs-synkedup-mcp.service",
    ):
        text = _read(SYSTEMD / name)
        assert "User=cjs-synkedup" in text
        assert "NoNewPrivileges=true" in text
        assert "ProtectSystem=strict" in text
        assert "UMask=0077" in text
    mcp = _read(SYSTEMD / "cjs-synkedup-mcp.service")
    assert "ReadWritePaths=/var/log/cjs-synkedup" in mcp
    assert "/var/lib/cjs-synkedup/cache" in mcp
    installer = _read(ROOT / "install-synkedup.sh")
    assert "/var/lib/cjs-synkedup/cache" in installer


def test_installer_builds_from_a_commit_and_preserves_worktree_changes():
    installer = _read(ROOT / "install-synkedup.sh")
    assert "git -C \"$REPO_ROOT\" archive \"$RELEASE_REF\"" in installer
    assert "git reset" not in installer
    assert "git clean" not in installer
    assert "git checkout" not in installer
    assert "useradd --system" in installer
    assert "chgrp -R cjs-synkedup /opt/cjs-whiteout/venv" in installer
    assert "chmod -R g+rX /opt/cjs-whiteout/venv" in installer
    assert "commit the CJS SynkedUP release files before installing" in installer
    assert "systemctl restart cjs-synkedup-mcp.service" in installer


def test_runtime_secrets_are_fetched_without_secret_command_arguments():
    wrapper = _read(ROOT / "bin" / "run-mason-gateway")
    assert "ssm get-parameter" in wrapper
    assert "--with-decryption" in wrapper
    assert "set +x" in wrapper
    assert "Parameter.Value" in wrapper
    assert "echo \"$DISCORD_BOT_TOKEN\"" not in wrapper
    assert "echo \"$OPENROUTER_API_KEY\"" not in wrapper
    assert "SecureString" not in _read(ROOT / "config" / "mason-config.example.yaml")


def test_install_and_runtime_scripts_are_executable():
    for path in (
        ROOT / "install-synkedup.sh",
        ROOT / "bin" / "wait-for-local-port",
        ROOT / "bin" / "cjs-synkedup-status",
        ROOT / "bin" / "run-mason-gateway",
    ):
        assert os.access(path, os.X_OK), path
