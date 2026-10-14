"""The hook script itself, exercised exactly as an agent would run it.

This is the one component that executes inside a live agent loop, so the properties that matter
are behavioural: it must always exit 0, always hand control back to the agent, and never lose
an event.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_village.adapters import parse_spool_line
from agent_village.models import EventKind

SCRIPT = Path(__file__).resolve().parents[1] / "hooks" / "village-emit.sh"


def run_hook(payload: str, source: str = "claude", event: str = "Stop", spool: Path | None = None, **env):
    result = subprocess.run(
        ["bash", str(SCRIPT), source, event],
        input=payload,
        capture_output=True,
        check=False,
        text=True,
        env={"HOME": str(spool.parent) if spool else "/tmp", "AGENT_VILLAGE_SPOOL": str(spool), **env},
    )
    return result


def spooled(spool: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(spool.glob("*.json"))]


@pytest.fixture
def spool(tmp_path) -> Path:
    return tmp_path / "spool"


class TestContract:
    def test_it_always_lets_the_agent_continue(self, spool):
        result = run_hook('{"session_id":"s1"}', spool=spool)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"continue": True}

    def test_the_event_lands_in_the_spool(self, spool):
        run_hook('{"session_id":"s1","cwd":"/w"}', event="Stop", spool=spool)
        records = spooled(spool)
        assert len(records) == 1
        assert records[0]["src"] == "claude"
        assert records[0]["ev"] == "Stop"
        assert records[0]["p"]["session_id"] == "s1"

    def test_what_it_writes_is_what_the_adapter_reads(self, spool):
        """End to end on the real format: no hand-written fixture can go stale here."""
        run_hook(
            json.dumps({"session_id": "s1", "cwd": "/w", "tool_name": "Bash", "tool_input": {"command": "ls"}}),
            event="PreToolUse",
            spool=spool,
        )
        line = next(spool.glob("*.json")).read_text()
        events = parse_spool_line(line)
        assert len(events) == 1
        assert events[0].kind is EventKind.TOOL_START
        assert events[0].tool_detail == "ls"

    def test_it_survives_empty_stdin(self, spool):
        result = run_hook("", spool=spool)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"continue": True}

    def test_it_survives_a_spool_it_cannot_write_to(self, tmp_path):
        """A read-only disk must not take an agent session down with it."""
        blocked = tmp_path / "nope"
        blocked.write_text("i am a file, not a directory")
        result = run_hook('{"session_id":"s1"}', spool=blocked / "spool")
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"continue": True}

    def test_multiline_payloads_collapse_to_one_line(self, spool):
        run_hook('{\n  "session_id": "s1",\n  "cwd": "/w"\n}', spool=spool)
        raw = next(spool.glob("*.json")).read_text()
        assert raw.count("\n") == 1
        assert json.loads(raw)["p"]["session_id"] == "s1"

    def test_a_giant_payload_is_dropped_not_spooled(self, spool):
        """afterShellExecution can carry megabytes of build log; we only ever need metadata."""
        huge = json.dumps({"session_id": "s1", "output": "x" * 50_000})
        run_hook(huge, spool=spool, AGENT_VILLAGE_MAX_BYTES="4096")
        assert spooled(spool)[0]["p"] == {"_truncated": True}

    def test_the_event_name_is_recovered_from_the_payload(self, spool):
        """Cursor may not forward arguments, so the payload is the fallback source of truth."""
        result = subprocess.run(
            ["bash", str(SCRIPT), "cursor"],
            input=json.dumps({"conversation_id": "c1", "hook_event_name": "stop", "status": "completed"}),
            capture_output=True,
            check=False,
            text=True,
            env={"HOME": "/tmp", "AGENT_VILLAGE_SPOOL": str(spool)},
        )
        assert result.returncode == 0
        assert spooled(spool)[0]["ev"] == "stop"

    def test_concurrent_hooks_do_not_lose_events(self, spool):
        """Several agents fire at once; one file per event means no interleaving."""
        procs = [
            subprocess.Popen(
                ["bash", str(SCRIPT), "claude", "PostToolUse"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                env={"HOME": "/tmp", "AGENT_VILLAGE_SPOOL": str(spool)},
            )
            for _ in range(25)
        ]
        for i, p in enumerate(procs):
            p.communicate(json.dumps({"session_id": f"s{i}", "cwd": "/w"}))
        assert len(spooled(spool)) == 25

    def test_it_records_no_pid_when_no_agent_is_in_the_parent_chain(self, spool):
        """A wrong pid is worse than no pid: the reaper would retire a live agent."""
        run_hook('{"session_id":"s1"}', spool=spool, AGENT_VILLAGE_AGENT_COMMS="no-such-process-name")
        assert spooled(spool)[0]["pid"] == 0

    def test_it_finds_the_agent_process_by_walking_up(self, spool):
        """Point the walk at this test process and it must come back with exactly that pid."""
        import os

        own_comm = Path("/proc/self/comm").read_text().strip()
        run_hook('{"session_id":"s1"}', spool=spool, AGENT_VILLAGE_AGENT_COMMS=own_comm)
        assert spooled(spool)[0]["pid"] == os.getpid()
