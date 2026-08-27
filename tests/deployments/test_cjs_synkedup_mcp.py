from __future__ import annotations

import json
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


def test_financial_tools_never_appear_in_operations_toolset():
    operations = set(synkedup.TOOL_NAMES_BY_ACCESS_CLASS["operations"])
    financial = set(synkedup.TOOL_NAMES_BY_ACCESS_CLASS["financial"])
    assert operations.isdisjoint(financial)
    assert "synkedup_job_costing" in financial
    assert "synkedup_job_costing" not in operations
