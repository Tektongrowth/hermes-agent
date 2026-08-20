#!/usr/bin/env python3
"""Verify CDP HTTP metadata and WebSocket reachability without printing tokens."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_MAX_HTTP_BODY = 1024 * 1024
_MAX_HANDSHAKE_HEADERS = 64 * 1024
_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class VerificationError(Exception):
    """A token-safe endpoint verification failure."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent endpoint checks from following redirects to another origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _parse_endpoint(raw_endpoint: str) -> urllib.parse.SplitResult:
    try:
        endpoint = urllib.parse.urlsplit(raw_endpoint)
        port = endpoint.port
    except ValueError as exc:
        raise VerificationError("endpoint URL is malformed") from exc
    if endpoint.scheme not in {"http", "https"}:
        raise VerificationError("endpoint scheme must be http or https")
    if not endpoint.hostname:
        raise VerificationError("endpoint URL has no host")
    if endpoint.username is not None or endpoint.password is not None:
        raise VerificationError("endpoint URL must not contain user information")
    if endpoint.fragment:
        raise VerificationError("endpoint URL must not contain a fragment")
    if port is not None and not 1 <= port <= 65535:
        raise VerificationError("endpoint port is outside the valid range")
    return endpoint


def _version_url(endpoint: urllib.parse.SplitResult) -> str:
    path = endpoint.path.rstrip("/") + "/json/version"
    return urllib.parse.urlunsplit(
        (endpoint.scheme, endpoint.netloc, path, endpoint.query, "")
    )


def _load_version(endpoint: urllib.parse.SplitResult, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        _version_url(endpoint),
        headers={"Accept": "application/json", "User-Agent": "hermes-cdp-verifier/1"},
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise VerificationError(f"/json/version returned HTTP status {response.status}")
            body = response.read(_MAX_HTTP_BODY + 1)
    except urllib.error.HTTPError as exc:
        raise VerificationError(
            f"/json/version returned HTTP status {exc.code}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise VerificationError("could not reach /json/version") from None

    if len(body) > _MAX_HTTP_BODY:
        raise VerificationError("/json/version response is too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VerificationError("/json/version did not return valid JSON") from None
    if not isinstance(payload, dict):
        raise VerificationError("/json/version returned a non-object JSON value")
    return payload


def _websocket_target(
    endpoint: urllib.parse.SplitResult, websocket_url: str
) -> urllib.parse.SplitResult:
    try:
        advertised = urllib.parse.urlsplit(websocket_url)
        advertised_port = advertised.port
    except ValueError as exc:
        raise VerificationError("returned WebSocket URL is malformed") from exc
    if advertised.scheme not in {"ws", "wss"} or not advertised.hostname:
        raise VerificationError("/json/version returned an invalid WebSocket URL")
    if advertised.username is not None or advertised.password is not None:
        raise VerificationError("returned WebSocket URL contains user information")
    if advertised_port is not None and not 1 <= advertised_port <= 65535:
        raise VerificationError("returned WebSocket URL has an invalid port")
    if not advertised.path.startswith("/"):
        raise VerificationError("returned WebSocket URL has an invalid path")

    # Chrome advertises its client-side authority. Keep only its opaque path and
    # query, then route the handshake through the endpoint being verified.
    tunnel_scheme = "wss" if endpoint.scheme == "https" else "ws"
    return urllib.parse.SplitResult(
        tunnel_scheme,
        endpoint.netloc,
        advertised.path,
        advertised.query,
        "",
    )


def _host_header(hostname: str, port: int, secure: bool) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if secure else 80
    return host if port == default_port else f"{host}:{port}"


def _check_websocket(target: urllib.parse.SplitResult, timeout: float) -> None:
    hostname = target.hostname
    if hostname is None:
        raise VerificationError("WebSocket target has no host")
    secure = target.scheme == "wss"
    port = target.port or (443 if secure else 80)
    request_target = target.path or "/"
    if target.query:
        request_target += "?" + target.query

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()
    ).decode("ascii")
    request = (
        f"GET {request_target} HTTP/1.1\r\n"
        f"Host: {_host_header(hostname, port, secure)}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "User-Agent: hermes-cdp-verifier/1\r\n"
        "\r\n"
    ).encode("ascii")

    connection: socket.socket | ssl.SSLSocket | None = None
    try:
        connection = socket.create_connection((hostname, port), timeout=timeout)
        if secure:
            context = ssl.create_default_context()
            connection = context.wrap_socket(connection, server_hostname=hostname)
            connection.settimeout(timeout)
        connection.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > _MAX_HANDSHAKE_HEADERS:
                raise VerificationError("WebSocket handshake headers are too large")
    except VerificationError:
        raise
    except (OSError, ssl.SSLError, TimeoutError):
        raise VerificationError("could not complete the WebSocket handshake") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    header_block = bytes(response).split(b"\r\n\r\n", 1)[0]
    try:
        lines = header_block.decode("iso-8859-1").split("\r\n")
        status_parts = lines[0].split(" ", 2)
        status = int(status_parts[1])
    except (IndexError, ValueError):
        raise VerificationError("WebSocket handshake returned an invalid response") from None
    if status != 101:
        raise VerificationError(f"WebSocket handshake returned HTTP status {status}")

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    if headers.get("upgrade", "").lower() != "websocket":
        raise VerificationError("WebSocket handshake omitted the Upgrade header")
    connection_tokens = {
        item.strip().lower() for item in headers.get("connection", "").split(",")
    }
    if "upgrade" not in connection_tokens:
        raise VerificationError("WebSocket handshake omitted Connection: Upgrade")
    if headers.get("sec-websocket-accept", "") != expected_accept:
        raise VerificationError("WebSocket handshake returned an invalid acceptance value")


def verify_endpoint(raw_endpoint: str, timeout: float) -> None:
    endpoint = _parse_endpoint(raw_endpoint)
    version = _load_version(endpoint, timeout)
    websocket_url = version.get("webSocketDebuggerUrl")
    if not isinstance(websocket_url, str) or not websocket_url:
        raise VerificationError(
            "/json/version did not provide webSocketDebuggerUrl"
        )
    target = _websocket_target(endpoint, websocket_url)
    _check_websocket(target, timeout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check one or more CDP endpoints without printing endpoint or "
            "WebSocket tokens."
        )
    )
    parser.add_argument(
        "endpoints",
        nargs="+",
        metavar="URL",
        help="CDP HTTP endpoint, such as http://127.0.0.1:9241",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="timeout in seconds for each network operation (default: 5)",
    )
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 60:
        parser.error("--timeout must be greater than 0 and no more than 60")
    return args


def main() -> int:
    args = parse_args()
    failures = 0
    for index, endpoint in enumerate(args.endpoints, start=1):
        try:
            verify_endpoint(endpoint, args.timeout)
        except VerificationError as exc:
            failures += 1
            print(f"[{index}] FAIL: {exc}", file=sys.stderr)
        else:
            print(f"[{index}] PASS: /json/version and WebSocket handshake succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
