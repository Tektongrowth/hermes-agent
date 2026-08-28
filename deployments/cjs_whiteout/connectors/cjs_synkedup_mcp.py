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
from typing import Annotated, Any, Callable

import websocket
from mcp.server.fastmcp import FastMCP
from pydantic import Field


TENANT = "cjs-landscape"
DEFAULT_BASE_URL = "https://app.synkedup.com"
DEFAULT_CDP_URL = "http://127.0.0.1:9341"
DEFAULT_AUDIT_PATH = "/var/log/cjs-synkedup/audit.jsonl"
DEFAULT_DASHBOARD_CACHE_PATH = "/var/lib/cjs-synkedup/cache/dashboard-jobs.json"
DASHBOARD_CACHE_MAX_AGE_SECONDS = 86_400
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
    ToolSpec("synkedup_labor_variance", "operations", "/reports/labor-hours", "Scan the jobs included in the current SynkedUP dashboard date range and compare estimated versus actual labor hours without payroll or labor cost fields. Leave query empty for all included jobs, or use query='status:completed' for completed jobs only. If the request also asks for job totals or profit, do not call this tool; call synkedup_job_costing instead because it already includes labor hours.", "operations", "/reports/labor-hours/{record_id}"),
    ToolSpec("synkedup_materials", "operations", "/materials", "Read material quantities and fulfillment state without costs.", "operations", "/materials/{record_id}"),
    ToolSpec("synkedup_equipment", "operations", "/equipment", "Read equipment assigned to work.", "operations", "/equipment/{record_id}"),
    ToolSpec("synkedup_subcontractors", "operations", "/subcontractors", "Read subcontractor assignments without payment fields.", "operations", "/subcontractors/{record_id}"),
    ToolSpec("synkedup_item_quantities", "operations", "/reports/item-quantities", "Compare estimated and actual item quantities without costs.", "operations", "/reports/item-quantities/{record_id}"),
    ToolSpec("synkedup_operations_dashboard", "operations", "/dashboard", "Read the operations dashboard.", "operations"),
    ToolSpec("synkedup_operations_reports", "operations", "/reports", "List and read non-financial operations reports.", "operations", "/reports/{record_id}"),
    ToolSpec("synkedup_exports", "operations", "/exports", "List documented read-only exports and same-host download links.", "operations"),
    ToolSpec("synkedup_job_costing", "financial", "/job-costing", "Scan the jobs included in the current SynkedUP dashboard date range and return estimated versus actual labor, job totals, and estimated/final net profit. Leave query empty for all included jobs, or use query='status:completed' for completed jobs only.", "financial", "/job-costing/{record_id}"),
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
DASHBOARD_LABOR_JOBS_SCRIPT = r"""
(() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const dates = Array.from(document.querySelectorAll('.main-dashboard input[date-range]')).map((input) => clean(input.value));
  const errorLoading = (document.body.innerText || '').includes('Error loading data');
  const headers = Array.from(document.querySelectorAll('.ant-collapse-header-text'));
  const label = headers.find((el) => clean(el.innerText || el.textContent) === 'Jobs included in this data:');
  const header = label && label.closest('[role="button"]');
  if (!header) return {
    ready: false,
    error_loading: errorLoading,
    date_start: dates[0] || '',
    date_end: dates[1] || '',
    jobs: []
  };
  if (header.getAttribute('aria-expanded') !== 'true') header.click();
  const container = header.closest('.ant-collapse-item');
  const jobs = Array.from((container && container.querySelectorAll('a[href]')) || []).map((link) => {
    const url = new URL(link.href, location.href);
    return {label: clean(link.innerText || link.textContent), href: url.href};
  }).filter((job) => job.label && job.href);
  return {
    ready: jobs.length > 0,
    error_loading: errorLoading,
    date_start: dates[0] || '',
    date_end: dates[1] || '',
    jobs
  };
})()
""".strip()
PROJECT_LABOR_SUMMARY_SCRIPT = r"""
(() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const number = clean(document.querySelector('#no') && document.querySelector('#no').value);
  const name = clean(document.querySelector('#name') && document.querySelector('#name').value);
  const status = document.querySelector('#project-status');
  const statusText = clean(status && status.selectedOptions && status.selectedOptions[0] && status.selectedOptions[0].text);
  const tab = document.querySelector('[data-target="#tab-analysis"]');
  const timeRoot = document.querySelector('section.time-analysis time-analysis');
  const summary = timeRoot && timeRoot.querySelector(':scope > .ant-row');
  const columns = summary ? Array.from(summary.children).map((el) => clean(el.innerText || el.textContent)) : [];
  const analysisText = clean(document.querySelector('#project-analysis') && document.querySelector('#project-analysis').innerText);
  const money = '(-?\\$-?[0-9,.]+)';
  const percent = '(-?[0-9,.]+%)';
  const revenue = analysisText.match(new RegExp('Revenue' + money + '\\s*estimated' + money + '\\s*actual' + money + '\\s*final', 'i'));
  const netProfit = analysisText.match(new RegExp('Net Profit' + percent + '\\s*estimated' + money + '\\s*estimated' + percent + '\\s*final' + money + '\\s*final', 'i'));
  if (number && tab && columns.length < 4) tab.click();
  return {
    ready: Boolean(number && columns.length >= 4),
    number,
    name,
    status: statusText,
    columns,
    financials: {
      estimated_total: (revenue && revenue[1]) || '',
      actual_total: (revenue && revenue[2]) || '',
      final_total: (revenue && revenue[3]) || '',
      estimated_net_profit_percent: (netProfit && netProfit[1]) || '',
      estimated_net_profit_dollars: (netProfit && netProfit[2]) || '',
      final_net_profit_percent: (netProfit && netProfit[3]) || '',
      final_net_profit_dollars: (netProfit && netProfit[4]) || ''
    }
  };
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

    def _connect_page_target(self, target: dict[str, Any]) -> None:
        if not isinstance(target.get("webSocketDebuggerUrl"), str):
            raise RuntimeError("browser page target is unavailable")
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

    def connect(self, expected_host: str) -> None:
        targets = [item for item in self._json_targets() if item.get("type") == "page"]
        matching = []
        for target in targets:
            try:
                if urllib.parse.urlsplit(str(target.get("url", ""))).hostname == expected_host:
                    matching.append(target)
            except ValueError:
                continue
        matching.sort(
            key=lambda item: (
                0
                if urllib.parse.urlsplit(str(item.get("url", ""))).path in {"", "/", "/dashboard"}
                else 1
            )
        )
        target = matching[0] if matching else (targets[0] if targets else None)
        if not target:
            raise RuntimeError("no browser page target available")
        self._connect_page_target(target)

    def connect_target(self, target_id: str) -> None:
        target = next(
            (
                item
                for item in self._json_targets()
                if item.get("type") == "page" and str(item.get("id", "")) == str(target_id)
            ),
            None,
        )
        if not target:
            raise RuntimeError("requested browser page target is unavailable")
        self._connect_page_target(target)

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
        if expression not in {
            EXTRACT_PAGE_SCRIPT,
            DASHBOARD_LABOR_JOBS_SCRIPT,
            PROJECT_LABOR_SUMMARY_SCRIPT,
            READY_STATE_SCRIPT,
            LOCATION_SCRIPT,
        }:
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


def _wait_for_constant(
    client: CDPClient,
    expression: str,
    predicate: Callable[[Any], bool],
    *,
    timeout: float = 25.0,
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = client.evaluate_constant(expression)
        if predicate(last):
            return last
        time.sleep(0.25)
    return last


def _hours_value(value: Any) -> float | None:
    match = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", str(value or ""))
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def _dashboard_cache_path() -> Path:
    return Path(
        os.getenv("CJS_SYNKEDUP_DASHBOARD_CACHE_PATH", DEFAULT_DASHBOARD_CACHE_PATH)
    )


def _write_dashboard_job_cache(dashboard: dict[str, Any]) -> None:
    jobs = dashboard.get("jobs") or []
    if not jobs or not dashboard.get("date_start") or not dashboard.get("date_end"):
        return
    payload = {
        "tenant": TENANT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "date_start": str(dashboard.get("date_start", "")),
        "date_end": str(dashboard.get("date_end", "")),
        "jobs": [
            {
                "label": str(job.get("label", ""))[:500],
                "href": str(job.get("href", ""))[:2000],
            }
            for job in jobs[:MAX_RESULT_ROWS]
            if isinstance(job, dict) and job.get("href")
        ],
    }
    if not payload["jobs"]:
        return
    path = _dashboard_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _load_dashboard_job_cache(
    *,
    origin: str,
    date_start: str,
    date_end: str,
    now: datetime | None = None,
    allow_range_mismatch: bool = False,
) -> dict[str, Any] | None:
    if (not date_start or not date_end) and not allow_range_mismatch:
        return None
    try:
        payload = json.loads(_dashboard_cache_path().read_text(encoding="utf-8"))
        captured_at = datetime.fromisoformat(str(payload.get("captured_at", "")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    age = ((now or datetime.now(timezone.utc)) - captured_at).total_seconds()
    if age < -300 or age > DASHBOARD_CACHE_MAX_AGE_SECONDS:
        return None
    if payload.get("tenant") != TENANT:
        return None
    cache_range_match = (
        payload.get("date_start") == date_start and payload.get("date_end") == date_end
    )
    if not cache_range_match and not allow_range_mismatch:
        return None
    jobs: list[dict[str, str]] = []
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            return None
        href = str(job.get("href", ""))
        try:
            _validate_dashboard_project_url(href, origin)
        except SecurityViolation:
            return None
        jobs.append({"label": str(job.get("label", ""))[:500], "href": href})
    if not jobs:
        return None
    return {
        "ready": True,
        "date_start": str(payload.get("date_start", "")),
        "date_end": str(payload.get("date_end", "")),
        "jobs": jobs[:MAX_RESULT_ROWS],
        "cache_captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "cache_range_match": cache_range_match,
    }


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
        controller = CDPClient()
        worker = CDPClient()
        target_id = ""
        try:
            controller.connect(self.host)
            created = controller.command("Target.createTarget", {"url": "about:blank", "background": True})
            target_id = str(created.get("targetId", ""))
            if not target_id:
                raise RuntimeError("could not create isolated browser read target")
            worker.connect_target(target_id)
            page = worker.navigate(url, self.origin)
        finally:
            worker.close()
            if target_id:
                try:
                    controller.command("Target.closeTarget", {"targetId": target_id})
                except Exception:
                    pass
            controller.close()
        if _looks_logged_out(page):
            raise ReauthenticationRequired("authenticated SynkedUP session is required")
        _validate_result_origin(str(page.get("url", "")), self.origin)
        return page

    def labor_variance(self, *, include_financial: bool = False) -> dict[str, Any]:
        dashboard_url = f"{self.base_url}/dashboard"
        controller = CDPClient()
        worker = CDPClient()
        target_id = ""
        dashboard: dict[str, Any] = {}
        dashboard_source = "live"
        alerts: list[str] = []
        rows: list[list[Any]] = []
        try:
            controller.connect(self.host)
            dashboard_page = controller.navigate(dashboard_url, self.origin)
            if _looks_logged_out(dashboard_page):
                raise ReauthenticationRequired("authenticated SynkedUP session is required")
            dashboard = _wait_for_constant(
                controller,
                DASHBOARD_LABOR_JOBS_SCRIPT,
                lambda value: (
                    isinstance(value, dict)
                    and bool(value.get("ready") or value.get("error_loading"))
                ),
            )
            if not isinstance(dashboard, dict) or not dashboard.get("ready"):
                page = controller.evaluate_constant(EXTRACT_PAGE_SCRIPT)
                if isinstance(page, dict) and _looks_logged_out(page):
                    raise ReauthenticationRequired("authenticated SynkedUP session is required")
                current_start = str((dashboard or {}).get("date_start", ""))
                current_end = str((dashboard or {}).get("date_end", ""))
                cached = _load_dashboard_job_cache(
                    origin=self.origin,
                    date_start=current_start,
                    date_end=current_end,
                )
                if cached is None:
                    cached = _load_dashboard_job_cache(
                        origin=self.origin,
                        date_start=current_start,
                        date_end=current_end,
                        allow_range_mismatch=True,
                    )
                if cached is None:
                    raise RuntimeError("dashboard labor job list did not load")
                dashboard = cached
                if cached.get("cache_range_match"):
                    dashboard_source = "same-range cache"
                    alerts.append(
                        "The dashboard job-costing panel was unavailable, so this scan used "
                        "the most recent job list captured for the same dashboard date range."
                    )
                else:
                    dashboard_source = "latest verified cache"
                    alerts.append(
                        "The dashboard job-costing panel was unavailable for the current date "
                        "range. This scan used the most recent verified dashboard job list and "
                        "re-read each listed project live."
                    )
            else:
                try:
                    _write_dashboard_job_cache(dashboard)
                except OSError:
                    pass

            jobs = dashboard.get("jobs") or []
            created = controller.command("Target.createTarget", {"url": "about:blank", "background": True})
            target_id = str(created.get("targetId", ""))
            if not target_id:
                raise RuntimeError("could not create isolated browser read target")
            worker.connect_target(target_id)
            for job in jobs[:MAX_RESULT_ROWS]:
                if not isinstance(job, dict):
                    continue
                label = str(job.get("label", ""))[:500]
                href = str(job.get("href", ""))
                expected_number = label.split(":", 1)[0].strip() if label else ""
                _validate_dashboard_project_url(href, self.origin)
                worker.command("Page.navigate", {"url": href, "transitionType": "typed"})
                project = _wait_for_constant(
                    worker,
                    PROJECT_LABOR_SUMMARY_SCRIPT,
                    lambda value: (
                        isinstance(value, dict)
                        and bool(value.get("ready"))
                        and (
                            not expected_number
                            or str(value.get("number", "")) == expected_number
                        )
                        and (
                            not include_financial
                            or bool((value.get("financials") or {}).get("final_total"))
                        )
                    ),
                )
                if not isinstance(project, dict) or not project.get("ready"):
                    alerts.append(f"Labor hours could not be read for {expected_number or label}.")
                    continue
                columns = project.get("columns") or []
                if len(columns) < 4:
                    alerts.append(f"Labor hours could not be read for {expected_number or label}.")
                    continue
                actual_hours = _hours_value(columns[1])
                estimated_hours = _hours_value(columns[-1])
                if actual_hours is None or estimated_hours is None:
                    alerts.append(f"Labor hours could not be parsed for {expected_number or label}.")
                    continue
                row: list[Any] = [
                    str(project.get("number", ""))[:100],
                    str(project.get("name", ""))[:500],
                    str(project.get("status", ""))[:100],
                    estimated_hours,
                    actual_hours,
                    round(actual_hours - estimated_hours, 2),
                ]
                if include_financial:
                    financials = project.get("financials") or {}
                    if not financials.get("final_total"):
                        alerts.append(
                            f"Job totals and profit could not be read for {expected_number or label}."
                        )
                    row.extend(
                        [
                            str(financials.get("estimated_total", ""))[:100],
                            str(financials.get("actual_total", ""))[:100],
                            str(financials.get("final_total", ""))[:100],
                            str(financials.get("estimated_net_profit_percent", ""))[:100],
                            str(financials.get("estimated_net_profit_dollars", ""))[:100],
                            str(financials.get("final_net_profit_percent", ""))[:100],
                            str(financials.get("final_net_profit_dollars", ""))[:100],
                        ]
                    )
                rows.append(row)
        finally:
            worker.close()
            if target_id:
                try:
                    controller.command("Target.closeTarget", {"targetId": target_id})
                except Exception:
                    pass
            controller.close()

        estimated_total = round(sum(float(row[3]) for row in rows), 2)
        actual_total = round(sum(float(row[4]) for row in rows), 2)
        headers = [
            "Job Number",
            "Name",
            "Status",
            "Estimated Hours",
            "Actual Hours",
            "Variance Hours",
        ]
        if include_financial:
            headers.extend(
                [
                    "Estimated Total",
                    "Actual Total",
                    "Final Total",
                    "Estimated Net Profit %",
                    "Estimated Net Profit $",
                    "Final Net Profit %",
                    "Final Net Profit $",
                ]
            )
        return {
            "url": dashboard_url,
            "title": (
                "SynkedUP Dashboard Job Costing"
                if include_financial
                else "SynkedUP Dashboard Labor Variance"
            ),
            "headings": ["Jobs included in this data"],
            "alerts": alerts,
            "fields": [
                {"label": "Dashboard start date", "value": str(dashboard.get("date_start", ""))},
                {"label": "Dashboard end date", "value": str(dashboard.get("date_end", ""))},
                {"label": "Dashboard job list source", "value": dashboard_source},
                {
                    "label": "Dashboard cache captured at",
                    "value": str(dashboard.get("cache_captured_at", "")),
                },
                {"label": "Jobs scanned", "value": len(rows)},
                {"label": "Estimated labor hours", "value": estimated_total},
                {"label": "Actual labor hours", "value": actual_total},
                {"label": "Labor variance hours", "value": round(actual_total - estimated_total, 2)},
            ],
            "tables": [
                {
                    "headers": headers,
                    "rows": rows,
                }
            ],
            "cards": [],
            "links": [],
        }

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


def _validate_dashboard_project_url(value: str, expected_origin: str) -> None:
    parsed = urllib.parse.urlsplit(value)
    if (
        f"{parsed.scheme}://{parsed.netloc}" != expected_origin
        or parsed.username
        or parsed.password
        or parsed.path != "/"
        or parsed.query
        or not re.fullmatch(r"!/projects/[0-9]{1,12}-[a-z0-9-]{1,160}", parsed.fragment)
    ):
        raise SecurityViolation("dashboard project link refused")



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


def _apply_dashboard_status_filter(payload: dict[str, Any], query: str) -> bool:
    """Apply an exact Status-column filter for dashboard labor/costing tools."""
    normalized = query.strip().casefold()
    if normalized.startswith("status:"):
        expected = normalized.split(":", 1)[1].strip()
    elif normalized.startswith("status="):
        expected = normalized.split("=", 1)[1].strip()
    else:
        return False
    if not expected:
        return False
    for table in payload.get("tables", []):
        headers = [str(value).strip().casefold() for value in table.get("headers", [])]
        if "status" not in headers:
            table["rows"] = []
            continue
        status_index = headers.index("status")
        table["rows"] = [
            row
            for row in table.get("rows", [])
            if len(row) > status_index
            and str(row[status_index]).strip().casefold() == expected
        ]
    return True


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
            browser = SynkedUPBrowser()
            if spec.name in {"synkedup_labor_variance", "synkedup_job_costing"}:
                raw = browser.labor_variance(include_financial=spec.name == "synkedup_job_costing")
                if record_id:
                    for table in raw.get("tables", []):
                        table["rows"] = [
                            row for row in table.get("rows", []) if row and str(row[0]) == record_id
                        ]
            else:
                raw = browser.read(spec, record_id=record_id)
        filtered = _filter_page(raw, spec.access_class)
        local_query = query
        if spec.name in {"synkedup_labor_variance", "synkedup_job_costing"}:
            if _apply_dashboard_status_filter(filtered, query):
                local_query = ""
        filtered = _local_filter(
            filtered,
            query=local_query,
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
