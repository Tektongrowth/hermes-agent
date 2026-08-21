from __future__ import annotations

from pathlib import Path


SOUL_PATH = Path(__file__).parents[2] / "deployments" / "cjs_whiteout" / "SOUL.md"


def test_mason_soul_defines_contractor_voice_and_current_employee_tools() -> None:
    soul = SOUL_PATH.read_text(encoding="utf-8")
    lowered = soul.casefold()

    assert "friend-to-friend" in lowered
    assert "contractor-to-contractor" in lowered
    assert "plain words" in lowered
    assert "light humor" in lowered
    assert "synkedup_active_jobs" in soul
    assert "synkedup_job_brief" in soul
    assert "synkedup_schedule" in soul
    assert "synkedup_labor_hours_variance" in soul
    assert "synkedup_item_quantity_variance" in soul


def test_mason_soul_keeps_employee_safety_rules_without_stale_tool_guidance() -> None:
    soul = SOUL_PATH.read_text(encoding="utf-8")
    lowered = soul.casefold()

    for protected_term in ("pricing", "margins", "payroll", "quickbooks", "credentials"):
        assert protected_term in lowered
    assert "discord direct messages fail closed" in lowered
    assert "never create, edit, delete, send, approve, pay, invoice, schedule" in lowered
    assert "crew can never perform writes, financial actions, or mason administration" in lowered
    assert "regardless of approval or which tools exist" in lowered

    for stale_tool in (
        "connector_status",
        "synkedup_business_snapshot",
        "synkedup_job_cost_variance",
        "synkedup_material_quantity_variance",
    ):
        assert stale_tool not in soul
    assert "Scope: SynkedUP Items is not a physical-material-only report" not in soul
