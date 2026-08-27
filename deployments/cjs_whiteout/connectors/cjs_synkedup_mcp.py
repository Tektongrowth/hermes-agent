#!/usr/bin/env python3
"""CJS Landscape SynkedUP read-only browser MCP.

The connector owns the browser, tenant, routes, selectors, scripts, and audit
location. Callers can only choose bounded read filters. Discord identity and
role policy are enforced by Hermes before these tools are exposed; this server
adds read-only browser and response boundaries underneath that policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import websocket
from mcp.server.fastmcp import FastMCP
from pydantic import Field


TENANT = "cjs-landscape"
DEFAULT_BASE_URL = "https://app.synkedup.com"
DEFAULT_CDP_URL = "http://127.0.0.1:9341"
DEFAULT_AUDIT_PATH = "/var/log/cjs-synkedup/audit.jsonl"
MAX_QUERY_LENGTH = 100
MAX_RECORD_ID_LENGTH = 80
MAX_RESULT_ROWS = 100
MAX_RESPONSE_CHARS = 120_000
MAX_REQUESTS_PER_MINUTE = 30
MIN_REQUEST_INTERVAL_SECONDS = 0.20
ALLOWED_CDP_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
UNSAFE_QUERY_RE = re.compile(
    r"(?:https?://|javascript:|data:|file:|<script|</|\$\(|`|\x00|[{}|;])",
    re.IGNORECASE,
)
MUTATION_SEGMENT_RE = re.compile(
    r"(?:^|[/_.?&=-])(?:create|edit|new|delete|remove|save|send|approve|"
    r"unschedule|charge|refund|void|collect|submit|post|put|patch)(?:$|[/_.?&=-])",
    re.IGNORECASE,
)
LOGIN_PATH_RE = re.compile(r"/(?:login|log-in|signin|sign-in|auth)(?:/|$)", re.IGNORECASE)
LOGIN_TEXT_RE = re.compile(
    r"\b(?:sign in|log in|forgot password|two-factor|verification code)\b",
    re.IGNORECASE,
)
PROTECTED_FINANCIAL_TERMS = frozenset(
    {
        "actual cost",
        "balance",
        "billing",
        "cost",
        "gross profit",
        "invoice",
        "labor cost",
        "margin",
        "material cost",
        "net profit",
        "payment",
        "payroll",
        "price",
        "profit",
        "quickbooks",
        "rate",
        "revenue",
        "tax",
        "wage",
    }
)
SALES_HIDDEN_TERMS = frozenset(
    {
        "actual cost",
        "balance",
        "gross profit",
        "invoice",
        "labor cost",
        "margin",
        "material cost",
        "net profit",
        "payment",
        "payroll",
        "quickbooks",
        "tax",
        "wage",
    }
)

mcp = FastMCP("CJS SynkedUP Read Only")
StrictPageSize = Annotated[int, Field(strict=True, ge=1, le=MAX_RESULT_ROWS)]
StrictCursor = Annotated[int, Field(strict=True, ge=0, le=100_000)]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    domain: str
    route: str
    description: str
    access_class: str
    detail_route: str | None = None


# Routes are server-owned defaults and can be calibrated after the authenticated
# UI inventory by replacing this checked-in catalog in a release. No caller can
# provide a path, host, URL, selector, or script.
TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("synkedup_company_profile", "reference", "/settings/company", "Read the CJS company profile and operating settings.", "reference"),
    ToolSpec("synkedup_employees", "reference", "/settings/employees", "List employees and their non-payroll profile fields.", "reference", "/settings/employees/{record_id}"),
    ToolSpec("synkedup_crews", "reference", "/settings/crews", "List crews and crew membership.", "reference", "/settings/crews/{record_id}"),
    ToolSpec("synkedup_divisions", "reference", "/settings/divisions", "List company divisions.", "reference", "/settings/divisions/{record_id}"),
    ToolSpec("synkedup_items", "reference", "/settings/items", "List the company item catalog without protected cost fields.", "reference", "/settings/items/{record_id}"),
    ToolSpec("synkedup_vendors", "reference", "/settings/vendors", "List vendors and non-financial reference fields.", "reference", "/settings/vendors/{record_id}"),
    ToolSpec("synkedup_customers", "sales", "/customers", "Search customers across the CJS SynkedUP account.", "sales", "/customers/{record_id}"),
    ToolSpec("synkedup_properties", "sales", "/properties", "Search customer properties and service locations.", "sales", "/properties/{record_id}"),
    ToolSpec("synkedup_leads", "sales", "/leads", "Search leads in every pipeline state.", "sales", "/leads/{record_id}"),
    ToolSpec("synkedup_consultations", "sales", "/consultations", "Read consultations and their status.", "sales", "/consultations/{record_id}"),
    ToolSpec("synkedup_estimates", "sales", "/estimates", "Read estimates and sales-facing pricing fields.", "sales", "/estimates/{record_id}"),
    ToolSpec("synkedup_proposals", "sales", "/proposals", "Read proposals in every state.", "sales", "/proposals/{record_id}"),
    ToolSpec("synkedup_sales_pipeline", "sales", "/sales", "Read the sales pipeline dashboard.", "sales"),
    ToolSpec("synkedup_pricing_catalog", "sales", "/settings/pricing", "Read sales pricing without internal cost or margin fields.", "sales", "/settings/pricing/{record_id}"),
    ToolSpec("synkedup_jobs", "operations", "/jobs", "Search all jobs, including unscheduled, sold, active, completed, and archived jobs.", "operations", "/jobs/{record_id}"),
    ToolSpec("synkedup_job_briefs", "operations", "/jobs", "Read job briefs and scope details across every job state.", "operations", "/jobs/{record_id}/brief"),
    ToolSpec("synkedup_maintenance", "operations", "/maintenance", "Read maintenance work in every state.", "operations", "/maintenance/{record_id}"),
    ToolSpec("synkedup_service_tickets", "operations", "/service-tickets", "Read service tickets in every state.", "operations", "/service-tickets/{record_id}"),
    ToolSpec("synkedup_schedules", "operations", "/schedule", "Read scheduled work without changing the schedule.", "operations"),
    ToolSpec("synkedup_routes", "operations", "/routes", "Read route plans and assigned work.", "operations", "/routes/{record_id}"),
    ToolSpec("synkedup_work_areas", "operations", "/work-areas", "Read work areas and assignments.", "operations", "/work-areas/{record_id}"),
    ToolSpec("synkedup_job_notes", "operations", "/jobs", "Read notes attached to a job.", "operations", "/jobs/{record_id}/notes"),
    ToolSpec("synkedup_job_attachments", "operations", "/jobs", "List attachments available on a job.", "operations", "/jobs/{record_id}/attachments"),
    ToolSpec("synkedup_job_photos", "operations", "/jobs", "List job photos and captions.", "operations", "/jobs/{record_id}/photos"),
    ToolSpec("synkedup_job_documents", "operations", "/jobs", "List read-only job PDFs and downloadable documents.", "operations", "/jobs/{record_id}/documents"),
    ToolSpec("synkedup_time_entries", "operations", "/time", "Read timesheets and time entries.", "operations", "/time/{record_id}"),
    ToolSpec("synkedup_clock_status", "operations", "/time/clock-status", "Read current clock status without clocking anyone in or out.", "operations"),
    ToolSpec("synkedup_labor_variance", "operations", "/reports/labor-hours", "Compare estimated and actual labor hours without payroll or labor cost fields.", "operations", "/reports/labor-hours/{record_id}"),
    ToolSpec("synkedup_materials", "operations", "/materials", "Read material quantities and fulfillment state without costs.", "operations", "/materials/{record_id}"),
    ToolSpec("synkedup_equipment", "operations", "/equipment", "Read equipment assigned to work.", "operations", "/equipment/{record_id}"),
    ToolSpec("synkedup_subcontractors", "operations", "/subcontractors", "Read subcontractor assignments without payment fields.", "operations", "/subcontractors/{record_id}"),
    ToolSpec("synkedup_item_quantities", "operations", "/reports/item-quantities", "Compare estimated and actual item quantities without costs.", "operations", "/reports/item-quantities/{record_id}"),
    ToolSpec("synkedup_operations_dashboard", "operations", "/dashboard", "Read the operations dashboard.", "operations"),
    ToolSpec("synkedup_operations_reports", "operations", "/reports", "List and read non-financial operations reports.", "operations", "/reports/{record_id}"),
    ToolSpec("synkedup_exports", "operations", "/exports", "List documented read-only exports and same-host download links.", "operations"),
    ToolSpec("synkedup_job_costing", "financial", "/job-costing", "Read full job costing details.", "financial", "/job-costing/{record_id}"),
    ToolSpec("synkedup_margins", "financial", "/reports/margins", "Read job and company margin reports.", "financial", "/reports/margins/{record_id}"),
    ToolSpec("synkedup_invoices", "financial", "/invoices", "Read invoices without creating, editing, sending, or collecting payment.", "financial", "/invoices/{record_id}"),
    ToolSpec("synkedup_balances", "financial", "/reports/balances", "Read customer and job balances.", "financial", "/reports/balances/{record_id}"),
    ToolSpec("synkedup_payment_status", "financial", "/reports/payment-status", "Read payment status without initiating payment activity.", "financial", "/reports/payment-status/{record_id}"),
    ToolSpec("synkedup_accounting_sync", "financial", "/settings/accounting", "Read accounting integration and sync status.", "financial"),
    ToolSpec("synkedup_financial_dashboard", "financial", "/dashboard/financial", "Read the financial dashboard.", "financial"),
    ToolSpec("synkedup_financial_reports", "financial", "/reports/financial", "List and read financial reports.", "financial", "/reports/financial/{record_id}"),
)
TOOL_SPEC_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}
TOOL_NAMES_BY_ACCESS_CLASS = {
    access: tuple(spec.name for spec in TOOL_SPECS if spec.access_class == access)
    for access in ("reference", "sales", "operations", "financial")
}
HEALTH_TOOL_NAMES = ("synkedup_connection_health", "synkedup_session_status")
ALL_TOOL_NAMES = HEALTH_TOOL_NAMES + tuple(spec.name for spec in TOOL_SPECS)


EXTRACT_PAGE_SCRIPT = r"""
(() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const text = (el) => clean(el && (el.innerText || el.textContent));
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]'))
    .filter(visible).map(text).filter(Boolean).slice(0, 30);
  const tables = Array.from(document.querySelectorAll('table')).slice(0, 8).map((table) => {
    const headers = Array.from(table.querySelectorAll('thead th')).map(text).filter(Boolean);
    const rows = Array.from(table.querySelectorAll('tbody tr, tr')).filter(visible).slice(0, 250)
      .map((row) => Array.from(row.querySelectorAll('th,td')).map(text).filter(Boolean))
      .filter((row) => row.length);
    return {headers, rows};
  }).filter((table) => table.rows.length || table.headers.length);
  const fields = [];
  for (const label of Array.from(document.querySelectorAll('label,dt,[data-label]')).slice(0, 300)) {
    if (!visible(label)) continue;
    const name = text(label);
    let value = '';
    if (label.tagName === 'DT' && label.nextElementSibling) value = text(label.nextElementSibling);
    if (!value && label.htmlFor) {
      const target = document.getElementById(label.htmlFor);
      value = clean(target && (target.value || target.textContent));
    }
    if (!value && label.nextElementSibling) value = text(label.nextElementSibling);
    if (name && value && name !== value) fields.push({label: name, value});
    if (fields.length >= 200) break;
  }
  const cards = Array.from(document.querySelectorAll('article,[role="row"],.card,[data-testid*="card"]'))
    .filter(visible).map(text).filter(Boolean).slice(0, 120);
  const links = Array.from(document.querySelectorAll('a[href]')).filter(visible).map((a) => {
    try {
      const url = new URL(a.href, location.href);
      const label = text(a);
      if (url.origin !== location.origin) return null;
      if (!/(download|export|attachment|photo|document|pdf)/i.test(label + ' ' + url.pathname)) return null;
      return {label, href: url.href};
    } catch (_) { return null; }
  }).filter(Boolean).slice(0, 100);
  const alerts = Array.from(document.querySelectorAll('[role="alert"],.alert,.error,.empty-state'))
    .filter(visible).map(text).filter(Boolean).slice(0, 20);
  return {url: location.href, title: document.title, headings, tables, fields, cards, links, alerts};
})()
""".strip()
READY_STATE_SCRIPT = "document.readyState"
LOCATION_SCRIPT = "location.href"


class SecurityViolation(RuntimeError):
    pass


class ReauthenticationRequired(RuntimeError):
    pass


class RateLimitExceeded(RuntimeError):
    pass


class AuditLogger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("CJS_SYNKEDUP_AUDIT_PATH", DEFAULT_AUDIT_PATH))
        self._lock = threading.Lock()

    def write(
        self,
        *,
        tool: str,
        scope: dict[str, Any],
        status: str,
        duration_ms: int,
        error_class: str | None = None,
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant": TENANT,
            "tool": tool,
            "scope": _audit_scope(scope),
            "status": status,
            "duration_ms": max(0, int(duration_ms)),
            "error_class": error_class,
        }
        line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            try:
                os.chmod(self.path, 0o640)
            except OSError:
                pass


def _audit_scope(scope: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    record_id = scope.get("record_id")
    if record_id:
        result["record_id_hash"] = hashlib.sha256(str(record_id).encode()).hexdigest()[:12]
    result["query_present"] = bool(scope.get("query"))
    for key in ("start_date", "end_date", "page_size", "cursor"):
        value = scope.get(key)
        if value not in (None, ""):
            result[key] = value
    return result


class SlidingRateLimiter:
    def __init__(self, max_requests: int = MAX_REQUESTS_PER_MINUTE):
        self.max_requests = max_requests
        self._events: deque[float] = deque()
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        now = time.monotonic()
        with self._lock:
            while self._events and now - self._events[0] >= 60:
                self._events.popleft()
            if len(self._events) >= self.max_requests:
                raise RateLimitExceeded("request rate exceeded")
            delay = MIN_REQUEST_INTERVAL_SECONDS - (now - self._last)
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._events.append(now)
            self._last = now


class CDPClient:
    def __init__(self, cdp_url: str | None = None, *, timeout: float = 20.0):
        self.cdp_url = (cdp_url or os.getenv("CJS_SYNKEDUP_CDP_URL", DEFAULT_CDP_URL)).rstrip("/")
        self.timeout = timeout
        self._validate_cdp_url()
        self._socket: Any = None
        self._next_id = 0
        self._pending_ids: set[int] = set()

    def _validate_cdp_url(self) -> None:
        parsed = urllib.parse.urlsplit(self.cdp_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ALLOWED_CDP_HOSTS
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise SecurityViolation("CDP endpoint must be loopback HTTP")

    def _json_targets(self) -> list[dict[str, Any]]:
        request = urllib.request.Request(f"{self.cdp_url}/json/list", method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError("invalid CDP target response")
        return [item for item in payload if isinstance(item, dict)]

    def connect(self, expected_host: str) -> None:
        targets = [item for item in self._json_targets() if item.get("type") == "page"]
        matching = []
        for target in targets:
            try:
                if urllib.parse.urlsplit(str(target.get("url", ""))).hostname == expected_host:
                    matching.append(target)
            except ValueError:
                continue
        target = matching[0] if matching else (targets[0] if targets else None)
        if not target or not isinstance(target.get("webSocketDebuggerUrl"), str):
            raise RuntimeError("no browser page target available")
        self._socket = websocket.create_connection(
            target["webSocketDebuggerUrl"],
            timeout=self.timeout,
            origin=self.cdp_url,
        )
        self.command("Page.enable")
        self.command("Runtime.enable")
        # Business-data mutations are blocked even if a page script, extension,
        # or compromised response attempts one while a tool call is active.
        self.command("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def _send(self, method: str, params: dict[str, Any] | None = None) -> int:
        if self._socket is None:
            raise RuntimeError("CDP is not connected")
        self._next_id += 1
        request_id = self._next_id
        self._pending_ids.add(request_id)
        self._socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        return request_id

    def _continue_or_block(self, event: dict[str, Any]) -> None:
        params = event.get("params") or {}
        request = params.get("request") or {}
        fetch_id = params.get("requestId")
        method = str(request.get("method", "")).upper()
        if not isinstance(fetch_id, str):
            return
        if method in {"GET", "HEAD", "OPTIONS"}:
            self._send("Fetch.continueRequest", {"requestId": fetch_id})
        else:
            self._send("Fetch.failRequest", {"requestId": fetch_id, "errorReason": "BlockedByClient"})

    def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method.startswith("Network.") or method in {
            "Runtime.callFunctionOn",
            "Runtime.compileScript",
            "Page.addScriptToEvaluateOnNewDocument",
        }:
            raise SecurityViolation("CDP method is not allowlisted")
        request_id = self._send(method, params)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._socket is None:
                raise RuntimeError("CDP socket closed")
            self._socket.settimeout(max(0.1, deadline - time.monotonic()))
            message = json.loads(self._socket.recv())
            if message.get("method") == "Fetch.requestPaused":
                self._continue_or_block(message)
                continue
            response_id = message.get("id")
            if isinstance(response_id, int):
                self._pending_ids.discard(response_id)
            if response_id != request_id:
                continue
            if "error" in message:
                raise RuntimeError("CDP command failed")
            return message.get("result") or {}
        raise TimeoutError("CDP command timed out")

    def evaluate_constant(self, expression: str) -> Any:
        if expression not in {EXTRACT_PAGE_SCRIPT, READY_STATE_SCRIPT, LOCATION_SCRIPT}:
            raise SecurityViolation("runtime expression is not allowlisted")
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": False,
            },
        )
        inner = result.get("result") or {}
        if inner.get("subtype") == "error":
            raise RuntimeError("page evaluation failed")
        return inner.get("value")

    def navigate(self, url: str, expected_origin: str) -> dict[str, Any]:
        _validate_navigation_url(url, expected_origin)
        self.command("Page.navigate", {"url": url, "transitionType": "typed"})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.evaluate_constant(READY_STATE_SCRIPT) in {"interactive", "complete"}:
                break
            time.sleep(0.1)
        current_url = str(self.evaluate_constant(LOCATION_SCRIPT) or "")
        page = self.evaluate_constant(EXTRACT_PAGE_SCRIPT)
        if not isinstance(page, dict):
            raise RuntimeError("page extraction returned invalid data")
        page["url"] = current_url or page.get("url", "")
        return page


class SynkedUPBrowser:
    def __init__(self):
        self.base_url = os.getenv("CJS_SYNKEDUP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.origin, self.host = _validate_base_url(self.base_url)

    def read(self, spec: ToolSpec, *, record_id: str = "") -> dict[str, Any]:
        route = spec.route
        if record_id:
            if not spec.detail_route:
                raise ValueError("record_id is not supported by this tool")
            route = spec.detail_route.format(record_id=urllib.parse.quote(record_id, safe=""))
        url = urllib.parse.urljoin(self.base_url + "/", route.lstrip("/"))
        _validate_navigation_url(url, self.origin)
        client = CDPClient()
        try:
            client.connect(self.host)
            page = client.navigate(url, self.origin)
        finally:
            client.close()
        if _looks_logged_out(page):
            raise ReauthenticationRequired("authenticated SynkedUP session is required")
        _validate_result_origin(str(page.get("url", "")), self.origin)
        return page

    def status(self) -> dict[str, Any]:
        client = CDPClient()
        try:
            client.connect(self.host)
            page = client.evaluate_constant(EXTRACT_PAGE_SCRIPT)
        finally:
            client.close()
        if not isinstance(page, dict):
            raise RuntimeError("page extraction returned invalid data")
        if _looks_logged_out(page):
            raise ReauthenticationRequired("authenticated SynkedUP session is required")
        _validate_result_origin(str(page.get("url", "")), self.origin)
        return {"url": page.get("url", ""), "title": page.get("title", "")}


_EXECUTION_LOCK = threading.Lock()
_RATE_LIMITER = SlidingRateLimiter()
_AUDIT = AuditLogger()


def _validate_base_url(value: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise SecurityViolation("base URL must be an HTTPS origin")
    return f"{parsed.scheme}://{parsed.netloc}", parsed.hostname


def _validate_navigation_url(value: str, expected_origin: str) -> None:
    parsed = urllib.parse.urlsplit(value)
    if f"{parsed.scheme}://{parsed.netloc}" != expected_origin:
        raise SecurityViolation("cross-origin navigation refused")
    decoded_target = urllib.parse.unquote(parsed.path + "?" + parsed.query)
    if MUTATION_SEGMENT_RE.search(decoded_target):
        raise SecurityViolation("mutation navigation refused")
    if parsed.fragment:
        raise SecurityViolation("fragment navigation refused")


def _validate_result_origin(value: str, expected_origin: str) -> None:
    parsed = urllib.parse.urlsplit(value)
    if f"{parsed.scheme}://{parsed.netloc}" != expected_origin:
        raise SecurityViolation("browser left the configured SynkedUP origin")


def _looks_logged_out(page: dict[str, Any]) -> bool:
    url = str(page.get("url", ""))
    if LOGIN_PATH_RE.search(urllib.parse.urlsplit(url).path):
        return True
    markers: list[str] = []
    for key in ("title", "headings", "alerts", "cards"):
        value = page.get(key)
        if isinstance(value, list):
            markers.extend(str(item) for item in value[:20])
        elif value:
            markers.append(str(value))
    return bool(LOGIN_TEXT_RE.search(" ".join(markers)[:10_000]))


def _validated_query(value: str) -> str:
    value = str(value or "").strip()
    if len(value) > MAX_QUERY_LENGTH or UNSAFE_QUERY_RE.search(value):
        raise ValueError("query contains unsupported characters or is too long")
    return value


def _validated_record_id(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > MAX_RECORD_ID_LENGTH or not SAFE_ID_RE.fullmatch(value):
        raise ValueError("record_id must be a bounded SynkedUP identifier")
    return value


def _validated_date(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc
    return value


def _contains_term(value: Any, terms: frozenset[str]) -> bool:
    lowered = str(value or "").casefold()
    return any(term in lowered for term in terms)


def _filter_page(page: dict[str, Any], access_class: str) -> dict[str, Any]:
    if access_class == "financial":
        terms = frozenset()
    elif access_class == "sales":
        terms = SALES_HIDDEN_TERMS
    else:
        terms = PROTECTED_FINANCIAL_TERMS

    result = {
        "title": str(page.get("title", ""))[:500],
        "headings": [str(item)[:500] for item in page.get("headings", [])[:30]],
        "alerts": [str(item)[:1000] for item in page.get("alerts", [])[:20]],
        "tables": [],
        "fields": [],
        "cards": [],
        "links": [],
    }
    for field in page.get("fields", [])[:200]:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label", ""))
        if terms and _contains_term(label, terms):
            continue
        result["fields"].append({"label": label[:300], "value": str(field.get("value", ""))[:2000]})
    for table in page.get("tables", [])[:8]:
        if not isinstance(table, dict):
            continue
        headers = [str(item)[:300] for item in table.get("headers", [])[:50]]
        keep_indexes = [index for index, header in enumerate(headers) if not terms or not _contains_term(header, terms)]
        rows = []
        for raw_row in table.get("rows", [])[:250]:
            if not isinstance(raw_row, list):
                continue
            if headers:
                row = [str(raw_row[index])[:2000] for index in keep_indexes if index < len(raw_row)]
            else:
                row_text = " ".join(str(item) for item in raw_row)
                if terms and _contains_term(row_text, terms):
                    continue
                row = [str(item)[:2000] for item in raw_row[:50]]
            if row:
                rows.append(row)
        result["tables"].append({"headers": [headers[index] for index in keep_indexes], "rows": rows})
    for card in page.get("cards", [])[:120]:
        if not terms or not _contains_term(card, terms):
            result["cards"].append(str(card)[:3000])
    page_url = urllib.parse.urlsplit(str(page.get("url", "")))
    page_origin = (
        f"{page_url.scheme}://{page_url.netloc}"
        if page_url.scheme and page_url.netloc
        else ""
    )
    for link in page.get("links", [])[:100]:
        if not isinstance(link, dict):
            continue
        href = str(link.get("href", ""))
        label = str(link.get("label", ""))
        link_url = urllib.parse.urlsplit(href)
        if not page_origin or f"{link_url.scheme}://{link_url.netloc}" != page_origin:
            continue
        if terms and _contains_term(label, terms):
            continue
        result["links"].append({"label": label[:500], "href": href[:2000]})
    return result


def _local_filter(
    payload: dict[str, Any],
    *,
    query: str,
    start_date: str,
    end_date: str,
    page_size: int,
    cursor: int,
) -> dict[str, Any]:
    needle = query.casefold()

    def matches(value: Any) -> bool:
        text = str(value)
        if needle and needle not in text.casefold():
            return False
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        if start_date and dates and max(dates) < start_date:
            return False
        if end_date and dates and min(dates) > end_date:
            return False
        return True

    for table in payload.get("tables", []):
        rows = [row for row in table.get("rows", []) if matches(" ".join(row))]
        table["rows"] = rows[cursor : cursor + page_size]
    cards = [card for card in payload.get("cards", []) if matches(card)]
    payload["cards"] = cards[cursor : cursor + page_size]
    fields = [field for field in payload.get("fields", []) if matches(field)]
    payload["fields"] = fields[cursor : cursor + page_size]
    payload["pagination"] = {"cursor": cursor, "page_size": page_size}
    return payload


def _bounded_payload(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) <= MAX_RESPONSE_CHARS:
        return value
    return {
        "ok": True,
        "tenant": TENANT,
        "truncated": True,
        "message": "Result exceeded the connector response limit. Narrow the query.",
        "headings": value.get("headings", [])[:10],
        "pagination": value.get("pagination", {}),
    }


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "tenant": TENANT, "error": {"code": code, "message": message}}


def _execute_read(
    spec: ToolSpec,
    *,
    query: str = "",
    record_id: str = "",
    start_date: str = "",
    end_date: str = "",
    page_size: int = 50,
    cursor: int = 0,
) -> dict[str, Any]:
    started = time.monotonic()
    scope = {
        "query": query,
        "record_id": record_id,
        "start_date": start_date,
        "end_date": end_date,
        "page_size": page_size,
        "cursor": cursor,
    }
    status = "error"
    error_class: str | None = None
    try:
        query = _validated_query(query)
        record_id = _validated_record_id(record_id)
        start_date = _validated_date(start_date, "start_date")
        end_date = _validated_date(end_date, "end_date")
        if start_date and end_date and start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        _RATE_LIMITER.acquire()
        with _EXECUTION_LOCK:
            raw = SynkedUPBrowser().read(spec, record_id=record_id)
        filtered = _filter_page(raw, spec.access_class)
        filtered = _local_filter(
            filtered,
            query=query,
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
            cursor=cursor,
        )
        payload = {"ok": True, "tenant": TENANT, "tool": spec.name, "data": filtered}
        status = "ok"
        return _bounded_payload(payload)
    except ReauthenticationRequired as exc:
        error_class = type(exc).__name__
        return _error_payload("reauthentication_required", "SynkedUP login or MFA is required from an authorized account owner.")
    except RateLimitExceeded as exc:
        error_class = type(exc).__name__
        return _error_payload("rate_limited", "The SynkedUP connector rate limit was reached. Try again shortly.")
    except (ValueError, SecurityViolation) as exc:
        error_class = type(exc).__name__
        return _error_payload("invalid_request", str(exc))
    except Exception as exc:
        error_class = type(exc).__name__
        return _error_payload("connector_unavailable", "The SynkedUP read connector is unavailable.")
    finally:
        _AUDIT.write(
            tool=spec.name,
            scope=scope,
            status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_class=error_class,
        )


@mcp.tool()
def synkedup_connection_health() -> dict[str, Any]:
    """Check the fixed CJS SynkedUP browser lane without returning business data."""
    started = time.monotonic()
    status = "error"
    error_class: str | None = None
    try:
        _RATE_LIMITER.acquire()
        with _EXECUTION_LOCK:
            SynkedUPBrowser().status()
        status = "ok"
        return {"ok": True, "tenant": TENANT, "browser": "reachable", "session": "authenticated"}
    except ReauthenticationRequired as exc:
        error_class = type(exc).__name__
        return _error_payload("reauthentication_required", "SynkedUP login or MFA is required from an authorized account owner.")
    except Exception as exc:
        error_class = type(exc).__name__
        return _error_payload("connector_unavailable", "The SynkedUP browser lane is unavailable.")
    finally:
        _AUDIT.write(
            tool="synkedup_connection_health",
            scope={},
            status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_class=error_class,
        )


@mcp.tool()
def synkedup_session_status() -> dict[str, Any]:
    """Return whether the fixed CJS SynkedUP browser session is authenticated."""
    return synkedup_connection_health()


def _make_read_tool(spec: ToolSpec):
    def read_tool(
        query: str = "",
        record_id: str = "",
        start_date: str = "",
        end_date: str = "",
        page_size: StrictPageSize = 50,
        cursor: StrictCursor = 0,
    ) -> dict[str, Any]:
        return _execute_read(
            spec,
            query=query,
            record_id=record_id,
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
            cursor=cursor,
        )

    read_tool.__name__ = spec.name
    read_tool.__qualname__ = spec.name
    read_tool.__doc__ = spec.description
    return read_tool


for _spec in TOOL_SPECS:
    mcp.tool(name=_spec.name, description=_spec.description)(_make_read_tool(_spec))


if __name__ == "__main__":
    transport = os.getenv("CJS_SYNKEDUP_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        host = os.getenv("CJS_SYNKEDUP_MCP_HOST", "127.0.0.1")
        if host not in ALLOWED_CDP_HOSTS:
            raise SystemExit("CJS SynkedUP MCP must bind to loopback")
        try:
            port = int(os.getenv("CJS_SYNKEDUP_MCP_PORT", "9342"))
        except ValueError as exc:
            raise SystemExit("CJS SynkedUP MCP port must be an integer") from exc
        if not 1024 <= port <= 65535:
            raise SystemExit("CJS SynkedUP MCP port is outside the allowed range")
        mcp.settings.host = host
        mcp.settings.port = port
    elif transport != "stdio":
        raise SystemExit("unsupported CJS SynkedUP MCP transport")
    mcp.run(transport=transport)
