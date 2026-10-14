"""How the installer merges into hook config that already has entries in it.

These tests use the user's *real* current configuration shape as the starting point, because
clobbering `block-git-mutations.sh` would be the single most damaging thing this project could
do to the machine it runs on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_village.hookinstall import (
    CURSOR_EVENTS,
    CURSOR_FORBIDDEN,
    MARKER,
    plan_claude,
    plan_cursor,
)

SCRIPT = Path("/opt/agent-village/hooks/village-emit.sh")

#: The shape of ~/.cursor/hooks.json as it actually exists on this machine.
EXISTING_CURSOR = {
    "version": 1,
    "hooks": {
        "beforeShellExecution": [
            {
                "command": "./hooks/block-git-mutations.sh",
                "matcher": "^git\\s+(commit|push|tag|rebase|merge|cherry-pick|reset)",
                "failClosed": True,
            }
        ],
        "stop": [{"command": "./hooks/check-tests-ran.sh", "loop_limit": 3}],
        "afterFileEdit": [{"command": "./hooks/check-plan-location.sh", "matcher": "Write"}],
    },
}

#: The shape of ~/.claude/settings.json as it actually exists: no hooks key at all.
EXISTING_CLAUDE = {
    "model": "opus[1m]",
    "effortLevel": "high",
    "enabledPlugins": {"frontend-design@claude-plugins-official": True},
}


def village_commands(hooks: dict) -> list[str]:
    found = []
    for entries in hooks.values():
        for entry in entries:
            inner = entry.get("hooks", [entry]) if isinstance(entry, dict) else []
            for h in inner:
                if MARKER in str(h.get("command", "")):
                    found.append(h["command"])
    return found


class TestCursorMerge:
    def test_existing_hooks_survive(self):
        merged = plan_cursor(EXISTING_CURSOR, SCRIPT)
        commands = [e["command"] for e in merged["hooks"]["beforeShellExecution"]]
        assert "./hooks/block-git-mutations.sh" in commands

    def test_the_git_guard_is_not_joined_by_a_village_hook(self):
        """beforeShellExecution is a permission hook. We must stay off it entirely."""
        merged = plan_cursor(EXISTING_CURSOR, SCRIPT)
        for event in CURSOR_FORBIDDEN:
            entries = merged["hooks"].get(event, [])
            assert all(MARKER not in str(e.get("command", "")) for e in entries), event

    def test_appends_alongside_an_existing_stop_hook(self):
        merged = plan_cursor(EXISTING_CURSOR, SCRIPT)
        commands = [e["command"] for e in merged["hooks"]["stop"]]
        assert commands[0] == "./hooks/check-tests-ran.sh"
        assert MARKER in commands[1]

    def test_registers_every_intended_event(self):
        merged = plan_cursor(EXISTING_CURSOR, SCRIPT)
        for event in CURSOR_EVENTS:
            entries = merged["hooks"][event]
            assert any(MARKER in str(e.get("command", "")) for e in entries), event

    def test_installing_twice_is_the_same_as_installing_once(self):
        once = plan_cursor(EXISTING_CURSOR, SCRIPT)
        twice = plan_cursor(once, SCRIPT)
        assert once == twice

    def test_uninstall_restores_the_original_exactly(self):
        installed = plan_cursor(EXISTING_CURSOR, SCRIPT)
        removed = plan_cursor(installed, SCRIPT, enable=False)
        assert removed == EXISTING_CURSOR

    def test_works_on_an_empty_config(self):
        merged = plan_cursor({}, SCRIPT)
        assert merged["version"] == 1
        assert len(village_commands(merged["hooks"])) == len(CURSOR_EVENTS)

    def test_the_forbidden_list_is_actually_enforced(self, monkeypatch):
        monkeypatch.setattr("agent_village.hookinstall.CURSOR_EVENTS", ["preToolUse"])
        with pytest.raises(ValueError, match="permission hook"):
            plan_cursor({}, SCRIPT)


class TestClaudeMerge:
    def test_unrelated_settings_are_untouched(self):
        merged = plan_claude(EXISTING_CLAUDE, SCRIPT)
        assert merged["model"] == "opus[1m]"
        assert merged["effortLevel"] == "high"
        assert merged["enabledPlugins"] == EXISTING_CLAUDE["enabledPlugins"]

    def test_hooks_are_async_so_they_cannot_stall_or_decide(self):
        """An async hook is never waited on and its output is never read as a decision."""
        merged = plan_claude(EXISTING_CLAUDE, SCRIPT)
        for entries in merged["hooks"].values():
            for group in entries:
                for hook in group["hooks"]:
                    assert hook["async"] is True
                    assert hook["timeout"] == 5

    def test_the_event_name_is_passed_as_an_argument(self):
        merged = plan_claude(EXISTING_CLAUDE, SCRIPT)
        assert merged["hooks"]["PermissionRequest"][0]["hooks"][0]["command"].endswith("claude PermissionRequest")

    def test_an_existing_user_hook_is_preserved(self):
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/audit.sh"}]}
                ]
            }
        }
        merged = plan_claude(existing, SCRIPT)
        groups = merged["hooks"]["PreToolUse"]
        assert groups[0]["hooks"][0]["command"] == "/usr/local/bin/audit.sh"
        assert groups[0]["matcher"] == "Bash"
        assert MARKER in groups[1]["hooks"][0]["command"]

    def test_installing_twice_is_the_same_as_installing_once(self):
        once = plan_claude(EXISTING_CLAUDE, SCRIPT)
        twice = plan_claude(once, SCRIPT)
        assert once == twice

    def test_uninstall_restores_the_original_exactly(self):
        installed = plan_claude(EXISTING_CLAUDE, SCRIPT)
        removed = plan_claude(installed, SCRIPT, enable=False)
        assert removed == EXISTING_CLAUDE

    def test_uninstall_keeps_a_users_own_hook_on_a_shared_event(self):
        existing = {
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/usr/local/bin/mine.sh"}]}]},
        }
        installed = plan_claude(existing, SCRIPT)
        removed = plan_claude(installed, SCRIPT, enable=False)
        assert removed == existing


class TestInstallerCli:
    def test_dry_run_changes_nothing_on_disk(self, tmp_path, capsys):
        from agent_village.hookinstall import main

        claude = tmp_path / "settings.json"
        cursor = tmp_path / "hooks.json"
        claude.write_text(json.dumps(EXISTING_CLAUDE))
        cursor.write_text(json.dumps(EXISTING_CURSOR))
        script = tmp_path / "village-emit.sh"
        script.write_text("#!/bin/bash\n")

        rc = main(
            [
                "--dry-run",
                "--claude-settings",
                str(claude),
                "--cursor-hooks",
                str(cursor),
                "--script",
                str(script),
            ]
        )
        assert rc == 0
        assert json.loads(claude.read_text()) == EXISTING_CLAUDE
        assert json.loads(cursor.read_text()) == EXISTING_CURSOR
        assert MARKER in capsys.readouterr().out

    def test_install_then_uninstall_round_trips_on_disk(self, tmp_path):
        from agent_village.hookinstall import main

        cursor = tmp_path / "hooks.json"
        cursor.write_text(json.dumps(EXISTING_CURSOR))
        script = tmp_path / "village-emit.sh"
        script.write_text("#!/bin/bash\n")
        common = [
            "--skip-claude",
            "--cursor-hooks",
            str(cursor),
            "--script",
            str(script),
            "--backup-dir",
            str(tmp_path / "backups"),
        ]

        assert main(common) == 0
        assert MARKER in cursor.read_text()
        assert main([*common, "--uninstall"]) == 0
        assert json.loads(cursor.read_text()) == EXISTING_CURSOR

    def test_a_backup_is_written_before_any_change(self, tmp_path):
        from agent_village.hookinstall import main

        cursor = tmp_path / "hooks.json"
        cursor.write_text(json.dumps(EXISTING_CURSOR))
        script = tmp_path / "village-emit.sh"
        script.write_text("#!/bin/bash\n")
        backups = tmp_path / "backups"

        main(["--skip-claude", "--cursor-hooks", str(cursor), "--script", str(script), "--backup-dir", str(backups)])
        saved = list(backups.glob("hooks.json.*.bak"))
        assert len(saved) == 1
        assert json.loads(saved[0].read_text()) == EXISTING_CURSOR

    def test_malformed_config_is_refused_rather_than_overwritten(self, tmp_path, capsys):
        from agent_village.hookinstall import main

        cursor = tmp_path / "hooks.json"
        cursor.write_text("{ this is not json")
        script = tmp_path / "village-emit.sh"
        script.write_text("#!/bin/bash\n")

        rc = main(["--skip-claude", "--cursor-hooks", str(cursor), "--script", str(script)])
        assert rc == 2
        assert cursor.read_text() == "{ this is not json"
        assert "refusing" in capsys.readouterr().err
