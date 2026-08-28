from __future__ import annotations

from pathlib import Path


SOUL_PATH = Path(__file__).parents[2] / "deployments" / "cjs_whiteout" / "SOUL.md"


def test_mason_soul_defines_contractor_voice_and_current_tools() -> None:
    soul = SOUL_PATH.read_text(encoding="utf-8")
    lowered = soul.casefold()

    assert "friend-to-friend" in lowered
    assert "contractor-to-contractor" in lowered
    assert "plain words" in lowered
    assert "light humor" in lowered
    for tool in (
        "synkedup_labor_variance",
        "synkedup_job_costing",
        "composio_read_drive_spreadsheet",
        "cronjob",
        "todo",
    ):
        assert tool in soul


def test_mason_soul_keeps_current_safety_rules_without_stale_tool_guidance() -> None:
    soul = SOUL_PATH.read_text(encoding="utf-8")
    lowered = soul.casefold()

    for protected_term in (
        "billing",
        "payroll",
        "credentials",
        "irreversible",
        "unknown users",
    ):
        assert protected_term in lowered
    assert "discord direct messages fail closed" in lowered
    assert "requires confirmation for ordinary users" in lowered
    assert "keep cjs landscape and whiteout winter services data inside this client account" in lowered

    for stale_tool in (
        "connector_status",
        "synkedup_business_snapshot",
        "synkedup_job_cost_variance",
        "synkedup_material_quantity_variance",
    ):
        assert stale_tool not in soul
    assert "Scope: SynkedUP Items is not a physical-material-only report" not in soul


def test_mason_soul_requires_complete_job_number_comparison() -> None:
    soul = SOUL_PATH.read_text(encoding="utf-8").casefold()

    assert "match jobs by exact job number" in soul
    assert "pass every synkedup job number in `job_numbers`" in soul
    assert "estimated hours" in soul
    assert "actual hours" in soul
    assert "final net profit %" in soul
    assert "final net profit $" in soul
    assert "final total" in soul
    assert "including zero values and small decimal differences" in soul
    assert "do not count the project-name column as a mismatch" in soul
