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


def test_mason_config_enables_skills_for_alyssa_without_generic_toolsets() -> None:
    config = yaml.safe_load((ROOT / "config" / "mason-config.example.yaml").read_text())
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
