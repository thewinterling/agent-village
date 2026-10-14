"""The daemon loop, driven synchronously - no event loop, no sockets."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from agent_village.daemon import Village, VillageConfig
from agent_village.models import AgentState, Source
from agent_village.reaper import ReaperConfig, dead_sessions
from agent_village.reducer import ReducerConfig
from agent_village.spool import SpoolReader
from conftest import at, claude_line, cursor_line

EMIT = Path(__file__).resolve().parents[1] / "hooks" / "village-emit.sh"


@pytest.fixture
def fake_proc(tmp_path) -> Path:
    """A /proc containing exactly the pids the fixtures emit, and nothing else.

    Pointing the reaper at the real /proc would make these tests depend on whether the host
    happens to have a process at pid 4242 right now. It usually does not, but pids get recycled,
    so the suite would pass or fail according to the machine's mood.
    """
    proc = tmp_path / "proc"
    for pid in (4242, 11201):
        (proc / str(pid)).mkdir(parents=True)
    return proc


@pytest.fixture
def village(tmp_path, fake_proc) -> Village:
    return Village(
        VillageConfig(
            spool_dir=tmp_path / "spool",
            db_path=tmp_path / "history.sqlite3",
            backfill_within=timedelta(0),
            reaper=ReaperConfig(proc_root=fake_proc),
        )
    )


def drop(spool: Path, line: str) -> None:
    """Write a spool file the way the hook script does, with a sortable name."""
    spool.mkdir(parents=True, exist_ok=True)
    seq = len(list(spool.glob("*.json")))
    (spool / f"{1760000000 + seq:010d}.000000-{seq}.json").write_text(line)


class TestSpoolReader:
    def test_events_arrive_in_timestamp_order(self, tmp_path):
        spool = tmp_path / "spool"
        reader = SpoolReader(spool)
        drop(spool, claude_line("UserPromptSubmit", at(0), prompt="first"))
        drop(spool, claude_line("Stop", at(1)))
        kinds = [e.kind.value for e in reader.drain()]
        assert kinds == ["prompt_submit", "turn_end"]

    def test_files_are_consumed_exactly_once(self, tmp_path):
        spool = tmp_path / "spool"
        reader = SpoolReader(spool)
        drop(spool, claude_line("Stop", at(0)))
        assert len(reader.drain()) == 1
        assert reader.drain() == []
        assert list(spool.glob("*.json")) == []

    def test_a_corrupt_file_does_not_block_the_rest(self, tmp_path):
        spool = tmp_path / "spool"
        reader = SpoolReader(spool)
        drop(spool, "{ truncated")
        drop(spool, claude_line("Stop", at(1)))
        assert len(reader.drain()) == 1

    def test_a_huge_backlog_is_trimmed_rather_than_replayed(self, tmp_path):
        """A daemon that was down for days should not resurrect a week of dead sessions."""
        spool = tmp_path / "spool"
        reader = SpoolReader(spool, max_batch=10)
        for _ in range(25):
            drop(spool, claude_line("Stop", at(0)))
        assert len(reader.drain()) == 10
        assert reader.dropped == 15


class TestPollLoop:
    def test_spooled_events_become_agents(self, village):
        drop(village.config.spool_dir, claude_line("UserPromptSubmit", at(0), prompt="go"))
        assert village.poll_once(at(1)) is True
        assert village.world.agents["sess-claude"].state is AgentState.THINKING

    def test_poll_is_quiet_when_nothing_happens(self, village):
        village.poll_once(at(0))
        assert village.poll_once(at(1)) is False

    def test_time_passing_alone_moves_an_agent_to_the_bench(self, village):
        drop(village.config.spool_dir, claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t1"))
        village.poll_once(at(1))
        village.poll_once(at(500))
        agent = village.world.agents["sess-claude"]
        assert agent.state is AgentState.WAITING
        assert agent.waiting_inferred is True

    def test_history_records_every_transition(self, village):
        drop(village.config.spool_dir, claude_line("UserPromptSubmit", at(0), prompt="go"))
        drop(village.config.spool_dir, claude_line("Stop", at(1)))
        village.poll_once(at(2))
        states = [t["to_state"] for t in village.store.recent_transitions()]
        assert "done" in states

    def test_departed_agents_linger_then_leave(self, village):
        """The client needs one frame showing GONE so it can animate the exit."""
        drop(village.config.spool_dir, claude_line("SessionStart", at(0)))
        drop(village.config.spool_dir, claude_line("SessionEnd", at(1), reason="logout"))
        village.poll_once(at(2))
        assert village.world.agents["sess-claude"].state is AgentState.GONE
        village.poll_once(at(120))
        assert "sess-claude" not in village.world.agents


class TestReaper:
    def test_a_claude_agent_whose_process_vanished_is_retired(self, village, tmp_path):
        empty = tmp_path / "empty-proc"
        empty.mkdir()
        # VillageConfig is frozen, so swap in a /proc with nothing in it.
        village.config = replace(village.config, reaper=ReaperConfig(proc_root=empty))
        drop(village.config.spool_dir, claude_line("UserPromptSubmit", at(0), pid=999999, prompt="go"))
        village.poll_once(at(1))
        assert village.world.agents["sess-claude"].state is AgentState.GONE

    def test_a_live_process_is_left_alone(self, village):
        # The village fixture's fake /proc contains 4242, the pid claude_line reports.
        drop(village.config.spool_dir, claude_line("UserPromptSubmit", at(0), pid=4242, prompt="go"))
        village.poll_once(at(1))
        assert village.world.agents["sess-claude"].state is AgentState.THINKING

    def test_a_sleeping_agent_is_left_in_its_bed(self):
        """An agent only reaches the hotel after an hour of silence, by which point its
        terminal has usually been closed. Reaping on that would empty the hotel as fast as it
        filled, and the point of the hotel is to still have the day's work on the map."""
        from agent_village.adapters import parse_spool_line
        from agent_village.models import World
        from agent_village.reducer import apply, tick

        world = World()
        for e in parse_spool_line(claude_line("Stop", at(0), pid=999999)):
            world = apply(world, e)
        world = tick(world, at(7200))

        assert world.agents["sess-claude"].state is AgentState.SLEEPING
        assert dead_sessions(world, ReaperConfig(proc_root=Path("/nonexistent"))) == []

    def test_cursor_agents_are_never_reaped_on_pid(self):
        """The Cursor pid is the shared editor process, so it says nothing about one chat."""
        from agent_village.adapters import parse_spool_line
        from agent_village.models import World
        from agent_village.reducer import apply

        world = World()
        for e in parse_spool_line(cursor_line("afterAgentResponse", at(0), pid=999999, text="hi")):
            world = apply(world, e)
        assert world.agents["conv-cursor"].source is Source.CURSOR
        assert dead_sessions(world, ReaperConfig(proc_root=Path("/nonexistent"))) == []


class TestSnapshot:
    def test_snapshot_carries_a_stable_appearance(self, village):
        drop(village.config.spool_dir, claude_line("UserPromptSubmit", at(0), prompt="go"))
        village.poll_once(at(1))
        first = village.snapshot()["agents"][0]["appearance"]
        second = village.snapshot()["agents"][0]["appearance"]
        assert first == second
        assert first["body"].startswith("#")

    def test_snapshot_is_json_serialisable(self, village):
        drop(village.config.spool_dir, claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t"))
        village.poll_once(at(1))
        assert json.loads(json.dumps(village.snapshot()))["counts"]["working"] == 1

    def test_detail_offers_a_resume_command_for_claude(self, village):
        drop(village.config.spool_dir, claude_line("Stop", at(0)))
        village.poll_once(at(1))
        detail = village.detail("sess-claude")
        assert detail["resumeCommand"] == "claude --resume sess-claude"
        assert isinstance(detail["timeline"], list)

    def test_detail_is_none_for_an_unknown_agent(self, village):
        assert village.detail("nope") is None


class TestEndToEnd:
    def test_the_real_hook_script_feeds_the_real_daemon(self, village):
        """No fixtures: run the shell hook, then poll the daemon that consumes what it wrote.

        The real script records a real pid, so this is the one case that wants the real /proc.
        """
        village.config = replace(village.config, reaper=ReaperConfig())
        for event, payload in [
            ("SessionStart", {"session_id": "e2e", "cwd": "/w/repo", "source": "startup"}),
            ("UserPromptSubmit", {"session_id": "e2e", "cwd": "/w/repo", "prompt": "ship it"}),
            (
                "PreToolUse",
                {
                    "session_id": "e2e",
                    "cwd": "/w/repo",
                    "tool_name": "Bash",
                    "tool_use_id": "t1",
                    "tool_input": {"command": "pytest"},
                },
            ),
        ]:
            subprocess.run(
                ["bash", str(EMIT), "claude", event],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env={"HOME": "/tmp", "AGENT_VILLAGE_SPOOL": str(village.config.spool_dir)},
                check=True,
            )

        village.poll_once()
        agent = village.world.agents["e2e"]
        assert agent.state is AgentState.WORKING
        assert agent.repo == "repo"
        assert agent.last_prompt == "ship it"
        assert agent.current_tool == "Bash"
        assert agent.open_tools[0].detail == "pytest"


class TestConfigurability:
    def test_thresholds_are_tunable_without_touching_the_reducer(self, tmp_path, fake_proc):
        village = Village(
            VillageConfig(
                spool_dir=tmp_path / "spool",
                enable_history=False,
                reducer=ReducerConfig(dangling_tool_s=5),
                reaper=ReaperConfig(proc_root=fake_proc),
            )
        )
        drop(village.config.spool_dir, claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t"))
        village.poll_once(at(1))
        assert village.world.agents["sess-claude"].state is AgentState.WORKING
        village.poll_once(at(10))
        assert village.world.agents["sess-claude"].state is AgentState.WAITING

    def test_history_can_be_disabled(self, tmp_path):
        village = Village(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False))
        drop(village.config.spool_dir, claude_line("Stop", at(0)))
        village.poll_once(at(1))
        assert village.store is None
        assert "stats" not in village.snapshot()
