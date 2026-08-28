import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployments.cjs_whiteout.connectors import cjs_composio_mcp as bridge


DEPLOYMENT_ROOT = Path(__file__).parents[2] / "deployments" / "cjs_whiteout"


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("CJS_COMPOSIO_TOOLKITS", "googledrive")
    monkeypatch.setenv("CJS_COMPOSIO_TOOL_PREFIXES", "GOOGLEDRIVE")
    monkeypatch.setenv("CJS_COMPOSIO_ACCOUNT", "googledrive_test-account")
    monkeypatch.setenv("CJS_COMPOSIO_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(bridge, "_composio_binary", lambda: "/safe/composio")


def test_execute_pins_account_and_never_uses_shell(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"successful":true}', stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    result = bridge.composio_execute(
        "GOOGLEDRIVE_CREATE_FOLDER",
        {"name": "Dale Petersen", "parent_id": "approved-projects"},
    )
    assert result == {"successful": True}
    assert seen["argv"][:3] == ["/safe/composio", "execute", "GOOGLEDRIVE_CREATE_FOLDER"]
    assert seen["argv"][3:5] == ["--account", "googledrive_test-account"]
    assert json.loads(seen["argv"][6]) == {
        "name": "Dale Petersen",
        "parent_id": "approved-projects",
    }
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["stdin"] is bridge.subprocess.DEVNULL


def test_execute_rejects_unapproved_toolkit():
    with pytest.raises(bridge.BridgeRequestError, match="outside the approved"):
        bridge.composio_execute("GMAIL_SEND_EMAIL", {"recipient": "x@example.com"})


def test_tool_slug_rejects_shell_metacharacters():
    with pytest.raises(bridge.BridgeRequestError, match="uppercase Composio tool slug"):
        bridge.composio_execute("GOOGLEDRIVE_FIND_FILE;rm", {})


def test_search_is_forced_to_approved_toolkits(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout='{"tools":[]}', stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    bridge.composio_search("find the top down plan", limit=5)
    assert seen["argv"] == [
        "/safe/composio",
        "search",
        "find the top down plan",
        "--toolkits",
        "googledrive",
        "--limit",
        "5",
    ]


def test_sensitive_cli_error_is_redacted(monkeypatch):
    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="token=super-secret")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    with pytest.raises(bridge.BridgeRequestError) as exc:
        bridge.composio_tool_schema("GOOGLEDRIVE_FIND_FILE")
    assert "super-secret" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_arguments_have_a_hard_size_limit():
    with pytest.raises(bridge.BridgeRequestError, match="too large"):
        bridge._bounded_arguments({"body": "x" * (bridge.MAX_ARGUMENT_CHARS + 1)})


def test_missing_account_fails_closed(monkeypatch):
    monkeypatch.delenv("CJS_COMPOSIO_ACCOUNT")
    with pytest.raises(bridge.BridgeConfigurationError, match="pinned CJS"):
        bridge._account_selector()


def test_cli_parser_accepts_json_with_trailing_download_status():
    result = bridge._parse_cli_output(
        '  {"successful":true,"data":{"name":"plan.pdf"}}\n'
        'Downloaded file successfully to the temporary store.\n'
    )
    assert result == {"successful": True, "data": {"name": "plan.pdf"}}


def test_cli_parser_preserves_plain_text_fallback():
    assert bridge._parse_cli_output("plain output\n") == {"output": "plain output"}


def test_structured_results_are_recursively_redacted():
    safe = bridge._sanitize_result(
        {"data": [{"url": "https://example.com/?token=secret-value"}]}
    )
    assert "secret-value" not in safe["data"][0]["url"]
    assert "[REDACTED]" in safe["data"][0]["url"]


def test_run_parses_signed_url_before_redacting_plain_text(monkeypatch):
    signed_url = (
        "https://temp.0123456789abcdef.r2.cloudflarestorage.com/file"
        "?X-Amz-Credential=temporary%2Fcredential&X-Amz-Signature=abc"
    )

    def fake_run(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "successful": True,
                    "data": {
                        "downloaded_file_content": {
                            "mimetype": "application/pdf",
                            "name": "plan.pdf",
                            "s3url": signed_url,
                        }
                    },
                }
            )
            + "\nDownload complete\n",
            stderr="",
        )

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    result = bridge._run(
        ["execute", "GOOGLEDRIVE_DOWNLOAD_FILE"],
        sanitize_result=False,
    )
    assert result["successful"] is True
    assert result["data"]["downloaded_file_content"]["s3url"] == signed_url


@pytest.mark.parametrize(
    "url",
    [
        "http://temp.0123456789abcdef.r2.cloudflarestorage.com/file?sig=x",
        "https://example.com/file?sig=x",
        "https://user@temp.0123456789abcdef.r2.cloudflarestorage.com/file?sig=x",
        "https://temp.0123456789abcdef.r2.cloudflarestorage.com/file",
    ],
)
def test_pdf_download_url_rejects_unapproved_locations(url):
    with pytest.raises(bridge.BridgeRequestError, match="unapproved"):
        bridge._validate_composio_file_url(url)


def test_pdf_download_url_accepts_composio_temporary_storage():
    url = "https://temp.0123456789abcdef.r2.cloudflarestorage.com/file?X-Amz-Signature=x"
    assert bridge._validate_composio_file_url(url) == url


def test_pdf_payload_must_be_successful_pdf():
    with pytest.raises(bridge.BridgeRequestError, match="not a PDF"):
        bridge._extract_pdf_download(
            {
                "successful": True,
                "data": {
                    "mimeType": "text/plain",
                    "downloaded_file_content": {
                        "mimetype": "text/plain",
                        "name": "notes.txt",
                        "s3url": "https://example.com/file",
                    },
                },
            }
        )


def test_pdf_page_count_has_a_hard_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bridge,
        "_run_pdf_helper",
        lambda args, timeout: SimpleNamespace(stdout="Pages:           11\n"),
    )
    with pytest.raises(bridge.BridgeRequestError, match="page limit"):
        bridge._pdf_page_count(tmp_path / "document.pdf")


def test_pdf_renderer_preserves_native_full_page_jpeg(monkeypatch, tmp_path):
    native_jpeg = b"\xff\xd8\xffnative-image-bytes\xff\xd9"
    calls = []

    def fake_helper(args, timeout):
        calls.append(args)
        command = Path(args[0]).name
        if command == "pdfinfo":
            return SimpleNamespace(stdout="Page size:       960 x 540 pts\n")
        if command == "pdfimages" and "-list" in args:
            return SimpleNamespace(
                stdout=(
                    "page num type width height color comp bpc enc interp object ID "
                    "x-ppi y-ppi size ratio\n"
                    "1 0 image 1280 720 rgb 3 8 jpeg no 5 0 96 96 84.5K 3.1%\n"
                )
            )
        if command == "pdfimages" and "-j" in args:
            Path(f"{args[-1]}-000.jpg").write_bytes(native_jpeg)
            return SimpleNamespace(stdout="")
        pytest.fail(f"unexpected PDF helper call: {args}")

    monkeypatch.setattr(bridge, "_run_pdf_helper", fake_helper)

    rendered = bridge._render_pdf_pages(tmp_path / "document.pdf", tmp_path, 1)

    assert rendered == [native_jpeg]
    assert all(Path(args[0]).name != "pdftoppm" for args in calls)


def test_pdf_renderer_fallback_does_not_force_scale(monkeypatch, tmp_path):
    fallback_jpeg = b"\xff\xd8\xfffallback-image-bytes\xff\xd9"
    raster_args = []

    def fake_helper(args, timeout):
        command = Path(args[0]).name
        if command == "pdfimages" and "-list" in args:
            return SimpleNamespace(stdout="no embedded full-page JPEGs\n")
        if command == "pdftoppm":
            raster_args.extend(args)
            Path(f"{args[-1]}-1.jpg").write_bytes(fallback_jpeg)
            return SimpleNamespace(stdout="")
        pytest.fail(f"unexpected PDF helper call: {args}")

    monkeypatch.setattr(bridge, "_run_pdf_helper", fake_helper)

    rendered = bridge._render_pdf_pages(tmp_path / "document.pdf", tmp_path, 1)

    assert rendered == [fallback_jpeg]
    assert "-scale-to" not in raster_args
    assert raster_args[raster_args.index("-r") + 1] == "150"


def test_read_drive_pdf_returns_text_and_image_blocks(monkeypatch):
    seen = {}
    download_url = (
        "https://temp.0123456789abcdef.r2.cloudflarestorage.com/file"
        "?X-Amz-Signature=x"
    )

    def fake_run(
        args,
        timeout=bridge.DEFAULT_TIMEOUT_SECONDS,
        sanitize_result=True,
    ):
        seen["args"] = args
        seen["timeout"] = timeout
        seen["sanitize_result"] = sanitize_result
        return {
            "successful": True,
            "data": {
                "mimeType": "application/pdf",
                "name": "Top Down Plan.pdf",
                "downloaded_file_content": {
                    "mimetype": "application/pdf",
                    "name": "Top Down Plan.pdf",
                    "s3url": download_url,
                },
            },
        }

    def fake_download(url, destination):
        assert url == download_url
        destination.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(bridge, "_run", fake_run)
    monkeypatch.setattr(bridge, "_download_pdf", fake_download)
    monkeypatch.setattr(bridge, "_pdf_page_count", lambda path: 1)
    monkeypatch.setattr(bridge, "_extract_pdf_text", lambda pdf, workdir: "embedded notes")
    monkeypatch.setattr(
        bridge,
        "_render_pdf_pages",
        lambda pdf, workdir, pages: [b"\xff\xd8\xffpage\xff\xd9"],
    )

    blocks = bridge.composio_read_drive_pdf("file_abc123")

    assert [block.type for block in blocks] == ["text", "text", "image"]
    assert "vision_analyze" in blocks[0].text
    assert blocks[1].text.endswith("embedded notes")
    assert blocks[2].mimeType == "image/jpeg"
    assert seen["args"][:3] == ["execute", "GOOGLEDRIVE_DOWNLOAD_FILE", "--account"]
    assert json.loads(seen["args"][-1]) == {"fileId": "file_abc123"}
    assert seen["timeout"] == 180
    assert seen["sanitize_result"] is False


def test_read_drive_pdf_rejects_malformed_file_id(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_run",
        lambda *args, **kwargs: pytest.fail("Composio must not be called"),
    )
    with pytest.raises(bridge.BridgeRequestError, match="canonical Google Drive"):
        bridge.composio_read_drive_pdf("../bad")


def test_pdf_reader_is_wired_into_mason_config_and_instructions():
    config = (DEPLOYMENT_ROOT / "config" / "mason-config.example.yaml").read_text(
        encoding="utf-8"
    )
    soul = (DEPLOYMENT_ROOT / "SOUL.md").read_text(encoding="utf-8")
    assert config.count("- composio_read_drive_pdf") == 1
    assert "Use `composio_read_drive_pdf`" in soul
    assert "call `vision_analyze` on every page" in soul
