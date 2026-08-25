from __future__ import annotations

import base64
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import boto3
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from deployments.client_connection_portal.admin import issue_test_invitation


REGION = "us-west-2"
FUNCTION_NAME = "tekton-client-connection-portal-test"
TABLE_NAME = "tekton-client-connection-portal-test"
SLOTS = [
    "google-drive",
    "gmail-primary",
    "microsoft-primary",
    "quickbooks-cjs",
    "quickbooks-whiteout",
    "yeti",
]
RECIPIENT = "nick.qa@cjs.test"
CACHE = Path("/home/nick/.cache/cjs-connector-portal-qa")


def _http_status(url: str, *, method: str = "GET", form: dict[str, str] | None = None):
    data = urlencode(form).encode("utf-8") if form is not None else None
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data else {}
    request = UrlRequest(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8"), dict(response.headers)
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


def _claims_from_link(link: str) -> dict[str, object]:
    token = urlsplit(link).path.removeprefix("/s/")
    body = token.split(".", 1)[0]
    body += "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body).decode("utf-8"))


def _set_viewport(driver, *, width: int, height: int, mobile: bool) -> None:
    driver.execute_cdp_cmd(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": width,
            "height": height,
            "deviceScaleFactor": 1,
            "mobile": mobile,
        },
    )


def main() -> int:
    print("QA_STARTED")
    CACHE.mkdir(parents=True, exist_ok=True)
    session = boto3.Session(profile_name="tekton", region_name=REGION)
    lambda_client = session.client("lambda")
    base_url = lambda_client.get_function_url_config(FunctionName=FUNCTION_NAME)[
        "FunctionUrl"
    ].rstrip("/")
    link = issue_test_invitation(
        boto3_module=session,
        base_url=base_url,
        recipient_email=RECIPIENT,
        slots=SLOTS,
        ttl_seconds=1_200,
        now=time.time,
        id_factory=lambda: uuid.uuid4().hex,
    )
    claims = _claims_from_link(link)

    checks: dict[str, object] = {}
    for path, method, form in (
        ("/oauth/start", "POST", {"slot": "gmail-primary"}),
        ("/oauth/callback/google?code=sentinel-code&state=sentinel-state", "GET", None),
        ("/credentials/yeti", "POST", {"username": "sentinel-user", "password": "sentinel-password"}),
    ):
        status, body, _ = _http_status(base_url + path, method=method, form=form)
        assert status == 404
        assert "sentinel" not in body
    checks["real_routes_hidden"] = True

    status, _, _ = _http_status(base_url + "/s/not-a-valid-token")
    assert status == 410
    checks["invalid_link_rejected"] = True

    profile_dir = Path(f"/dev/shm/cjs-portal-qa-{os.getpid()}")
    shutil.rmtree(profile_dir, ignore_errors=True)
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage=false")
    options.add_argument("--incognito")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disk-cache-size=1")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    driver = webdriver.Chrome(options=options)
    try:
        wait = WebDriverWait(driver, 20)
        _set_viewport(driver, width=1280, height=900, mobile=False)
        driver.get(link)
        wait.until(lambda browser: browser.current_url.rstrip("/").endswith("/setup"))
        assert driver.title == "CJS Landscape connection setup"
        assert RECIPIENT in driver.page_source
        assert len(driver.find_elements(By.CSS_SELECTOR, "section.card")) == len(SLOTS)
        assert len(driver.find_elements(By.TAG_NAME, "script")) == 0
        assert driver.execute_script("return performance.getEntriesByType('resource').length") == 0
        geometry = driver.execute_script(
            "return {innerWidth:innerWidth,scrollWidth:document.documentElement.scrollWidth}"
        )
        assert geometry["scrollWidth"] <= geometry["innerWidth"]
        driver.save_screenshot(str(CACHE / "desktop.png"))
        checks["desktop_width"] = geometry["innerWidth"]
        checks["desktop_no_overflow"] = True

        _set_viewport(driver, width=390, height=844, mobile=True)
        mobile_geometry = driver.execute_script(
            "return {innerWidth:innerWidth,scrollWidth:document.documentElement.scrollWidth,columns:getComputedStyle(document.querySelector('.grid')).gridTemplateColumns}"
        )
        assert mobile_geometry["innerWidth"] == 390
        assert mobile_geometry["scrollWidth"] <= mobile_geometry["innerWidth"]
        assert " " not in mobile_geometry["columns"].strip()
        driver.save_screenshot(str(CACHE / "mobile.png"))
        checks["mobile_width"] = mobile_geometry["innerWidth"]
        checks["mobile_no_overflow"] = True
        checks["mobile_single_column"] = True

        _set_viewport(driver, width=1280, height=900, mobile=False)
        complete = driver.find_element(By.XPATH, "//form[@class='complete']/button")
        assert complete.get_attribute("disabled") is not None

        for slot_id in SLOTS:
            form = driver.find_element(
                By.XPATH,
                f"//form[input[@name='slot' and @value='{slot_id}']]",
            )
            if slot_id == "yeti":
                form.find_element(By.NAME, "username").send_keys("sensitive-user")
                form.find_element(By.NAME, "password").send_keys("sensitive-password")
            old_form = form
            form.find_element(By.TAG_NAME, "button").click()
            wait.until(lambda browser: browser.current_url.rstrip("/").endswith("/setup"))
            wait.until(lambda browser: old_form not in browser.find_elements(By.TAG_NAME, "form"))

        connected = driver.find_elements(By.XPATH, "//*[contains(@class,'status') and normalize-space()='Connected']")
        assert len(connected) == len(SLOTS)
        complete = driver.find_element(By.XPATH, "//form[@class='complete']/button")
        assert complete.get_attribute("disabled") is None
        complete.click()
        wait.until(lambda browser: "Setup complete" in browser.page_source)
        assert "session is now locked" in driver.page_source
        checks["all_slots_connected"] = True
        checks["completion_locked"] = True

        browser_logs = driver.get_log("browser")
        severe = [entry for entry in browser_logs if entry.get("level") == "SEVERE"]
        assert severe == [], [
            {"level": entry.get("level"), "source": entry.get("source")}
            for entry in severe
        ]
        checks["browser_console_clean"] = True

        driver.get(link)
        wait.until(lambda browser: "unavailable" in browser.page_source)
        checks["invitation_replay_rejected"] = True
    finally:
        driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)

    table = session.resource("dynamodb").Table(TABLE_NAME)
    item = table.get_item(
        Key={
            "pk": f"TENANT#{claims['tenant_id']}#INVITE#{claims['invitation_id']}"
        },
        ConsistentRead=True,
    )["Item"]
    serialized = json.dumps(item, default=str, sort_keys=True)
    assert item["status"] == "complete"
    assert set(item["connections"]) == set(SLOTS)
    assert "sensitive-user" not in serialized
    assert "sensitive-password" not in serialized
    assert all(connection.get("simulated") is True for connection in item["connections"].values())
    checks["dynamo_persistence"] = True
    checks["credential_values_absent"] = True
    checks["simulated_only"] = True

    summary = {
        "checks": checks,
        "screenshots": [str(CACHE / "desktop.png"), str(CACHE / "mobile.png")],
        "invitation_ttl_seconds": 1_200,
        "slots_tested": len(SLOTS),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("QA_COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        exit_code = 1
    raise SystemExit(exit_code)
