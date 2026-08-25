from __future__ import annotations

import base64
from typing import Any
from urllib.parse import parse_qsl

from .app import Request, Response


_MAX_BODY_BYTES = 16_384
_MAX_FORM_FIELDS = 32


class LambdaRequestError(ValueError):
    """Raised for a malformed or unsupported Lambda Function URL request."""


def request_from_event(event: dict[str, Any]) -> Request:
    try:
        method = str(event["requestContext"]["http"]["method"]).upper()
        path = str(event.get("rawPath") or "/")
        raw_headers = event.get("headers") or {}
        lowered = {str(key).lower(): str(value) for key, value in raw_headers.items()}
        headers: dict[str, str] = {}
        if "cookie" in lowered:
            headers["Cookie"] = lowered["cookie"]
        if "content-type" in lowered:
            headers["Content-Type"] = lowered["content-type"]
        query = {
            str(key): str(value)
            for key, value in (event.get("queryStringParameters") or {}).items()
            if value is not None
        }
        form: dict[str, str] = {}
        raw_body = event.get("body") or ""
        if raw_body:
            if event.get("isBase64Encoded"):
                body_bytes = base64.b64decode(raw_body, validate=True)
            else:
                body_bytes = str(raw_body).encode("utf-8")
            if len(body_bytes) > _MAX_BODY_BYTES:
                raise LambdaRequestError("invalid request")
            content_type = lowered.get("content-type", "").split(";", 1)[0].strip()
            if content_type != "application/x-www-form-urlencoded":
                raise LambdaRequestError("invalid request")
            pairs = parse_qsl(
                body_bytes.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=False,
                max_num_fields=_MAX_FORM_FIELDS,
            )
            if len({key for key, _ in pairs}) != len(pairs):
                raise LambdaRequestError("invalid request")
            form = dict(pairs)
        return Request(method=method, path=path, headers=headers, query=query, form=form)
    except LambdaRequestError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise LambdaRequestError("invalid request") from exc


def response_to_event(response: Response) -> dict[str, Any]:
    headers = dict(response.headers)
    cookies: list[str] = []
    cookie = headers.pop("Set-Cookie", None)
    if cookie:
        cookies.append(cookie)
    return {
        "statusCode": response.status,
        "headers": headers,
        "cookies": cookies,
        "body": response.body,
        "isBase64Encoded": False,
    }
