from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployments.cjs_whiteout.connectors import cjs_synkedup_mcp as synkedup


EXPECTED_DOMAINS = {"reference", "sales", "operations", "financial"}
FORBIDDEN_ARGUMENTS = {
    "url",
    "path",
    "host",
    "origin",
    "selector",
    "script",
    "javascript",
    "cdp",
    "tenant",
    "account",
    "session",
    "secret",
    "cookie",
    "token",
}
MUTATION_TOOL_VERBS = {
    "create",
    "edit",
    "delete",
    "save",
    "send",
    "approve",
    "schedule",
    "pay",
    "refund",
    "void",
    "update",
}


def test_catalog_is_complete_unique_and_split_into_read_classes():
    assert len(synkedup.TOOL_SPECS) >= 40
    assert len(synkedup.ALL_TOOL_NAMES) == len(set(synkedup.ALL_TOOL_NAMES))
    assert {spec.domain for spec in synkedup.TOOL_SPECS} == EXPECTED_DOMAINS
    assert set(synkedup.TOOL_NAMES_BY_ACCESS_CLASS) == EXPECTED_DOMAINS
    assert all(synkedup.TOOL_NAMES_BY_ACCESS_CLASS.values())

    names = set(synkedup.ALL_TOOL_NAMES)
    required = {
        "synkedup_customers",
        "synkedup_leads",
        "synkedup_estimates",
        "synkedup_proposals",
        "synkedup_jobs",
        "synkedup_job_briefs",
        "synkedup_schedules",
        "synkedup_job_notes",
        "synkedup_job_attachments",
        "synkedup_time_entries",
        "synkedup_labor_variance",
        "synkedup_materials",
        "synkedup_equipment",
        "synkedup_job_costing",
        "synkedup_margins",
        "synkedup_invoices",
        "synkedup_payment_status",
        "synkedup_employees",
        "synkedup_crews",
        "synkedup_divisions",
        "synkedup_items",
        "synkedup_vendors",
        "synkedup_operations_reports",
        "synkedup_financial_reports",
        "synkedup_exports",
    }
    assert required <= names


def test_tool_names_do_not_expose_mutation_actions():
    for name in synkedup.ALL_TOOL_NAMES:
        segments = set(name.casefold().split("_"))
        assert not segments & MUTATION_TOOL_VERBS


def test_fastmcp_schema_exposes_only_bounded_read_filters():
    tools = synkedup.mcp._tool_manager._tools
    assert set(tools) == set(synkedup.ALL_TOOL_NAMES)
    for name, tool in tools.items():
        properties = set((tool.parameters or {}).get("properties", {}))
        assert not properties & FORBIDDEN_ARGUMENTS, name
        if name not in synkedup.HEALTH_TOOL_NAMES:
            assert properties == {
                "query",
                "record_id",
                "start_date",
                "end_date",
                "page_size",
                "cursor",
            }
            assert tool.parameters["properties"]["page_size"]["maximum"] == 100
            assert tool.parameters["properties"]["cursor"]["minimum"] == 0


def test_every_catalog_route_is_same_origin_relative_and_non_mutating():
    origin = "https://app.synkedup.test"
    for spec in synkedup.TOOL_SPECS:
        for route in (spec.route, spec.detail_route):
            if route is None:
                continue
            assert route.startswith("/")
            assert "://" not in route
            test_route = route.replace("{record_id}", "MS-044")
            synkedup._validate_navigation_url(origin + test_route, origin)


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:9341",
        "http://10.0.0.5:9341",
        "https://127.0.0.1:9341",
        "http://user:pass@127.0.0.1:9341",
        "http://127.0.0.1:9341/json",
        "http://127.0.0.1:9341?target=evil",
    ],
)
def test_cdp_endpoint_must_be_plain_loopback_origin(url):
    with pytest.raises(synkedup.SecurityViolation):
        synkedup.CDPClient(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://app.synkedup.test",
        "https://user:pass@app.synkedup.test",
        "https://app.synkedup.test/path",
        "https://app.synkedup.test?tenant=other",
        "https://app.synkedup.test#fragment",
    ],
)
def test_base_url_is_fixed_https_origin(url):
    with pytest.raises(synkedup.SecurityViolation):
        synkedup._validate_base_url(url)


def test_navigation_blocks_cross_origin_and_mutation_paths():
    origin = "https://app.synkedup.test"
    with pytest.raises(synkedup.SecurityViolation):
        synkedup._validate_navigation_url("https://evil.test/jobs", origin)
    for path in (
        "/jobs/new",
        "/jobs/MS-044/edit",
        "/jobs/MS-044/delete",
        "/invoices/123/send",
        "/payments/123/refund",
        "/jobs?mode=approve",
    ):
        with pytest.raises(synkedup.SecurityViolation):
            synkedup._validate_navigation_url(origin + path, origin)


def test_user_filters_reject_url_script_selector_and_shell_injection():
    for query in (
        "https://evil.test",
        "javascript:alert(1)",
        "<script>alert(1)</script>",
        "$(touch /tmp/pwned)",
        "x; rm -rf /",
        "{selector:#save}",
    ):
        with pytest.raises(ValueError):
            synkedup._validated_query(query)
    for record_id in (
        "../other-tenant",
        "https://evil.test",
        "MS-044/edit",
        "x?mode=delete",
        "x;shutdown",
    ):
        with pytest.raises(ValueError):
            synkedup._validated_record_id(record_id)


def test_runtime_evaluation_accepts_only_server_constants():
    client = synkedup.CDPClient("http://127.0.0.1:9341")
    with pytest.raises(synkedup.SecurityViolation):
        client.evaluate_constant("document.cookie")
    with pytest.raises(synkedup.SecurityViolation):
        client.command("Network.getAllCookies")
    with pytest.raises(synkedup.SecurityViolation):
        client.command("Runtime.callFunctionOn")


def test_fetch_interception_blocks_business_mutations_and_allows_reads():
    class FakeSocket:
        def __init__(self):
            self.messages = []

        def send(self, value):
            self.messages.append(json.loads(value))

    client = synkedup.CDPClient("http://127.0.0.1:9341")
    client._socket = FakeSocket()
    client._continue_or_block(
        {"params": {"requestId": "post-1", "request": {"method": "POST"}}}
    )
    client._continue_or_block(
        {"params": {"requestId": "get-1", "request": {"method": "GET"}}}
    )
    assert client._socket.messages[0]["method"] == "Fetch.failRequest"
    assert client._socket.messages[1]["method"] == "Fetch.continueRequest"


def _page_fixture():
    return {
        "url": "https://app.synkedup.test/jobs/MS-044",
        "title": "Job MS-044",
        "headings": ["Steve Cherney Landscape Renovation"],
        "alerts": [],
        "fields": [
            {"label": "Status", "value": "Sold and unscheduled"},
            {"label": "Margin", "value": "28%"},
            {"label": "Actual Cost", "value": "$10,000"},
            {"label": "Estimated Hours", "value": "240"},
        ],
        "tables": [
            {
                "headers": ["Job", "Price", "Estimated Hours", "Cost"],
                "rows": [["MS-044", "$20,000", "240", "$10,000"]],
            }
        ],
        "cards": ["Crew A assigned", "Margin 28 percent", "Invoice balance $5,000"],
        "links": [
            {"label": "Job PDF", "href": "https://app.synkedup.test/jobs/MS-044.pdf"},
            {"label": "External PDF", "href": "https://evil.test/stolen.pdf"},
        ],
    }


def test_operations_response_removes_financial_fields_before_returning():
    filtered = synkedup._filter_page(_page_fixture(), "operations")
    encoded = json.dumps(filtered).casefold()
    for forbidden in ("margin", "actual cost", "$10,000", "$20,000", "invoice", "$5,000"):
        assert forbidden not in encoded
    assert "estimated hours" in encoded
    assert "crew a assigned" in encoded
    assert "evil.test" not in encoded
    assert "ms-044.pdf" in encoded


def test_financial_response_retains_financial_fields():
    filtered = synkedup._filter_page(_page_fixture(), "financial")
    encoded = json.dumps(filtered).casefold()
    assert "margin" in encoded
    assert "actual cost" in encoded
    assert "$20,000" in encoded
    assert "invoice balance" in encoded
    assert "evil.test" not in encoded


def test_sales_response_keeps_price_but_hides_cost_margin_and_payment():
    filtered = synkedup._filter_page(_page_fixture(), "sales")
    encoded = json.dumps(filtered).casefold()
    assert "price" in encoded
    assert "$20,000" in encoded
    assert "margin" not in encoded
    assert "actual cost" not in encoded
    assert "invoice" not in encoded


def test_login_detection_handles_login_routes_and_page_markers():
    assert synkedup._looks_logged_out({"url": "https://app.synkedup.test/login"})
    assert synkedup._looks_logged_out(
        {"url": "https://auth.vendor.test/continue", "headings": ["Sign in to continue"]}
    )
    assert not synkedup._looks_logged_out(
        {"url": "https://app.synkedup.test/jobs", "headings": ["All Jobs"]}
    )


def test_audit_log_hashes_record_id_and_never_writes_query_or_page_content(tmp_path):
    path = tmp_path / "audit.jsonl"
    logger = synkedup.AuditLogger(path)
    logger.write(
        tool="synkedup_jobs",
        scope={
            "record_id": "MS-044-private",
            "query": "Steve Cherney secret query",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "page_size": 25,
            "cursor": 0,
            "page_content": "private customer notes",
        },
        status="ok",
        duration_ms=12,
    )
    text = path.read_text(encoding="utf-8")
    event = json.loads(text)
    assert "MS-044-private" not in text
    assert "Steve Cherney" not in text
    assert "private customer notes" not in text
    assert event["tenant"] == "cjs-landscape"
    assert event["scope"]["query_present"] is True
    assert len(event["scope"]["record_id_hash"]) == 12


def test_execute_read_returns_structured_reauth_error_and_audits(monkeypatch, tmp_path):
    class FakeBrowser:
        def read(self, spec, *, record_id=""):
            raise synkedup.ReauthenticationRequired("do not log this detail")

    monkeypatch.setattr(synkedup, "SynkedUPBrowser", FakeBrowser)
    monkeypatch.setattr(synkedup, "_AUDIT", synkedup.AuditLogger(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(synkedup._RATE_LIMITER, "acquire", lambda: None)
    result = synkedup._execute_read(synkedup.TOOL_SPEC_BY_NAME["synkedup_jobs"])
    assert result["ok"] is False
    assert result["error"]["code"] == "reauthentication_required"
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "do not log this detail" not in audit
    assert "ReauthenticationRequired" in audit


def test_local_filters_apply_query_dates_and_cursor():
    payload = {
        "tables": [
            {
                "headers": ["Job", "Date"],
                "rows": [
                    ["MS-001 Alpha", "2026-01-01"],
                    ["MS-002 Beta", "2026-02-01"],
                    ["MS-003 Beta", "2026-03-01"],
                ],
            }
        ],
        "cards": [],
        "fields": [],
    }
    result = synkedup._local_filter(
        payload,
        query="Beta",
        start_date="2026-02-01",
        end_date="2026-03-31",
        page_size=1,
        cursor=1,
    )
    assert result["tables"][0]["rows"] == [["MS-003 Beta", "2026-03-01"]]
    assert result["pagination"] == {
        "cursor": 1,
        "page_size": 1,
        "total_rows": 2,
        "has_more": False,
        "next_cursor": None,
    }


def test_local_filter_reports_more_rows_and_next_cursor():
    payload = {
        "tables": [{"headers": ["Item"], "rows": [["A"], ["B"], ["C"]]}],
        "cards": [],
        "fields": [],
    }
    result = synkedup._local_filter(
        payload,
        query="",
        start_date="",
        end_date="",
        page_size=2,
        cursor=0,
    )
    assert result["tables"][0]["rows"] == [["A"], ["B"]]
    assert result["pagination"]["total_rows"] == 3
    assert result["pagination"]["has_more"] is True
    assert result["pagination"]["next_cursor"] == 2


def test_workarea_unit_extraction_uses_full_dom_text():
    assert "quantityCell.textContent" in synkedup.WORKAREA_ITEMS_SCRIPT
    assert "quantityCell.innerText" not in synkedup.WORKAREA_ITEMS_SCRIPT


def test_sold_material_pagination_reuses_same_recent_live_scan(monkeypatch):
    calls = []
    raw = {
        "url": "https://app.synkedup.com/#!/projects/1-test",
        "title": "Sold job materials AY-859",
        "headings": ["AY-859"],
        "alerts": [],
        "tables": [{
            "headers": ["Work area", "Item", "Estimated quantity", "Source unit"],
            "rows": [["Area", f"Item {i}", "1", "Each"] for i in range(3)],
        }],
        "fields": [],
        "cards": [],
        "links": [],
    }

    def sold_job_materials(_self, job_number):
        calls.append(job_number)
        return raw

    monkeypatch.setattr(synkedup.SynkedUPBrowser, "sold_job_materials", sold_job_materials)
    monkeypatch.setattr(synkedup, "_RATE_LIMITER", SimpleNamespace(acquire=lambda: None))
    monkeypatch.setattr(synkedup, "_AUDIT", SimpleNamespace(write=lambda **_kwargs: None))
    synkedup._SOLD_MATERIAL_CACHE.clear()
    spec = synkedup.TOOL_SPEC_BY_NAME["synkedup_sold_job_materials"]

    first = synkedup._execute_read(spec, query="AY-859", page_size=2, cursor=0)
    second = synkedup._execute_read(spec, query="AY-859", page_size=2, cursor=2)

    assert calls == ["AY-859"]
    assert first["data"]["pagination"]["has_more"] is True
    assert second["data"]["tables"][0]["rows"] == [["Area", "Item 2", "1", "Each"]]


def test_dashboard_project_links_are_same_origin_bounded_hash_routes():
    origin = "https://app.synkedup.test"
    synkedup._validate_dashboard_project_url(
        "https://app.synkedup.test/#!/projects/497695-outcropping-wall",
        origin,
    )
    for url in (
        "https://evil.test/#!/projects/497695-outcropping-wall",
        "https://app.synkedup.test/#!/projects/new",
        "https://app.synkedup.test/#!/projects/497695-outcropping-wall/edit",
        "https://app.synkedup.test/?next=evil#!/projects/497695-outcropping-wall",
    ):
        with pytest.raises(synkedup.SecurityViolation):
            synkedup._validate_dashboard_project_url(url, origin)


def test_labor_variance_uses_dashboard_scan_and_filters_record_id(monkeypatch, tmp_path):
    raw = {
        "url": "https://app.synkedup.test/dashboard#!/",
        "title": "SynkedUP Dashboard Labor Variance",
        "headings": ["Jobs included in this data"],
        "alerts": [],
        "fields": [],
        "tables": [
            {
                "headers": ["Job Number", "Name", "Status", "Estimated Hours", "Actual Hours", "Variance Hours"],
                "rows": [
                    ["AY-659", "Landscape Renovation", "Completed", 379.0, 580.37, 201.37],
                    ["AY-741", "Landscape Design", "Completed", 221.0, 153.63, -67.37],
                ],
            }
        ],
        "cards": [],
        "links": [],
    }

    class FakeBrowser:
        def labor_variance(self, *, include_financial=False):
            assert include_financial is False
            return json.loads(json.dumps(raw))

        def read(self, spec, *, record_id=""):
            raise AssertionError("generic page read must not be used for labor variance")

    monkeypatch.setattr(synkedup, "SynkedUPBrowser", FakeBrowser)
    monkeypatch.setattr(synkedup, "_AUDIT", synkedup.AuditLogger(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(synkedup._RATE_LIMITER, "acquire", lambda: None)
    result = synkedup._execute_read(
        synkedup.TOOL_SPEC_BY_NAME["synkedup_labor_variance"],
        record_id="AY-741",
    )
    assert result["ok"] is True
    assert result["data"]["tables"][0]["rows"] == [
        ["AY-741", "Landscape Design", "Completed", "221.0", "153.63", "-67.37"]
    ]
    encoded = json.dumps(result).casefold()
    assert "actual hours" in encoded
    assert "actual cost" not in encoded


def test_job_costing_uses_dashboard_scan_with_financial_fields(monkeypatch, tmp_path):
    raw = {
        "url": "https://app.synkedup.test/dashboard#!/",
        "title": "SynkedUP Dashboard Job Costing",
        "headings": ["Jobs included in this data"],
        "alerts": [],
        "fields": [],
        "tables": [
            {
                "headers": [
                    "Job Number",
                    "Name",
                    "Status",
                    "Estimated Hours",
                    "Actual Hours",
                    "Variance Hours",
                    "Estimated Total",
                    "Actual Total",
                    "Final Total",
                    "Estimated Net Profit %",
                    "Estimated Net Profit $",
                    "Final Net Profit %",
                    "Final Net Profit $",
                ],
                "rows": [
                    [
                        "AY-659",
                        "Landscape Renovation",
                        "Completed",
                        379.0,
                        580.37,
                        201.37,
                        "$100,000",
                        "$110,000",
                        "$105,000",
                        "25%",
                        "$25,000",
                        "20%",
                        "$21,000",
                    ]
                ],
            }
        ],
        "cards": [],
        "links": [],
    }

    class FakeBrowser:
        def labor_variance(self, *, include_financial=False):
            assert include_financial is True
            return json.loads(json.dumps(raw))

        def read(self, spec, *, record_id=""):
            raise AssertionError("generic page read must not be used for job costing")

    monkeypatch.setattr(synkedup, "SynkedUPBrowser", FakeBrowser)
    monkeypatch.setattr(synkedup, "_AUDIT", synkedup.AuditLogger(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(synkedup._RATE_LIMITER, "acquire", lambda: None)
    result = synkedup._execute_read(synkedup.TOOL_SPEC_BY_NAME["synkedup_job_costing"])

    assert result["ok"] is True
    headers = result["data"]["tables"][0]["headers"]
    row = result["data"]["tables"][0]["rows"][0]
    assert "Estimated Net Profit %" in headers
    assert "Final Net Profit $" in headers
    assert "$100,000" in row
    assert "$21,000" in row


def test_hours_value_parses_dashboard_hour_labels():
    assert synkedup._hours_value("1,182.17h") == 1182.17
    assert synkedup._hours_value("0h") == 0.0
    assert synkedup._hours_value(36.5) == 36.5
    assert synkedup._hours_value("") is None


def test_financial_project_readiness_does_not_require_optional_final_total():
    source = Path(synkedup.__file__).read_text(encoding="utf-8")
    start = source.index("def project_ready")
    end = source.index("project = _wait_for_constant", start)
    project_ready_block = source[start:end]

    assert "final_total" not in project_ready_block
    assert "not expected_number" in project_ready_block


def test_financial_tools_never_appear_in_operations_toolset():
    operations = set(synkedup.TOOL_NAMES_BY_ACCESS_CLASS["operations"])
    financial = set(synkedup.TOOL_NAMES_BY_ACCESS_CLASS["financial"])
    assert operations.isdisjoint(financial)
    assert "synkedup_job_costing" in financial
    assert "synkedup_job_costing" not in operations


def test_job_costing_status_query_filters_completed_rows(monkeypatch, tmp_path):
    raw = {
        "url": "https://cjs-landscape.synkedup.com/dashboard#!/",
        "title": "Dashboard",
        "headings": [],
        "alerts": [],
        "fields": [],
        "tables": [
            {
                "headers": ["Job Number", "Name", "Status", "Final Total"],
                "rows": [
                    ["AY-659", "Landscape Renovation", "Completed", "$105,000"],
                    ["AY-425", "Lawn Restoration", "Waiting on parts", "$7,500"],
                ],
            }
        ],
        "cards": [],
        "links": [],
    }

    class FakeBrowser:
        def labor_variance(self, *, include_financial=False):
            assert include_financial is True
            return json.loads(json.dumps(raw))

    monkeypatch.setattr(synkedup, "SynkedUPBrowser", FakeBrowser)
    monkeypatch.setattr(synkedup, "_AUDIT", synkedup.AuditLogger(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(synkedup._RATE_LIMITER, "acquire", lambda: None)

    result = synkedup._execute_read(
        synkedup.TOOL_SPEC_BY_NAME["synkedup_job_costing"],
        query="status:completed",
    )

    assert result["ok"] is True
    assert result["data"]["tables"][0]["rows"] == [
        ["AY-659", "Landscape Renovation", "Completed", "$105,000"]
    ]


def test_dashboard_scan_navigates_back_to_dashboard_before_reading(monkeypatch):
    instances = []

    class FakeCDP:
        def __init__(self):
            self.navigated = []
            instances.append(self)

        def connect(self, host):
            assert host == "app.synkedup.test"

        def navigate(self, url, origin):
            self.navigated.append((url, origin))
            return {"url": url, "title": "Dashboard", "headings": ["Dashboard"]}

        def command(self, method, params=None):
            if method == "Target.createTarget":
                return {"targetId": "worker-1"}
            if method == "Target.closeTarget":
                return {}
            raise AssertionError(f"unexpected command: {method}")

        def connect_target(self, target_id):
            assert target_id == "worker-1"

        def close(self):
            return None

    monkeypatch.setenv("CJS_SYNKEDUP_BASE_URL", "https://app.synkedup.test")
    monkeypatch.setattr(synkedup, "CDPClient", FakeCDP)
    monkeypatch.setattr(
        synkedup,
        "_wait_for_constant",
        lambda client, expression, predicate: {
            "ready": True,
            "jobs": [],
            "date_start": "2026-07-27",
            "date_end": "2026-08-27",
        },
    )

    result = synkedup.SynkedUPBrowser().labor_variance(include_financial=True)

    assert result["tables"][0]["rows"] == []
    assert instances[0].navigated == [
        (
            "https://app.synkedup.test/dashboard",
            "https://app.synkedup.test",
        )
    ]


def test_dashboard_job_cache_requires_matching_range_and_safe_routes(monkeypatch, tmp_path):
    cache_path = tmp_path / "dashboard-jobs.json"
    monkeypatch.setenv("CJS_SYNKEDUP_DASHBOARD_CACHE_PATH", str(cache_path))
    dashboard = {
        "date_start": "2026-07-27",
        "date_end": "2026-08-27",
        "jobs": [
            {
                "label": "AY-659: Landscape Renovation",
                "href": "https://app.synkedup.test/#!/projects/466093-landscape-renovation",
            }
        ],
    }

    synkedup._write_dashboard_job_cache(dashboard)
    loaded = synkedup._load_dashboard_job_cache(
        origin="https://app.synkedup.test",
        date_start="2026-07-27",
        date_end="2026-08-27",
        now=datetime.now(timezone.utc),
    )

    assert loaded is not None
    assert loaded["jobs"] == dashboard["jobs"]
    assert synkedup._load_dashboard_job_cache(
        origin="https://app.synkedup.test",
        date_start="2026-07-28",
        date_end="2026-08-28",
    ) is None
    latest = synkedup._load_dashboard_job_cache(
        origin="https://app.synkedup.test",
        date_start="2026-07-28",
        date_end="2026-08-28",
        allow_range_mismatch=True,
    )
    assert latest is not None
    assert latest["cache_range_match"] is False
    assert latest["date_start"] == "2026-07-27"

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["jobs"][0]["href"] = "https://evil.test/#!/projects/466093"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    assert synkedup._load_dashboard_job_cache(
        origin="https://app.synkedup.test",
        date_start="2026-07-27",
        date_end="2026-08-27",
    ) is None


def test_dashboard_scan_uses_same_range_cache_when_widget_errors(monkeypatch, tmp_path):
    cache_path = tmp_path / "dashboard-jobs.json"
    monkeypatch.setenv("CJS_SYNKEDUP_BASE_URL", "https://app.synkedup.test")
    monkeypatch.setenv("CJS_SYNKEDUP_DASHBOARD_CACHE_PATH", str(cache_path))
    synkedup._write_dashboard_job_cache(
        {
            "date_start": "2026-07-27",
            "date_end": "2026-08-27",
            "jobs": [
                {
                    "label": "AY-659: Landscape Renovation",
                    "href": "https://app.synkedup.test/#!/projects/466093-landscape-renovation",
                }
            ],
        }
    )

    class FakeCDP:
        def connect(self, host):
            assert host == "app.synkedup.test"

        def navigate(self, url, origin):
            return {"url": url, "title": "Dashboard", "headings": ["Dashboard"]}

        def evaluate_constant(self, expression):
            assert expression == synkedup.EXTRACT_PAGE_SCRIPT
            return {
                "url": "https://app.synkedup.test/dashboard#!/",
                "title": "Dashboard",
                "headings": ["Dashboard"],
            }

        def command(self, method, params=None):
            if method == "Target.createTarget":
                return {"targetId": "worker-1"}
            if method in {"Target.closeTarget", "Page.navigate"}:
                return {}
            raise AssertionError(f"unexpected command: {method}")

        def connect_target(self, target_id):
            assert target_id == "worker-1"

        def close(self):
            return None

    def fake_wait(client, expression, predicate):
        if expression == synkedup.DASHBOARD_LABOR_JOBS_SCRIPT:
            return {
                "ready": False,
                "error_loading": True,
                "date_start": "2026-07-27",
                "date_end": "2026-08-27",
                "jobs": [],
            }
        assert expression == synkedup.PROJECT_LABOR_SUMMARY_SCRIPT
        return {
            "ready": True,
            "number": "AY-659",
            "name": "Landscape Renovation",
            "status": "Completed",
            "columns": ["Labor", "12h", "Variance", "10h"],
            "financials": {},
        }

    monkeypatch.setattr(synkedup, "CDPClient", FakeCDP)
    monkeypatch.setattr(synkedup, "_wait_for_constant", fake_wait)

    result = synkedup.SynkedUPBrowser().labor_variance()

    assert result["tables"][0]["rows"] == [
        ["AY-659", "Landscape Renovation", "Completed", 10.0, 12.0, 2.0]
    ]
    fields = {field["label"]: field["value"] for field in result["fields"]}
    assert fields["Dashboard job list source"] == "same-range cache"
    assert result["alerts"]


def test_full_scan_cache_round_trip_is_short_lived_and_origin_bound(monkeypatch, tmp_path):
    cache_path = tmp_path / "dashboard-jobs.json"
    monkeypatch.setenv("CJS_SYNKEDUP_DASHBOARD_CACHE_PATH", str(cache_path))
    captured = datetime.now(timezone.utc)
    result = {
        "url": "https://app.synkedup.test/dashboard",
        "title": "SynkedUP Dashboard Job Costing",
        "headings": ["Jobs included in this data"],
        "alerts": [],
        "fields": [],
        "tables": [
            {
                "headers": ["Job Number"],
                "rows": [
                    [
                        "AY-659",
                        "Landscape Renovation",
                        "Completed",
                        10.0,
                        12.0,
                        2.0,
                        "$10,000.00",
                        "$9,000.00",
                        "$9,500.00",
                        "30.00%",
                        "$3,000.00",
                        "25.00%",
                        "$2,375.00",
                    ]
                ],
            }
        ],
        "cards": [],
        "links": [],
    }

    synkedup._write_full_scan_cache(result, include_financial=True)
    loaded = synkedup._load_full_scan_cache(
        origin="https://app.synkedup.test",
        include_financial=True,
        now=captured,
    )

    assert loaded is not None
    assert loaded["tables"] == result["tables"]
    assert loaded["alerts"][0].startswith("Project details were read live recently")
    fields = {field["label"]: field["value"] for field in loaded["fields"]}
    assert fields["Project detail scan source"] == "recent live scan cache"
    assert synkedup._load_full_scan_cache(
        origin="https://app.synkedup.test",
        include_financial=True,
        now=captured + timedelta(minutes=30),
    ) is not None
    assert synkedup._load_full_scan_cache(
        origin="https://evil.test",
        include_financial=True,
        now=captured,
    ) is None
    assert synkedup._load_full_scan_cache(
        origin="https://app.synkedup.test",
        include_financial=False,
        now=captured,
    ) is None
    assert synkedup._load_full_scan_cache(
        origin="https://app.synkedup.test",
        include_financial=True,
        now=captured + timedelta(seconds=synkedup.FULL_SCAN_CACHE_MAX_AGE_SECONDS + 1),
    ) is None


def test_full_scan_cache_rejects_empty_rows(monkeypatch, tmp_path):
    cache_path = tmp_path / "dashboard-jobs.json"
    monkeypatch.setenv("CJS_SYNKEDUP_DASHBOARD_CACHE_PATH", str(cache_path))
    result = {
        "url": "https://app.synkedup.test/dashboard",
        "alerts": [],
        "fields": [],
        "tables": [{"headers": [], "rows": []}],
    }
    synkedup._write_full_scan_cache(result, include_financial=True)
    assert synkedup._load_full_scan_cache(
        origin="https://app.synkedup.test",
        include_financial=True,
    ) is None


def test_browser_returns_full_scan_cache_without_connecting_to_cdp(monkeypatch, tmp_path):
    cache_path = tmp_path / "dashboard-jobs.json"
    monkeypatch.setenv("CJS_SYNKEDUP_DASHBOARD_CACHE_PATH", str(cache_path))
    monkeypatch.setenv("CJS_SYNKEDUP_BASE_URL", "https://app.synkedup.test")
    result = {
        "url": "https://app.synkedup.test/dashboard",
        "alerts": [],
        "fields": [],
        "tables": [
            {
                "headers": [],
                "rows": [["AY-659", "Job", "Completed", 10.0, 12.0, 2.0]],
            }
        ],
    }
    synkedup._write_full_scan_cache(result, include_financial=False)

    class FailIfConstructed:
        def __init__(self):
            raise AssertionError("CDP should not be opened for a valid recent full-scan cache")

    monkeypatch.setattr(synkedup, "CDPClient", FailIfConstructed)
    loaded = synkedup.SynkedUPBrowser().labor_variance()
    assert loaded["tables"] == result["tables"]


def test_financial_scan_retries_project_when_costing_fields_are_late(monkeypatch, tmp_path):
    monkeypatch.setenv("CJS_SYNKEDUP_BASE_URL", "https://app.synkedup.test")
    monkeypatch.setenv(
        "CJS_SYNKEDUP_DASHBOARD_CACHE_PATH",
        str(tmp_path / "dashboard-jobs.json"),
    )
    navigations = []
    project_reads = 0

    class FakeCDP:
        def connect(self, host):
            assert host == "app.synkedup.test"

        def navigate(self, url, origin):
            return {"url": url, "title": "Dashboard", "headings": ["Dashboard"]}

        def command(self, method, params=None):
            if method == "Target.createTarget":
                return {"targetId": "worker-1"}
            if method == "Page.navigate":
                navigations.append(params["url"])
                return {}
            if method == "Target.closeTarget":
                return {}
            raise AssertionError(f"unexpected command: {method}")

        def connect_target(self, target_id):
            assert target_id == "worker-1"

        def close(self):
            return None

    def fake_wait(client, expression, predicate, *, timeout=25.0):
        nonlocal project_reads
        if expression == synkedup.DASHBOARD_LABOR_JOBS_SCRIPT:
            return {
                "ready": True,
                "date_start": "2026-07-27",
                "date_end": "2026-08-27",
                "jobs": [
                    {
                        "label": "AY-659: Landscape Renovation",
                        "href": "https://app.synkedup.test/#!/projects/466093-landscape-renovation",
                    }
                ],
            }
        project_reads += 1
        project = {
            "ready": True,
            "number": "AY-659",
            "name": "Landscape Renovation",
            "status": "Completed",
            "columns": ["Labor", "12h", "Variance", "10h"],
            "financials": {},
        }
        if project_reads == 2:
            project["financials"] = {
                "estimated_total": "$10,000.00",
                "actual_total": "$9,000.00",
                "final_total": "$9,500.00",
                "estimated_net_profit_percent": "30.00%",
                "estimated_net_profit_dollars": "$3,000.00",
                "final_net_profit_percent": "25.00%",
                "final_net_profit_dollars": "$2,375.00",
            }
        return project

    monkeypatch.setattr(synkedup, "CDPClient", FakeCDP)
    monkeypatch.setattr(synkedup, "_wait_for_constant", fake_wait)

    result = synkedup.SynkedUPBrowser().labor_variance(include_financial=True)

    assert project_reads == 2
    assert len(navigations) == 1
    assert result["tables"][0]["rows"][0][8] == "$9,500.00"
    assert not any("could not be read" in alert for alert in result["alerts"])
    assert (tmp_path / "job-costing.json").is_file()
