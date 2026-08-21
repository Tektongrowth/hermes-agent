from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


DEFAULT_CORE_PATH = Path("/opt/cjs-whiteout/connectors/cjs_assets_mcp.py")
API_VERSION = "1.1.60"
MAX_QUERY_LENGTH = 100
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
mcp = FastMCP("CJS Employee Project Data")


def _required_number(value: Any, label: str) -> float:
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        raise RuntimeError(f"Malformed {label} number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Malformed {label} number") from exc
    if not math.isfinite(parsed):
        raise RuntimeError(f"Malformed {label} number")
    return parsed


def _required_finite_result(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise RuntimeError(f"Malformed {label}")
    return value


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _required_nonnegative_int(value: Any, label: str) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit() and value == str(int(value)):
        return int(value)
    raise RuntimeError(f"Malformed {label} number")


def _safe_optional_number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _required_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"Malformed {label}")
    return value


def _required_timestamp(value: Any, label: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Malformed {label}")
    cleaned = value.strip()
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"Malformed {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"Malformed {label}")
    return cleaned, parsed


def _optional_scalar(value: Any, label: str) -> str | None:
    if value is None:
        return None
    cleaned = _text(value)
    if cleaned is None:
        raise RuntimeError(f"Malformed {label}")
    return cleaned


def _require_identity_match(
    source: dict[str, Any], key: str, expected: str | None, label: str
) -> None:
    if key not in source or source[key] is None:
        return
    actual = _text(source[key])
    if actual is None and isinstance(source[key], str) and not source[key].strip():
        actual = None
    elif actual is None:
        raise RuntimeError(f"Malformed {label} identity")
    if actual != expected:
        raise RuntimeError(f"{label} identity mismatch")


def _validated_limit(value: int, maximum: int = 100) -> int:
    return max(1, min(int(value), maximum))


def _validated_query(value: str | None) -> str:
    query = (value or "").strip()
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query cannot exceed {MAX_QUERY_LENGTH} characters")
    return query.casefold()


def _validated_dates(start_date: str, end_date: str, max_days: int = 366) -> tuple[date, date]:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD") from exc
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > max_days:
        raise ValueError(f"Date range cannot exceed {max_days} days")
    return start, end


def _backend_id(value: Any) -> int | None:
    if type(value) is int:
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit() and value == str(int(value)):
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _validated_job_id(value: Any) -> int:
    job_id = _backend_id(value)
    if job_id is None:
        raise ValueError("job_id must be a positive integer")
    return job_id


def _safe_address(row: dict[str, Any]) -> dict[str, str | None]:
    return {
        "address1": _text(row.get("address1")),
        "address2": _text(row.get("address2")),
        "city": _text(row.get("city")),
        "state": _text(row.get("state")),
        "zip": _text(row.get("zip")),
    }


def _safe_work_areas(rows: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _require_dict_rows(rows, "active-job work areas"):
        output.append(
            {
                "name": _optional_scalar(row.get("name"), "active-job work-area name"),
                "status": _optional_scalar(row.get("status"), "active-job work-area status"),
                "scheduled_event_count": _required_nonnegative_int(
                    row.get("calendarEventCount"), "active-job scheduled event count"
                ),
            }
        )
    return output


def _active_jobs_by_id(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    jobs: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Malformed active job row")
        if row.get("active") is not True:
            continue
        job_id = _backend_id(row.get("jobId"))
        if job_id is None:
            continue
        if job_id in jobs:
            raise RuntimeError("Duplicate active job identity")
        jobs[job_id] = row
    return jobs


def _active_job_ids(rows: list[dict[str, Any]]) -> set[int]:
    return set(_active_jobs_by_id(rows))


def _build_active_jobs(rows: list[dict[str, Any]], query: str, limit: int) -> dict[str, Any]:
    needle = _validated_query(query)
    jobs: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("active") is not True:
            continue
        job_id = _backend_id(row.get("jobId"))
        if job_id is None:
            continue
        haystack = " ".join(
            value
            for key in ("customerDisplayName", "jobName", "jobNumber", "type", "status")
            if (value := _text(row.get(key))) is not None
        ).casefold()
        if needle and needle not in haystack:
            continue
        jobs.append(
            {
                "job_id": job_id,
                "job_number": _text(row.get("jobNumber")),
                "job_name": _text(row.get("jobName")),
                "client_name": _text(row.get("customerDisplayName")),
                "job_type": _text(row.get("type")),
                "status": _text(row.get("status")),
                "address": _safe_address(row),
                "scheduled_event_count": _required_nonnegative_int(
                    row.get("calendarEventCount"), "active-job scheduled event count"
                ),
                "work_areas": _safe_work_areas(row.get("approvedWorkAreas")),
            }
        )
    jobs.sort(key=lambda row: (str(row.get("job_number") or ""), int(row["job_id"])), reverse=True)
    matching_count = len(jobs)
    jobs = jobs[: _validated_limit(limit, 250)]
    return {
        "matching_count": matching_count,
        "jobs": jobs,
        "returned": len(jobs),
        "source_scope": "SynkedUP active scheduling jobs",
        "mode": "read_only",
    }


def _build_item_quantity_variance(
    source_rows: list[dict[str, Any]],
    active_jobs_by_id: dict[int, dict[str, Any]],
    only_over_estimate: bool,
    limit: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for source in _require_dict_rows(source_rows, "item report"):
        project_id = _backend_id(source.get("projectId"))
        if project_id is None:
            raise RuntimeError("Malformed item report identity")
        active_job = active_jobs_by_id.get(project_id)
        if active_job is None:
            continue
        canonical_job_name = _text(active_job.get("jobName"))
        canonical_client_name = _text(active_job.get("customerDisplayName"))
        _require_identity_match(source, "projectName", canonical_job_name, "item report")
        _require_identity_match(
            source, "customerDisplayName", canonical_client_name, "item report"
        )
        estimated = _required_number(source.get("estimatedQuantity"), "item report")
        actual = _required_number(source.get("actualQuantity"), "item report")
        variance = _required_finite_result(actual - estimated, "item report variance")
        if only_over_estimate and variance <= 0:
            continue
        rows.append(
            {
                "job_id": project_id,
                "job_name": canonical_job_name,
                "client_name": canonical_client_name,
                "work_area": _optional_scalar(source.get("workareaName"), "item work area"),
                "item": _optional_scalar(source.get("name"), "item name"),
                "unit": _optional_scalar(source.get("units"), "item unit"),
                "estimated_quantity": estimated,
                "actual_quantity": actual,
                "quantity_variance": variance,
            }
        )
    rows.sort(key=lambda row: row["quantity_variance"], reverse=True)
    matching_count = len(rows)
    rows = rows[: _validated_limit(limit, 250)]
    return {
        "only_over_estimate": bool(only_over_estimate),
        "matching_count": matching_count,
        "items": rows,
        "returned": len(rows),
        "source_scope": "SynkedUP Items report filtered to active jobs",
        "scope_warning": "Items may include labor, fees, subcontractors, and physical materials.",
        "mode": "read_only",
    }


def _build_labor_hours_variance(
    source_rows: list[dict[str, Any]],
    active_jobs_by_id: dict[int, dict[str, Any]],
    job_id: int | None,
    only_over_estimate: bool,
    limit: int,
) -> dict[str, Any]:
    requested_job_id = _validated_job_id(job_id) if job_id is not None else None
    jobs: dict[int, dict[str, Any]] = {}
    for source in _require_dict_rows(source_rows, "labor report"):
        project_id = _backend_id(source.get("projectId"))
        if project_id is None:
            raise RuntimeError("Malformed labor report identity")
        active_job = active_jobs_by_id.get(project_id)
        if active_job is None or (requested_job_id is not None and project_id != requested_job_id):
            continue
        canonical_job_name = _text(active_job.get("jobName"))
        canonical_client_name = _text(active_job.get("customerDisplayName"))
        canonical_job_type = _text(active_job.get("type"))
        _require_identity_match(source, "jobName", canonical_job_name, "labor report")
        _require_identity_match(
            source, "customerDisplayName", canonical_client_name, "labor report"
        )
        _require_identity_match(source, "jobType", canonical_job_type, "labor report")
        estimated = _required_number(source.get("estimatedLaborQty"), "labor report")
        actual = _required_number(source.get("actualLaborQty"), "labor report")
        division = _optional_scalar(source.get("divisionName"), "labor division")
        row = jobs.setdefault(
            project_id,
            {
                "job_id": project_id,
                "job_name": canonical_job_name,
                "client_name": canonical_client_name,
                "job_type": canonical_job_type,
                "division": division,
                "workarea_count": 0,
                "estimated_labor_hours": 0.0,
                "actual_labor_hours": 0.0,
                "labor_hour_variance": 0.0,
            },
        )
        if row["division"] != division:
            raise RuntimeError("Labor report metadata mismatch")
        row["workarea_count"] += 1
        row["estimated_labor_hours"] = _required_finite_result(
            row["estimated_labor_hours"] + estimated,
            "labor report total",
        )
        row["actual_labor_hours"] = _required_finite_result(
            row["actual_labor_hours"] + actual,
            "labor report total",
        )
    output: list[dict[str, Any]] = []
    for row in jobs.values():
        row["estimated_labor_hours"] = round(row["estimated_labor_hours"], 2)
        row["actual_labor_hours"] = round(row["actual_labor_hours"], 2)
        row["labor_hour_variance"] = round(
            _required_finite_result(
                row["actual_labor_hours"] - row["estimated_labor_hours"],
                "labor report variance",
            ),
            2,
        )
        if only_over_estimate and row["labor_hour_variance"] <= 0:
            continue
        output.append(row)
    output.sort(key=lambda row: row["labor_hour_variance"], reverse=True)
    matching_count = len(output)
    output = output[: _validated_limit(limit, 100)]
    return {
        "only_over_estimate": bool(only_over_estimate),
        "matching_count": matching_count,
        "jobs": output,
        "returned": len(output),
        "source_scope": "SynkedUP labor hours filtered to active jobs; no labor costs",
        "mode": "read_only",
    }


def _event_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    return _require_dict_rows(data.get("events"), "schedule events")


def _build_schedule(
    data: dict[str, Any],
    active_jobs_by_id: dict[int, dict[str, Any]],
    query: str,
    limit: int,
) -> dict[str, Any]:
    needle = _validated_query(query)
    events: list[dict[str, Any]] = []
    for source in _event_rows(data):
        event_id = _backend_id(source.get("id"))
        job_id = _backend_id(source.get("jobId"))
        if event_id is None or job_id is None:
            raise RuntimeError("Malformed schedule event identity")
        active_job = active_jobs_by_id.get(job_id)
        if active_job is None:
            continue
        customer = source.get("customer")
        property_data = source.get("property")
        crew_member_ids = source.get("crewMemberIds")
        work_area_rows = source.get("workAreas")
        if not isinstance(customer, dict):
            raise RuntimeError("Malformed schedule event customer")
        if not isinstance(property_data, dict):
            raise RuntimeError("Malformed schedule event property")
        if not isinstance(crew_member_ids, list):
            raise RuntimeError("Malformed schedule event crew members")
        if not isinstance(work_area_rows, list):
            raise RuntimeError("Malformed schedule event work areas")

        canonical_job_number = _text(active_job.get("jobNumber"))
        canonical_job_name = _text(active_job.get("jobName"))
        canonical_client_name = _text(active_job.get("customerDisplayName"))
        canonical_customer_id = _backend_id(active_job.get("customerId"))
        if canonical_customer_id is None or _backend_id(customer.get("id")) != canonical_customer_id:
            raise RuntimeError("schedule event identity mismatch")
        _require_identity_match(source, "jobName", canonical_job_name, "schedule event")
        _require_identity_match(source, "jobNumber", canonical_job_number, "schedule event")
        _require_identity_match(customer, "displayName", canonical_client_name, "schedule event")
        canonical_address = _safe_address(active_job)
        for key, expected in canonical_address.items():
            _require_identity_match(property_data, key, expected, "schedule event")

        start_utc, start = _required_timestamp(source.get("startUtc"), "schedule event timestamp")
        end_utc, end = _required_timestamp(source.get("endUtc"), "schedule event timestamp")
        if end < start:
            raise RuntimeError("Malformed schedule event timestamp")
        all_day = _required_bool(source.get("allDay"), "schedule event all-day flag")
        work_areas = []
        for row in _require_dict_rows(work_area_rows, "schedule event work areas"):
            work_areas.append(
                {
                    "name": _optional_scalar(row.get("name"), "schedule event work-area name"),
                    "status": _optional_scalar(row.get("status"), "schedule event work-area status"),
                    "estimated_hours": _required_number(
                        row.get("estimatedHours"), "schedule event work-area"
                    ),
                }
            )
        haystack = " ".join(
            value
            for value in (canonical_job_name, canonical_job_number, canonical_client_name)
            if value is not None
        ).casefold()
        if needle and needle not in haystack:
            continue
        events.append(
            {
                "event_id": event_id,
                "job_id": job_id,
                "job_number": canonical_job_number,
                "job_name": canonical_job_name,
                "client_name": canonical_client_name,
                "address": canonical_address,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "all_day": all_day,
                "crew_foreman_email": _optional_scalar(
                    source.get("crewForemanEmail"), "schedule event crew foreman"
                ),
                "crew_member_count": len(crew_member_ids),
                "work_areas": work_areas,
            }
        )
    events.sort(key=lambda row: str(row.get("start_utc") or ""))
    matching_count = len(events)
    events = events[: _validated_limit(limit, 250)]
    return {
        "matching_count": matching_count,
        "events": events,
        "returned": len(events),
        "source_scope": "SynkedUP schedule events for active jobs",
        "mode": "read_only",
    }


def _require_dict_payload(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Malformed {label} response")
    return value


def _require_list_payload(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"Malformed {label} response")
    return value


def _require_dict_rows(value: Any, label: str) -> list[dict[str, Any]]:
    rows = _require_list_payload(value, label)
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeError(f"Malformed {label} row")
    return rows


def _validated_calendar_event_ids(rows: Any, job_id: int) -> set[int]:
    event_ids: set[int] = set()
    for row in _require_list_payload(rows, "calendar events"):
        if not isinstance(row, dict):
            raise RuntimeError("Malformed calendar event row")
        event_id = _backend_id(row.get("id"))
        row_job_id = _backend_id(row.get("jobId"))
        if event_id is None or row_job_id != job_id:
            raise RuntimeError("Calendar event identity mismatch")
        event_ids.add(event_id)
    return event_ids


def _primary_contact(
    rows: Any,
    value_key: str,
    expected_customer_id: int,
) -> tuple[str | None, str | None]:
    available = []
    for row in _require_list_payload(rows, f"customer {value_key} contacts"):
        if not isinstance(row, dict):
            raise RuntimeError("Malformed customer contact row")
        if _backend_id(row.get("customerId")) != expected_customer_id:
            raise RuntimeError("Customer contact identity mismatch")
        if type(row.get("removed")) is not bool or type(row.get("isPrimary")) is not bool:
            raise RuntimeError("Malformed customer contact state")
        if row["removed"] is False:
            available.append(row)
    if not available:
        return None, None
    selected = next((row for row in available if row.get("isPrimary")), available[0])
    return _text(selected.get(value_key)), _text(selected.get("description"))


def _build_job_brief(
    active_job: dict[str, Any],
    details: dict[str, Any],
    events: list[dict[str, Any]],
    customer: dict[str, Any],
    allowed_event_ids: set[int],
) -> dict[str, Any]:
    job_id = _backend_id(active_job.get("jobId"))
    customer_id = _backend_id(active_job.get("customerId"))
    if job_id is None or _backend_id(details.get("id")) != job_id:
        raise RuntimeError("SynkedUP job details identity mismatch")
    if customer_id is None or _backend_id(customer.get("id")) != customer_id:
        raise RuntimeError("SynkedUP customer identity mismatch")
    canonical_job_number = _text(active_job.get("jobNumber"))
    canonical_job_name = _text(active_job.get("jobName"))
    canonical_client_name = _text(active_job.get("customerDisplayName"))
    canonical_job_type = _text(active_job.get("type"))
    _require_identity_match(details, "no", canonical_job_number, "SynkedUP job details")
    _require_identity_match(details, "name", canonical_job_name, "SynkedUP job details")
    _require_identity_match(details, "type", canonical_job_type, "SynkedUP job details")
    _require_identity_match(customer, "displayName", canonical_client_name, "SynkedUP customer")

    validated_events: list[dict[str, Any]] = []
    for event in _require_dict_rows(events, "job schedule events"):
        event_id = _backend_id(event.get("id"))
        event_job_id = _backend_id(event.get("jobId")) if "jobId" in event else job_id
        if event_id is None or event_id not in allowed_event_ids or event_job_id != job_id:
            raise RuntimeError("SynkedUP schedule event identity mismatch")
        validated_events.append(event)
    phone, phone_label = _primary_contact(
        customer.get("phones"), "number", customer_id
    )
    email, email_label = _primary_contact(
        customer.get("emails"), "address", customer_id
    )
    work_areas = []
    for row in _require_dict_rows(details.get("workAreas"), "job work areas"):
        work_areas.append(
            {
                "name": _optional_scalar(row.get("name"), "job work-area name"),
                "status": _optional_scalar(row.get("status"), "job work-area status"),
            }
        )
    scheduled_events = []
    for row in validated_events:
        start_utc, start = _required_timestamp(
            row.get("startUtc"), "job schedule event timestamp"
        )
        end_utc, end = _required_timestamp(row.get("endUtc"), "job schedule event timestamp")
        if end < start:
            raise RuntimeError("Malformed job schedule event timestamp")
        work_area_names = row.get("workAreaNames")
        if not isinstance(work_area_names, list):
            raise RuntimeError("Malformed job schedule event work areas")
        scheduled_events.append(
            {
                "start_utc": start_utc,
                "end_utc": end_utc,
                "all_day": _required_bool(
                    row.get("allDay"), "job schedule event all-day flag"
                ),
                "crew_foreman_email": _optional_scalar(
                    row.get("crewForemanEmail"), "job schedule event crew foreman"
                ),
                "daily_labor_hours": _required_number(
                    row.get("dailyManhours"), "job schedule event"
                ),
                "work_area_names": [
                    _optional_scalar(name, "job schedule event work-area name")
                    for name in work_area_names
                ],
            }
        )
    return {
        "job": {
            "job_id": job_id,
            "job_number": canonical_job_number,
            "job_name": canonical_job_name,
            "client_name": canonical_client_name,
            "job_type": canonical_job_type,
            "status": _text(active_job.get("status") or details.get("status")),
            "address": _safe_address(active_job),
            "work_areas": work_areas,
        },
        "contact": {
            "primary_phone": phone,
            "primary_phone_label": phone_label,
            "primary_email": email,
            "primary_email_label": email_label,
        },
        "scheduled_events": scheduled_events,
        "mode": "read_only",
    }


@lru_cache(maxsize=1)
def _core() -> Any:
    core_path = Path(os.environ.get("CJS_ASSETS_CORE_PATH", str(DEFAULT_CORE_PATH)))
    spec = importlib.util.spec_from_file_location("cjs_assets_core", core_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {core_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _allowed_employee_path(path: str) -> bool:
    if path == "/api/web/scheduling/jobs/v2":
        return True
    patterns = (
        r"/api/web/jobs/[1-9]\d*/details",
        r"/api/web/scheduling/events/job/[1-9]\d*",
        r"/api/web/calendar-events/job/[1-9]\d*",
        r"/api/customers/[1-9]\d*",
        r"/api/web/scheduling/events/daterange\?startDate=\d{4}-\d{2}-\d{2}&endDate=\d{4}-\d{2}-\d{2}",
        r"/api/web/datacenter/items-filtered/\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}",
        r"/api/web/datacenter/man-hours/\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}",
    )
    return any(re.fullmatch(pattern, path) for pattern in patterns)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _read_json_without_redirects(request: urllib.request.Request) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=45) as response:
        content_type = response.headers.get_content_type()
        if content_type != "application/json":
            raise RuntimeError("SynkedUP returned a non-JSON response")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise RuntimeError("SynkedUP response exceeded the safe size limit")
        return json.loads(payload.decode("utf-8"))


async def _employee_get(path: str) -> Any:
    if not _allowed_employee_path(path):
        raise ValueError("Endpoint is not on the employee read-only allowlist")
    core = _core()
    headers = {
        "Authorization": "Bearer " + await core._token(),
        "Accept": "application/json",
        "User-Agent": "CJS-Employee-ReadOnly-Connector/2.0",
        "SynkedUP-Version": API_VERSION,
        "Referer": "https://app.synkedup.com/",
    }
    request = urllib.request.Request(core.API_ROOT + path, headers=headers, method="GET")
    try:
        return _read_json_without_redirects(request)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
    headers["Authorization"] = "Bearer " + await core._reauthenticate_synkedup()
    retry = urllib.request.Request(core.API_ROOT + path, headers=headers, method="GET")
    return _read_json_without_redirects(retry)


async def _active_job_rows() -> list[dict[str, Any]]:
    return _require_dict_rows(
        await _employee_get("/api/web/scheduling/jobs/v2"), "active jobs"
    )


async def _require_active_job(job_id: int | str) -> dict[str, Any]:
    validated = _validated_job_id(job_id)
    row = _active_jobs_by_id(await _active_job_rows()).get(validated)
    if row is None:
        raise ValueError("job_id is not an active SynkedUP job")
    return row


@mcp.tool()
async def synkedup_active_jobs(query: str = "", limit: int = 50) -> dict[str, Any]:
    """List or search current active jobs with client names, job numbers, addresses, and work-area status.

    Search matches client name, job name, job number, job type, or status. This
    tool returns no prices, costs, margins, payroll, or QuickBooks data.
    """
    return _build_active_jobs(await _active_job_rows(), query=query, limit=limit)


@mcp.tool()
async def synkedup_job_brief(job_id: int) -> dict[str, Any]:
    """Return an employee-safe brief for one active job.

    Includes client contact information, job address, work-area status, and
    scheduled dates. Free-text notes and protected financial fields are omitted.
    """
    active_job = await _require_active_job(job_id)
    validated = _validated_job_id(job_id)
    customer_id = _validated_job_id(active_job.get("customerId"))
    details = _require_dict_payload(
        await _employee_get(f"/api/web/jobs/{validated}/details"), "job details"
    )
    events = _require_list_payload(
        await _employee_get(f"/api/web/scheduling/events/job/{validated}"),
        "job schedule events",
    )
    calendar_events = await _employee_get(
        f"/api/web/calendar-events/job/{validated}"
    )
    allowed_event_ids = _validated_calendar_event_ids(calendar_events, validated)
    customer = _require_dict_payload(
        await _employee_get(f"/api/customers/{customer_id}"), "customer"
    )
    return _build_job_brief(
        active_job,
        details,
        events,
        customer,
        allowed_event_ids=allowed_event_ids,
    )


@mcp.tool()
async def synkedup_schedule(
    start_date: str,
    end_date: str,
    query: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """List safe schedule events for active jobs in a date range.

    Search matches client name, job name, or job number. The response includes
    dates, work areas, and crew counts without free-text notes or financial data.
    """
    start, end = _validated_dates(start_date, end_date, max_days=92)
    jobs = await _active_job_rows()
    path = (
        "/api/web/scheduling/events/daterange?"
        + urllib.parse.urlencode({"startDate": start.isoformat(), "endDate": end.isoformat()})
    )
    data = _require_dict_payload(await _employee_get(path), "schedule")
    result = _build_schedule(data, _active_jobs_by_id(jobs), query=query, limit=limit)
    result["range"] = {"start": start.isoformat(), "end": end.isoformat()}
    return result


@mcp.tool()
async def synkedup_labor_hours_variance(
    start_date: str,
    end_date: str,
    job_id: int | None = None,
    only_over_estimate: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Return estimated versus actual labor hours for active jobs.

    This tool intentionally omits labor cost, materials cost, equipment cost,
    subcontractor cost, pricing, margins, payroll, and QuickBooks data.
    """
    start, end = _validated_dates(start_date, end_date)
    jobs = await _active_job_rows()
    rows = _require_dict_rows(
        await _employee_get(
            f"/api/web/datacenter/man-hours/{start.isoformat()}/{end.isoformat()}"
        ),
        "labor hours",
    )
    result = _build_labor_hours_variance(
        rows,
        active_jobs_by_id=_active_jobs_by_id(jobs),
        job_id=job_id,
        only_over_estimate=only_over_estimate,
        limit=limit,
    )
    result["range"] = {"start": start.isoformat(), "end": end.isoformat()}
    return result


@mcp.tool()
async def synkedup_item_quantity_variance(
    start_date: str,
    end_date: str,
    only_over_estimate: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Return SynkedUP item quantity variance for active jobs with client names.

    The Items report can include physical materials, labor, fees, subcontractors,
    and other line items. It is not a physical-material-only report. No prices,
    costs, margins, payroll, or QuickBooks data are returned.
    """
    start, end = _validated_dates(start_date, end_date)
    jobs = await _active_job_rows()
    rows = _require_dict_rows(
        await _employee_get(
            f"/api/web/datacenter/items-filtered/{start.isoformat()}/{end.isoformat()}"
        ),
        "item quantities",
    )
    result = _build_item_quantity_variance(
        rows,
        active_jobs_by_id=_active_jobs_by_id(jobs),
        only_over_estimate=only_over_estimate,
        limit=limit,
    )
    result["range"] = {"start": start.isoformat(), "end": end.isoformat()}
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
