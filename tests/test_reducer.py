"""How a stream of events becomes a village.

Read top to bottom these are the specification of where every avatar stands and why.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_village.adapters import parse_spool_line
from agent_village.models import AgentState, Source, World
from agent_village.reducer import ReducerConfig, apply, tick
from conftest import at, claude_line, cursor_line


def play(*lines: str, world: World | None = None, config: ReducerConfig | None = None) -> World:
    """Feed spool lines through the full adapter + reducer pipeline."""
    world = world or World()
    config = config or ReducerConfig()
    for line in lines:
        for event in parse_spool_line(line):
            world = apply(world, event, config)
    return world


def only(world: World):
    assert len(world.agents) == 1, f"expected one agent, got {list(world.agents)}"
    return next(iter(world.agents.values()))


class TestTurnLifecycle:
    def test_a_whole_turn_walks_the_expected_path(self):
        world = play(claude_line("SessionStart", at(0), source="startup"))
        assert only(world).state is AgentState.SPAWNING

        world = play(claude_line("UserPromptSubmit", at(1), prompt="fix the build"), world=world)
        assert only(world).state is AgentState.THINKING

        world = play(
            claude_line("PreToolUse", at(2), tool_name="Bash", tool_use_id="t1", tool_input={"command": "make"}),
            world=world,
        )
        agent = only(world)
        assert agent.state is AgentState.WORKING
        assert agent.current_tool == "Bash"

        world = play(claude_line("PostToolUse", at(5), tool_name="Bash", tool_use_id="t1"), world=world)
        agent = only(world)
        assert agent.state is AgentState.THINKING
        assert agent.current_tool is None
        assert agent.recent_tools[-1].duration_ms == 3000

        world = play(claude_line("Stop", at(6), last_assistant_message="Build fixed."), world=world)
        agent = only(world)
        assert agent.state is AgentState.DONE
        assert agent.last_assistant == "Build fixed."
        assert agent.turns == 1

    def test_prompt_counts_turns_and_clears_attention(self):
        world = play(
            claude_line("Stop", at(0)),
            claude_line("Notification", at(60), notification_type="idle_prompt"),
        )
        assert only(world).needs_attention is True

        world = play(claude_line("UserPromptSubmit", at(70), prompt="carry on"), world=world)
        agent = only(world)
        assert agent.needs_attention is False
        assert agent.turns == 1

    def test_parallel_tools_keep_working_until_the_last_one_closes(self):
        world = play(
            claude_line("PreToolUse", at(0), tool_name="Read", tool_use_id="a"),
            claude_line("PreToolUse", at(0), tool_name="Grep", tool_use_id="b"),
            claude_line("PostToolUse", at(1), tool_name="Read", tool_use_id="a"),
        )
        assert only(world).state is AgentState.WORKING
        world = play(claude_line("PostToolUse", at(2), tool_name="Grep", tool_use_id="b"), world=world)
        assert only(world).state is AgentState.THINKING


class TestWaiting:
    def test_permission_request_parks_on_the_bench(self):
        world = play(claude_line("PermissionRequest", at(0), tool_name="Bash", tool_input={"command": "rm -rf x"}))
        agent = only(world)
        assert agent.state is AgentState.WAITING
        assert agent.waiting_inferred is False, "a reported prompt is a fact, not a guess"

    def test_approving_the_prompt_sends_them_back_to_work(self):
        world = play(
            claude_line("PermissionRequest", at(0), tool_name="Bash"),
            claude_line("PreToolUse", at(9), tool_name="Bash", tool_use_id="t1"),
        )
        assert only(world).state is AgentState.WORKING

    def test_idle_prompt_goes_to_the_cafe_not_the_bench(self):
        """Deviation from plan 0077 section 3.1, documented in the reducer module docstring."""
        world = play(
            claude_line("Stop", at(0), last_assistant_message="done"),
            claude_line("Notification", at(60), notification_type="idle_prompt"),
        )
        agent = only(world)
        assert agent.state is AgentState.DONE
        assert agent.needs_attention is True


class TestFailures:
    def test_tool_failure_blocks_and_records_why(self):
        world = play(
            claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t1"),
            claude_line("PostToolUseFailure", at(1), tool_name="Bash", tool_use_id="t1", error="exit 1"),
        )
        agent = only(world)
        assert agent.state is AgentState.BLOCKED
        assert agent.waiting_reason == "exit 1"
        assert agent.recent_tools[-1].ok is False

    def test_blocked_survives_until_something_else_happens(self):
        """A failure must not be erased by the next tool starting - it decays on a timer instead."""
        world = play(
            claude_line("PostToolUseFailure", at(0), tool_name="Bash", error="boom"),
            claude_line("PreToolUse", at(1), tool_name="Read", tool_use_id="t2"),
        )
        assert only(world).state is AgentState.WORKING

    def test_stop_failure_blocks(self):
        assert only(play(claude_line("StopFailure", at(0), reason="api error"))).state is AgentState.BLOCKED


class TestIdentityAndIsolation:
    def test_resume_reuses_the_avatar_instead_of_spawning_a_twin(self):
        world = play(
            claude_line("SessionStart", at(0), source="startup"),
            claude_line("Stop", at(5)),
            claude_line("SessionStart", at(10), source="resume"),
        )
        assert len(world.agents) == 1
        assert only(world).spawned_at == at(0)

    def test_two_sessions_do_not_cross_contaminate(self):
        world = play(
            claude_line("PreToolUse", at(0), session_id="s1", tool_name="Bash", tool_use_id="t1"),
            claude_line("Stop", at(1), session_id="s2"),
        )
        assert world.agents["s1"].state is AgentState.WORKING
        assert world.agents["s2"].state is AgentState.DONE

    def test_a_subagent_is_a_separate_avatar_that_knows_its_parent(self):
        world = play(
            claude_line("UserPromptSubmit", at(0), prompt="explore"),
            claude_line("PreToolUse", at(1), agent_id="ag1", agent_type="explore", tool_name="Grep"),
        )
        child = world.agents["sess-claude:ag1"]
        assert child.parent_session_id == "sess-claude"
        assert child.is_subagent is True
        assert world.agents["sess-claude"].is_subagent is False

    def test_daemon_starting_mid_session_still_shows_the_agent(self):
        """Hooks only fire going forward, so the first event we ever see may be a PostToolUse."""
        world = play(claude_line("PostToolUse", at(0), tool_name="Bash"))
        agent = only(world)
        assert agent.state is AgentState.THINKING
        assert agent.tool_count == 1, "a tool we never saw start still belongs in the timeline"

    def test_session_end_retires_the_avatar(self):
        world = play(claude_line("SessionStart", at(0)), claude_line("SessionEnd", at(1), reason="logout"))
        assert only(world).state is AgentState.GONE
        assert world.live() == []


class TestBusyAgentsLookBusy:
    """`thinking` vs `working` is the difference between an avatar idling and one at a desk.

    The two sources report tool use very differently, and both used to end up understating it.
    """

    def test_a_cursor_agent_running_tools_reads_as_working(self):
        """Cursor only ever reports *completions*.

        Every "before" hook there is a permission hook we refuse to register, so the tool stack
        is permanently empty. Treating that as "no tool running" left a Cursor agent labelled
        `thinking` for a whole turn of solid tool use.
        """
        world = play(
            cursor_line("beforeSubmitPrompt", at(0), prompt="fix the build"),
            cursor_line("afterShellExecution", at(1), command="pytest"),
        )
        assert only(world).state is AgentState.WORKING

    def test_a_cursor_agent_still_settles_once_the_turn_ends(self):
        world = play(
            cursor_line("afterShellExecution", at(0), command="pytest"),
            cursor_line("afterAgentResponse", at(1), text="all green"),
        )
        assert only(world).state is AgentState.THINKING

        world = play(cursor_line("stop", at(2), status="completed"), world=world)
        assert only(world).state is AgentState.DONE

    def test_a_silent_cursor_agent_mid_tool_run_still_reaches_the_bench(self):
        """It cannot tell us it is waiting, so silence is the only clue - and it now goes
        silent from WORKING rather than THINKING."""
        world = play(cursor_line("afterShellExecution", at(0), command="pytest"))
        assert only(world).state is AgentState.WORKING

        world = tick(world, at(120), ReducerConfig())
        agent = only(world)
        assert agent.state is AgentState.WAITING
        assert (agent.waiting_inferred, agent.waiting_reason) == (True, "cursor_silence")

    def test_an_edit_reads_as_working_not_thinking(self):
        """`afterFileEdit` is Cursor's most frequent event; it used to read as idling."""
        world = play(cursor_line("afterFileEdit", at(0), file_path="/w/a.ts"))
        agent = only(world)
        assert agent.state is AgentState.WORKING
        assert agent.files_touched == ("/w/a.ts",)

    def test_one_finished_call_does_not_cancel_its_parallel_siblings(self):
        """Tool calls come in batches. One completion says nothing about the others."""
        world = play(
            claude_line("PreToolUse", at(0), tool_name="Read", tool_use_id="t1"),
            claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t2", tool_input={"command": "make"}),
            claude_line("PostToolUse", at(1), tool_name="Read", tool_use_id="t1"),
        )
        agent = only(world)
        assert agent.state is AgentState.WORKING, "the Bash call is still running"
        assert agent.current_tool == "Bash"

    def test_a_claude_agent_between_tools_is_still_thinking(self):
        """The counterpart: Claude *does* report starts, so an empty stack is meaningful."""
        world = play(
            claude_line("PreToolUse", at(0), tool_name="Read", tool_use_id="t1"),
            claude_line("PostToolUse", at(1), tool_name="Read", tool_use_id="t1"),
        )
        assert only(world).state is AgentState.THINKING


class TestContextAccumulation:
    def test_descriptive_fields_accumulate_across_events(self):
        world = play(
            claude_line("SessionStart", at(0), source="startup", model="claude-opus-5-5"),
            claude_line("UserPromptSubmit", at(1), prompt="do the thing"),
            cursor_line("afterFileEdit", at(2), conversation_id="sess-claude", file_path="/w/a.py"),
        )
        # The cursor line shares an id on purpose: context merging must not lose earlier values.
        agent = world.agents["sess-claude"]
        assert agent.model == "claude-opus-5-5"
        assert agent.last_prompt == "do the thing"

    def test_files_touched_are_deduplicated(self):
        world = play(
            cursor_line("afterFileEdit", at(0), file_path="/w/a.ts"),
            cursor_line("afterFileEdit", at(1), file_path="/w/a.ts"),
            cursor_line("afterFileEdit", at(2), file_path="/w/b.ts"),
        )
        assert only(world).files_touched == ("/w/a.ts", "/w/b.ts")

    def test_an_agent_is_named_for_where_it_is_working(self):
        world = play(claude_line("UserPromptSubmit", at(0), prompt="make the thing faster"))
        assert only(world).display_name() == "acme-platform"

    def test_the_branch_is_what_tells_two_agents_in_one_repo_apart(self):
        # Branches only reach the village through transcript enrichment, never through a hook.
        agent = replace(only(play(claude_line("UserPromptSubmit", at(0), prompt="go"))), git_branch="feat/github")
        assert agent.display_name() == "acme-platform / feat/github"

    def test_a_nameless_session_falls_back_to_its_id(self):
        world = play(claude_line("UserPromptSubmit", at(0), prompt="go", cwd=None))
        assert only(world).display_name() == "claude sess-cla"

    def test_the_subtitle_says_what_the_session_is_about(self):
        world = play(claude_line("UserPromptSubmit", at(0), prompt="make the thing faster"))
        assert only(world).subtitle() == "make the thing faster"

    def test_the_subtitle_prefers_a_generated_title_over_the_prompt(self):
        world = play(
            claude_line("UserPromptSubmit", at(0), prompt="make the thing faster"),
            claude_line("SessionStart", at(1), session_title="Speed up the exporter"),
        )
        assert only(world).subtitle() == "Speed up the exporter"

    def test_the_subtitle_skips_wrappers_and_attachments_to_find_the_request(self):
        """`<task-notification>` and a row of @file paths are not what the agent was asked."""
        prompt = "<task-notification>\n@workspace/acme-platform/ci.yml\nwhy is the job red?"
        world = play(claude_line("UserPromptSubmit", at(0), prompt=prompt))
        assert only(world).subtitle() == "why is the job red?"

    def test_repo_is_derived_from_cwd(self):
        world = play(claude_line("Stop", at(0), cwd="/home/u/workspace/acme-platform"))
        assert only(world).repo == "acme-platform"

    def test_a_hook_reporting_no_process_clears_a_stale_pid(self):
        """Otherwise a dead pid sticks and the reaper keeps retiring a session that is alive."""
        world = play(
            claude_line("UserPromptSubmit", at(0), pid=1234, prompt="go"),
            claude_line("PostToolUse", at(1), pid=0, tool_name="Bash"),
        )
        assert not only(world).pid

    def test_transcript_enrichment_does_not_clear_the_pid(self):
        """Transcript events know nothing about processes and must not speak for them."""
        from agent_village.models import EventKind, Source, VillageEvent
        from agent_village.reducer import apply

        world = play(claude_line("UserPromptSubmit", at(0), pid=1234, prompt="go"))
        world = apply(
            world,
            VillageEvent(
                source=Source.CLAUDE,
                session_id="sess-claude",
                kind=EventKind.TITLE,
                ts=at(2),
                title="A title",
            ),
        )
        assert only(world).pid == 1234
        assert only(world).title == "A title"


class TestTick:
    """Time-based transitions: how the village notices things that stopped happening."""

    def test_a_dangling_tool_becomes_an_inferred_bench_visit(self):
        world = play(claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t1"))
        world = tick(world, at(200), ReducerConfig(dangling_tool_s=90))
        agent = only(world)
        assert agent.state is AgentState.WAITING
        assert agent.waiting_inferred is True, "a guess must be visibly a guess"
        assert agent.waiting_reason == "tool_dangling"

    def test_a_short_tool_is_left_alone(self):
        world = play(claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t1"))
        assert only(tick(world, at(10), ReducerConfig(dangling_tool_s=90))).state is AgentState.WORKING

    def test_the_tool_finishing_clears_the_guess(self):
        world = play(claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t1"))
        world = tick(world, at(200), ReducerConfig(dangling_tool_s=90))
        world = play(claude_line("PostToolUse", at(201), tool_name="Bash", tool_use_id="t1"), world=world)
        agent = only(world)
        assert agent.state is AgentState.THINKING
        assert agent.waiting_inferred is False

    def test_cursor_silence_is_inferred_waiting(self):
        """Cursor exposes no permission event, so prolonged silence mid-turn is the only hint."""
        world = play(cursor_line("beforeSubmitPrompt", at(0), prompt="go"))
        world = tick(world, at(100), ReducerConfig(cursor_silence_waiting_s=45))
        agent = only(world)
        assert agent.state is AgentState.WAITING
        assert agent.waiting_inferred is True

    def test_claude_silence_is_not_inferred_waiting(self):
        """Claude reports permission prompts properly, so silence there needs no guessing."""
        world = play(claude_line("UserPromptSubmit", at(0), prompt="go"))
        world = tick(world, at(100), ReducerConfig(cursor_silence_waiting_s=45))
        assert only(world).state is AgentState.THINKING

    def test_blocked_decays_so_the_infirmary_empties(self):
        world = play(claude_line("StopFailure", at(0), reason="boom"))
        assert only(tick(world, at(300), ReducerConfig(blocked_decay_s=120))).state is AgentState.DONE

    def test_finished_agents_turn_in_for_the_night_rather_than_leaving(self):
        # They used to be retired outright, which threw away the morning's work at lunchtime.
        world = play(claude_line("Stop", at(0)))
        assert only(tick(world, at(7200), ReducerConfig(done_to_hotel_s=3600))).state is AgentState.SLEEPING

    def test_an_agent_still_finishing_up_is_left_in_the_cafe(self):
        world = play(claude_line("Stop", at(0)))
        assert only(tick(world, at(600), ReducerConfig(done_to_hotel_s=3600))).state is AgentState.DONE

    def test_the_cafe_is_the_only_way_into_the_hotel(self):
        # Nothing checks in straight from the workshop or the bench. Turning in is something
        # you do after you have finished, not instead of finishing.
        config = ReducerConfig(done_to_hotel_s=1, cursor_silence_waiting_s=1e9, dangling_tool_s=1e9)
        for line in (
            claude_line("UserPromptSubmit", at(0), prompt="go"),
            claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t1"),
            claude_line("Notification", at(0), notification_type="permission_prompt"),
            claude_line("PreCompact", at(0)),
        ):
            state = only(tick(play(line), at(60), config)).state
            assert state is not AgentState.SLEEPING, f"{line} checked into the hotel"

    def test_a_sleeping_agent_wakes_up_when_you_prompt_it(self):
        world = tick(play(claude_line("Stop", at(0))), at(7200), ReducerConfig(done_to_hotel_s=3600))
        assert only(world).state is AgentState.SLEEPING

        woken = play(claude_line("UserPromptSubmit", at(7300), prompt="one more"), world=world)
        assert only(woken).state is AgentState.THINKING

    def test_the_hotel_empties_after_a_day_so_it_holds_one_day_of_work(self):
        config = ReducerConfig(done_to_hotel_s=3600, stale_to_gone_s=86400)
        world = tick(play(claude_line("Stop", at(0))), at(7200), config)
        assert only(tick(world, at(86400 + 60), config)).state is AgentState.GONE

    def test_tick_is_idempotent(self):
        world = play(claude_line("PreToolUse", at(0), tool_name="Bash", tool_use_id="t1"))
        once = tick(world, at(200))
        twice = tick(once, at(200))
        assert once.agents["sess-claude"] == twice.agents["sess-claude"]

    def test_tick_never_resurrects_the_departed(self):
        world = play(claude_line("SessionEnd", at(0)))
        assert only(tick(world, at(99999))).state is AgentState.GONE


class TestSerialisation:
    def test_world_round_trips_to_json_friendly_dict(self):
        world = play(
            claude_line("UserPromptSubmit", at(0), prompt="hello"),
            claude_line("PreToolUse", at(1), tool_name="Bash", tool_use_id="t1", tool_input={"command": "ls"}),
        )
        payload = world.to_dict(at(2))
        agent = payload["agents"][0]
        assert agent["state"] == "working"
        assert agent["currentTool"] == "Bash"
        assert agent["openTools"][0]["detail"] == "ls"
        assert agent["stateAgeS"] == pytest.approx(1.0)

    def test_long_text_is_clipped_so_frames_stay_small(self):
        world = play(claude_line("UserPromptSubmit", at(0), prompt="x" * 9000))
        assert len(only(world).last_prompt) <= 2000


class TestSourceTagging:
    def test_source_survives_the_pipeline(self):
        world = play(claude_line("Stop", at(0)), cursor_line("stop", at(0), status="completed"))
        sources = {a.source for a in world.agents.values()}
        assert sources == {Source.CLAUDE, Source.CURSOR}
