from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import yaml


ROOT = Path("deployments/cjs_whiteout")
HOOK_PATH = ROOT / "hooks" / "mason-training-escalation" / "handler.py"
EXPECTED_SKILLS = {
    "daily-operations-briefing",
    "cjs-job-lookup",
    "whiteout-account-lookup",
    "job-cost-project-review",
    "schedule-crew-planning",
    "hit-lists-reminders",
    "procurement-invoice-review",
    "project-changes-closeout",
    "snow-material-contract-operations",
    "workforce-directory-rewards",
}


def _load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_mason_training_hook", HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_reviewed_cjs_skills_are_deployed() -> None:
    skills_root = ROOT / "skills"
    names = {path.parent.name for path in skills_root.glob("*/SKILL.md")}
    assert names == EXPECTED_SKILLS
    for path in skills_root.glob("*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        assert "A skill never grants access by itself" in text
        assert "Mason's training hook" in text

    review = (skills_root / "job-cost-project-review" / "SKILL.md").read_text()
    assert "Never state a count that differs from the number of jobs actually listed" in review

    hit_lists = (skills_root / "hit-lists-reminders" / "SKILL.md").read_text()
    for phrase in [
        "call `todo` before writing any user-facing answer",
        "authoritative saved state",
        "Never claim a list mutation succeeded before the tool confirms it",
    ]:
        assert phrase in hit_lists

    soul = (ROOT / "SOUL.md").read_text()
    for phrase in [
        "Send exactly one user-facing answer per request",
        "send only the tool call with no draft",
        "Never announce that a skill was loaded",
        "treat its returned state as authoritative",
    ]:
        assert phrase in soul

    lookup = (skills_root / "cjs-job-lookup" / "SKILL.md").read_text()
    assert "Do not stop at the first empty or partial match" in lookup
    assert "00 - Sold YYYY" in lookup
    assert "composio_read_drive_pdf" in lookup
    for phrase in [
        "Preserve the employee's original note verbatim",
        "EOD - YYYY-MM-DD - <employee or crew>",
        "An EOD note needs an Alyssa alert",
        "A morning crew briefing",
        "A material shortage report",
        "A customer change request is not approved work",
        "A completion handoff",
        "what changed since yesterday",
        "questions about what remains",
        "never carry a customer or project name forward",
        "absence of a record is not proof",
        "filenames alone are not evidence",
        "omission from a later note does not resolve",
        "do not infer unfinished scope",
    ]:
        assert phrase in lookup

    procurement = (skills_root / "procurement-invoice-review" / "SKILL.md").read_text()
    for phrase in [
        "verify the source says it is sold",
        "Never silently allocate an invoice",
        "Do not create folders",
        "Never send or place an order without exact approval",
    ]:
        assert phrase in procurement

    changes = (skills_root / "project-changes-closeout" / "SKILL.md").read_text()
    for phrase in [
        "Pending review",
        "Preserve prior contract and design versions",
        "final sold scope plus approved change orders",
        "check every change ID",
        "treat that as a workflow setup request",
        "create two reusable versions",
        "ask for the smallest missing prerequisite",
    ]:
        assert phrase in changes

    snow = (skills_root / "snow-material-contract-operations" / "SKILL.md").read_text()
    for phrase in [
        "Chat history is not inventory",
        "Do not mix salt weight, liquid volume, and chemical quantity",
        "signed contract version",
        "Do not generate, change, approve, or send an invoice",
    ]:
        assert phrase in snow

    workforce = (skills_root / "workforce-directory-rewards" / "SKILL.md").read_text()
    for phrase in [
        "Never infer or rank by protected traits",
        "Do not make an automatic hiring or rejection decision",
        "Eligible for review",
        "Match against existing leads",
        "Payroll overtime and PTO tracking",
        "Total SynkedUP time without an overtime/PTO split is not a valid substitute",
        "Ask for the schedule only after the source is confirmed",
    ]:
        assert phrase in workforce

    briefing = (skills_root / "daily-operations-briefing" / "SKILL.md").read_text()
    for phrase in [
        "Read each candidate message body",
        "Check later replies",
        "Owner not specified",
        "do not send email, change mailbox state, create tasks",
        "A platform label such as `Action required`",
        "Never add who would typically handle it",
        "Reply verification unavailable",
        "`Addressed to Mike` and `original order was from Alyssa` do not assign ownership",
    ]:
        assert phrase in briefing


def test_mason_config_enables_skills_for_alyssa_without_generic_toolsets() -> None:
    config = yaml.safe_load((ROOT / "config" / "mason-config.example.yaml").read_text())
    assert config["model"] == {"provider": "openrouter", "default": "google/gemini-3.7-flash"}
    assert "skills" not in config["agent"]["disabled_toolsets"]
    alyssa = config["platform_principal_toolsets"]["discord"]["users"]["1541580058152665199"]
    assert "skills" in alyssa
    assert "terminal" not in alyssa
    assert "file" not in alyssa


def test_training_hook_ignores_success_and_non_discord(tmp_path, monkeypatch) -> None:
    hook = _load_hook()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    hook.handle("agent:end", {"platform": "discord", "response": "Done.", "message": "Do it"})
    hook.handle(
        "agent:end",
        {
            "platform": "telegram",
            "response": "I couldn't complete that with Mason's current tools.",
            "message": "Do it",
        },
    )
    assert not (tmp_path / "state" / "mason-training-requests.jsonl").exists()


def test_training_hook_recognizes_honest_tool_failure_variants(tmp_path, monkeypatch) -> None:
    hook = _load_hook()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MASON_TRAINING_ALERT_CHANNEL", raising=False)
    hook.handle(
        "agent:end",
        {
            "platform": "discord",
            "response": "I couldn't pull the SynkedUP data right now, and I'm not going to guess.",
            "message": "Compare completed jobs.",
        },
    )
    records = (tmp_path / "state" / "mason-training-requests.jsonl").read_text().splitlines()
    assert len(records) == 1


def test_training_hook_records_notifies_and_deduplicates(tmp_path, monkeypatch) -> None:
    hook = _load_hook()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("MASON_TRAINING_ALERT_CHANNEL", "123")
    sent: list[dict] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        assert timeout == 15
        assert request.full_url.endswith("/channels/123/messages")
        return Response()

    monkeypatch.setattr(hook.urllib.request, "urlopen", fake_urlopen)
    context = {
        "platform": "discord",
        "chat_id": "456",
        "user_id": "789",
        "session_id": "session",
        "message": "Pull a system Mason cannot reach",
        "response": "I couldn't complete that with Mason's current tools. The connection is missing.",
    }
    hook.handle("agent:end", context)
    hook.handle("agent:end", context)

    records = (tmp_path / "state" / "mason-training-requests.jsonl").read_text().splitlines()
    assert len(records) == 1
    record = json.loads(records[0])
    assert record["status"] == "new"
    assert record["request"] == context["message"]
    assert "do not execute the underlying business task" in record["instruction"].casefold()
    assert len(sent) == 1
    assert "Status: New" in sent[0]["content"]
    assert "Train or repair Mason" in sent[0]["content"]


def test_installer_replaces_generic_skills_and_deploys_training_hook() -> None:
    installer = (ROOT / "install-synkedup.sh").read_text(encoding="utf-8")
    assert "backup_path /var/lib/cjs-whiteout/hermes/skills" in installer
    assert "backup_path /var/lib/cjs-whiteout/hermes/hooks" in installer
    assert 'rm -rf /var/lib/cjs-whiteout/hermes/skills /var/lib/cjs-whiteout/hermes/hooks' in installer
    assert 'cp -a "$SKILLS_SOURCE/." /var/lib/cjs-whiteout/hermes/skills/' in installer
    assert 'cp -a "$HOOKS_SOURCE/." /var/lib/cjs-whiteout/hermes/hooks/' in installer
    assert 'config.setdefault("skills", {})["disabled"] = sorted(discovered - allowed)' in installer
    for skill in EXPECTED_SKILLS:
        assert f'"{skill}"' in installer
