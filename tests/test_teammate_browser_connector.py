"""Security invariants for the teammate browser connector package."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "scripts" / "teammate_browser_connector"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "teammate_browser_connector_verifier", CONNECTOR / "verify_connector.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_pins_advertised_websocket_path_to_endpoint_authority():
    verifier = _load_verifier()
    endpoint = verifier._parse_endpoint("http://127.0.0.1:9241")

    target = verifier._websocket_target(
        endpoint, "ws://127.0.0.1:9227/devtools/browser/opaque-token?x=1"
    )

    assert target.geturl() == "ws://127.0.0.1:9241/devtools/browser/opaque-token?x=1"


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://127.0.0.1:9241",
        "http://user:password@127.0.0.1:9241",
        "http://127.0.0.1:9241/#fragment",
    ],
)
def test_verifier_rejects_unsafe_endpoint_forms(endpoint):
    verifier = _load_verifier()

    with pytest.raises(verifier.VerificationError):
        verifier._parse_endpoint(endpoint)


def test_macos_wrapper_rejects_dedicated_port_reuse_and_non_loopback_listener():
    script = (CONNECTOR / "install-macos.sh").read_text(encoding="utf-8")

    assert "Dedicated connector port is already serving CDP" in script
    assert "listener_is_loopback_only" in script
    assert "Local CDP listener is not loopback-only" in script


def test_windows_wrapper_rejects_dedicated_port_reuse_and_non_loopback_listener():
    script = (CONNECTOR / "install-windows.ps1").read_text(encoding="utf-8")

    assert "Dedicated connector port is already serving CDP" in script
    assert "Test-LoopbackOnlyListener" in script
    assert "Local CDP listener is not loopback-only" in script


def test_windows_start_failure_stops_task_and_log_removal_covers_both_logs():
    installer = (CONNECTOR / "install-windows.ps1").read_text(encoding="utf-8")
    uninstaller = (CONNECTOR / "uninstall-windows.ps1").read_text(encoding="utf-8")

    failure = installer.index("Connector did not prove the remote forward")
    preceding = installer[max(0, failure - 700) : failure]
    assert "Stop-ScheduledTask" in preceding
    assert "$SshDebugLogPath" in uninstaller
    assert "Remove-Item -LiteralPath $SshDebugLogPath" in uninstaller
