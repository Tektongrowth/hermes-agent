#!/usr/bin/env python3
"""CJS Composio bridge with approved-toolkit and connected-account pinning.

The bridge intentionally exposes a small stable MCP surface while leaving the
approved Composio toolkit open-ended. Hermes applies principal and action-level
approval policy before execution. This process adds tenant, toolkit, account,
input, output, and audit boundaries underneath that policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field


MAX_QUERY_LENGTH = 500
MAX_RESULT_TOOLS = 25
MAX_ARGUMENT_CHARS = 100_000
MAX_OUTPUT_CHARS = 150_000
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_AUDIT_PATH = "/var/lib/cjs-whiteout/hermes/logs/composio-audit.jsonl"
TOOLKIT_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
TOOL_SLUG_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,199}$")
SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|credential|password|secret|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)

mcp = FastMCP("CJS Composio Approved Tools")
StrictLimit = Annotated[int, Field(strict=True, ge=1, le=MAX_RESULT_TOOLS)]
_AUDIT_LOCK = threading.Lock()


class BridgeConfigurationError(RuntimeError):
    pass


class BridgeRequestError(RuntimeError):
    pass


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in os.getenv(name, "").split(",") if part.strip())


def _approved_toolkits() -> tuple[str, ...]:
    toolkits = _csv_env("CJS_COMPOSIO_TOOLKITS")
    if not toolkits or any(not TOOLKIT_RE.fullmatch(toolkit) for toolkit in toolkits):
        raise BridgeConfigurationError("approved Composio toolkit configuration is missing or invalid")
    return toolkits


def _approved_prefixes() -> tuple[str, ...]:
    prefixes = tuple(prefix.upper() for prefix in _csv_env("CJS_COMPOSIO_TOOL_PREFIXES"))
    if not prefixes or any(not re.fullmatch(r"[A-Z][A-Z0-9]{1,63}", prefix) for prefix in prefixes):
        raise BridgeConfigurationError("approved Composio tool prefix configuration is missing or invalid")
    return prefixes


def _account_selector() -> str:
    selector = os.getenv("CJS_COMPOSIO_ACCOUNT", "").strip()
    if not selector or len(selector) > 200 or any(ch.isspace() for ch in selector):
        raise BridgeConfigurationError("the pinned CJS Composio account is missing or invalid")
    return selector


def _composio_binary() -> str:
    binary = os.getenv("CJS_COMPOSIO_BIN", "/home/nick/.local/bin/composio").strip()
    path = Path(binary)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise BridgeConfigurationError("the Composio CLI is unavailable")
    return str(path)


def _validate_tool_slug(tool_slug: str) -> str:
    slug = str(tool_slug or "").strip().upper()
    if not TOOL_SLUG_RE.fullmatch(slug):
        raise BridgeRequestError("tool_slug must be an uppercase Composio tool slug")
    if not any(slug.startswith(prefix + "_") for prefix in _approved_prefixes()):
        raise BridgeRequestError("tool_slug is outside the approved CJS Composio toolkits")
    return slug


def _sanitize_text(text: str) -> str:
    clean = SENSITIVE_TEXT_RE.sub(lambda match: match.group(1) + match.group(2) + "[REDACTED]", text)
    return clean[:MAX_OUTPUT_CHARS]


def _run(args: list[str], *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> Any:
    completed = subprocess.run(
        [_composio_binary(), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "NO_COLOR": "1"},
    )
    stdout = _sanitize_text(completed.stdout or "")
    stderr = _sanitize_text(completed.stderr or "")
    if completed.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "Composio command failed"
        raise BridgeRequestError(detail[:4000])
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"output": stdout.strip()}


def _audit(tool: str, status: str, started: float, *, remote_tool: str = "") -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant": "cjs-landscape",
        "bridge_tool": tool,
        "remote_tool": remote_tool,
        "status": status,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "account_hash": hashlib.sha256(_account_selector().encode()).hexdigest()[:12],
    }
    path = Path(os.getenv("CJS_COMPOSIO_AUDIT_PATH", DEFAULT_AUDIT_PATH))
    with _AUDIT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        os.chmod(path, 0o600)


def _bounded_arguments(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        raise BridgeRequestError("arguments must be a JSON object")
    try:
        encoded = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise BridgeRequestError("arguments must be JSON-serializable") from exc
    if len(encoded) > MAX_ARGUMENT_CHARS:
        raise BridgeRequestError("arguments are too large")
    return encoded


@mcp.tool()
def composio_connection_status() -> dict[str, Any]:
    """Verify the pinned CJS Composio account and approved toolkit connection."""
    started = time.monotonic()
    try:
        data = _run(["connections", "list", "--toolkit", ",".join(_approved_toolkits())])
        expected = _account_selector()
        active = []
        if isinstance(data, dict):
            for toolkit in _approved_toolkits():
                rows = data.get(toolkit, [])
                if isinstance(rows, list):
                    active.extend(
                        {
                            "toolkit": toolkit,
                            "word_id": row.get("word_id"),
                            "alias": row.get("alias"),
                            "status": row.get("status"),
                            "pinned": expected in {row.get("word_id"), row.get("alias"), row.get("id")},
                        }
                        for row in rows
                        if isinstance(row, dict) and row.get("status") == "ACTIVE"
                    )
        result = {"connected": any(row["pinned"] for row in active), "accounts": active}
        _audit("composio_connection_status", "ok", started)
        return result
    except Exception:
        _audit("composio_connection_status", "error", started)
        raise


@mcp.tool()
def composio_search(query: str, limit: StrictLimit = 10) -> Any:
    """Find tools across the CJS-approved Composio toolkits."""
    started = time.monotonic()
    clean_query = str(query or "").strip()
    if not clean_query or len(clean_query) > MAX_QUERY_LENGTH:
        raise BridgeRequestError("query must be between 1 and 500 characters")
    try:
        result = _run(
            [
                "search",
                clean_query,
                "--toolkits",
                ",".join(_approved_toolkits()),
                "--limit",
                str(limit),
            ]
        )
        _audit("composio_search", "ok", started)
        return result
    except Exception:
        _audit("composio_search", "error", started)
        raise


@mcp.tool()
def composio_tool_schema(tool_slug: str) -> Any:
    """Read the input schema for one tool in an approved Composio toolkit."""
    started = time.monotonic()
    slug = _validate_tool_slug(tool_slug)
    try:
        result = _run(["execute", slug, "--account", _account_selector(), "--get-schema"])
        _audit("composio_tool_schema", "ok", started, remote_tool=slug)
        return result
    except Exception:
        _audit("composio_tool_schema", "error", started, remote_tool=slug)
        raise


@mcp.tool()
def composio_execute(
    tool_slug: str,
    arguments: dict[str, Any],
    dry_run: bool = False,
) -> Any:
    """Execute any tool in an approved toolkit against the pinned CJS account.

    Hermes requires one-time administrator confirmation before a non-admin can
    run an irreversible or externally consequential tool slug.
    """
    started = time.monotonic()
    slug = _validate_tool_slug(tool_slug)
    args = [
        "execute",
        slug,
        "--account",
        _account_selector(),
        "--data",
        _bounded_arguments(arguments),
    ]
    if dry_run:
        args.append("--dry-run")
    try:
        result = _run(args)
        _audit("composio_execute", "ok", started, remote_tool=slug)
        return result
    except Exception:
        _audit("composio_execute", "error", started, remote_tool=slug)
        raise


if __name__ == "__main__":
    mcp.run(transport="stdio")
