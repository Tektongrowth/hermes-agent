from __future__ import annotations

import base64

from deployments.client_connection_portal.portal.app import Response
from deployments.client_connection_portal.portal.lambda_adapter import (
    LambdaRequestError,
    request_from_event,
    response_to_event,
)


def test_lambda_event_adapter_parses_cookie_query_and_urlencoded_form() -> None:
    event = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/test/connect",
        "headers": {
            "cookie": "portal_session=signed-session",
            "content-type": "application/x-www-form-urlencoded",
        },
        "queryStringParameters": {"safe": "value"},
        "body": "slot=gmail-primary&connected_email=alyssa%40example.com&csrf=signed-csrf",
        "isBase64Encoded": False,
    }

    request = request_from_event(event)

    assert request.method == "POST"
    assert request.path == "/test/connect"
    assert request.headers["Cookie"] == "portal_session=signed-session"
    assert request.query == {"safe": "value"}
    assert request.form == {
        "slot": "gmail-primary",
        "connected_email": "alyssa@example.com",
        "csrf": "signed-csrf",
    }


def test_lambda_event_adapter_decodes_base64_without_echoing_body() -> None:
    raw = b"username=sensitive-user&password=sensitive-password"
    event = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/test/connect",
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "body": base64.b64encode(raw).decode("ascii"),
        "isBase64Encoded": True,
    }

    request = request_from_event(event)

    assert request.form["username"] == "sensitive-user"
    assert request.form["password"] == "sensitive-password"
    assert "sensitive" not in repr(request.headers)


def test_lambda_event_adapter_rejects_large_or_unsupported_request_bodies() -> None:
    base = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/test/connect",
        "isBase64Encoded": False,
    }

    too_large = {
        **base,
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "body": "x=" + ("a" * 20_000),
    }
    unsupported = {
        **base,
        "headers": {"content-type": "application/json"},
        "body": '{"password":"must-not-be-parsed"}',
    }

    for event in (too_large, unsupported):
        try:
            request_from_event(event)
        except LambdaRequestError as exc:
            assert str(exc) == "invalid request"
        else:
            raise AssertionError("unsafe request body was accepted")


def test_lambda_response_adapter_preserves_security_headers_and_cookie() -> None:
    result = response_to_event(
        Response(
            status=303,
            body="",
            headers={
                "Location": "/setup",
                "Set-Cookie": "portal_session=signed; Secure; HttpOnly",
                "Cache-Control": "no-store",
            },
        )
    )

    assert result == {
        "statusCode": 303,
        "headers": {"Location": "/setup", "Cache-Control": "no-store"},
        "cookies": ["portal_session=signed; Secure; HttpOnly"],
        "body": "",
        "isBase64Encoded": False,
    }
