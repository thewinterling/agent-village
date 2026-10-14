"""How raw hook payloads become village events.

These double as the contract against the two upstream hook schemas: if Claude Code or Cursor
rename a field, a test here fails loudly instead of the village silently going quiet.
"""

from __future__ import annotations

import json

import pytest

from agent_village.adapters import parse_spool_line
from agent_village.models import EventKind, Source
from conftest import at, claude_line, cursor_line


def one(line: str):
    events = parse_spool_line(line)
    assert len(events) == 1, f"expected exactly one event, got {events}"
    return events[0]


class TestClaudeCode:
    def test_session_start_spawns(self):
        e = one(claude_line("SessionStart", source="startup", model="claude-opus-5-5"))
        assert e.kind is EventKind.SESSION_START
        assert e.source is Source.CLAUDE
        assert e.session_id == "sess-claude"
        assert e.model == "claude-opus-5-5"

    @pytest.mark.parametrize("source", ["resume", "fork", "compact"])
    def test_resume_is_distinct_from_start(self, source):
        """A resumed session must not be mistaken for a new one, or you get twin avatars."""
        assert one(claude_line("SessionStart", source=source)).kind is EventKind.SESSION_RESUME

    def test_prompt_carries_text(self):
        e = one(claude_line("UserPromptSubmit", prompt="fix the flaky test"))
        assert e.kind is EventKind.PROMPT_SUBMIT
        assert e.text == "fix the flaky test"

    def test_tool_start_summarises_the_command(self):
        e = one(
            claude_line(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id="tu-1",
                tool_input={"command": "pytest -q", "description": "run tests"},
            )
        )
        assert (e.kind, e.tool_name, e.tool_id) == (EventKind.TOOL_START, "Bash", "tu-1")
        assert e.tool_detail == "pytest -q"

    def test_tool_detail_falls_back_to_file_path(self):
        e = one(claude_line("PreToolUse", tool_name="Read", tool_input={"file_path": "/tmp/x.py"}))
        assert e.tool_detail == "/tmp/x.py"

    def test_permission_request_is_the_live_waiting_signal(self):
        """PermissionRequest fires immediately; Notification/permission_prompt waits ~6s."""
        e = one(claude_line("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build"}))
        assert e.kind is EventKind.NEEDS_INPUT
        assert e.reason == "permission_request"
        assert "rm -rf build" in e.text

    @pytest.mark.parametrize(
        "ntype", ["permission_prompt", "agent_needs_input", "elicitation_dialog", "quota_auto_resume_stale"]
    )
    def test_blocking_notifications_need_input(self, ntype):
        e = one(claude_line("Notification", notification_type=ntype, message="Claude needs your permission"))
        assert e.kind is EventKind.NEEDS_INPUT
        assert e.reason == ntype

    @pytest.mark.parametrize("ntype", ["idle_prompt", "agent_completed"])
    def test_idle_means_finished_not_stuck(self, ntype):
        """idle_prompt is 'you have not looked yet', not 'the agent is blocked'."""
        assert one(claude_line("Notification", notification_type=ntype)).kind is EventKind.COMPLETED

    @pytest.mark.parametrize("ntype", ["auth_success", "elicitation_complete", "quota_auto_resume_fired"])
    def test_noise_notifications_are_dropped(self, ntype):
        assert parse_spool_line(claude_line("Notification", notification_type=ntype)) == []

    def test_stop_uses_last_assistant_message(self):
        """The docs warn the transcript lags at Stop time, so the payload field is the truth."""
        e = one(claude_line("Stop", last_assistant_message="All 42 tests pass."))
        assert e.kind is EventKind.TURN_END
        assert e.text == "All 42 tests pass."

    def test_subagent_gets_its_own_avatar_id(self):
        e = one(claude_line("PreToolUse", agent_id="ag-7", agent_type="explore", tool_name="Grep"))
        assert e.session_id == "sess-claude:ag-7"
        assert e.parent_session_id == "sess-claude"
        assert e.agent_type == "explore"

    def test_session_end_reports_reason(self):
        e = one(claude_line("SessionEnd", reason="prompt_input_exit"))
        assert (e.kind, e.reason) == (EventKind.SESSION_END, "prompt_input_exit")


class TestCursor:
    def test_conversation_id_is_the_identity(self):
        e = one(cursor_line("sessionStart", conversation_id="conv-9", is_background_agent=True))
        assert e.session_id == "conv-9"
        assert e.source is Source.CURSOR
        assert e.is_background is True

    def test_cwd_falls_back_to_first_workspace_root(self):
        e = one(cursor_line("beforeSubmitPrompt", prompt="hi", workspace_roots=["/w/a", "/w/b"]))
        assert e.cwd == "/w/a"

    def test_thought_accumulates_thinking_time(self):
        e = one(cursor_line("afterAgentThought", text="considering options", duration_ms=5000))
        assert (e.kind, e.thinking_ms) == (EventKind.THOUGHT, 5000)

    def test_shell_execution_closes_a_shell_tool(self):
        e = one(cursor_line("afterShellExecution", command="yarn test", output="ok", duration=1234))
        assert (e.kind, e.tool_name, e.tool_detail) == (EventKind.TOOL_END, "Shell", "yarn test")

    def test_file_edit_records_the_path(self):
        e = one(cursor_line("afterFileEdit", file_path="/w/a/src/x.ts", edits=[]))
        assert (e.kind, e.file_path) == (EventKind.FILE_EDIT, "/w/a/src/x.ts")

    def test_failed_tool_blocks(self):
        e = one(
            cursor_line(
                "postToolUseFailure", tool_name="Shell", failure_type="timeout", error_message="timed out after 30s"
            )
        )
        assert e.kind is EventKind.TOOL_FAIL
        assert e.reason == "timed out after 30s"

    def test_past_tense_denial_does_not_block(self):
        """`permission_denied` here means the user already said no, not that a prompt is open."""
        e = one(cursor_line("postToolUseFailure", tool_name="Shell", failure_type="permission_denied"))
        assert e.kind is EventKind.TOOL_END

    @pytest.mark.parametrize(
        ("status", "kind"),
        [("completed", EventKind.TURN_END), ("aborted", EventKind.TURN_FAIL), ("error", EventKind.TURN_FAIL)],
    )
    def test_stop_status_decides_cafe_or_infirmary(self, status, kind):
        assert one(cursor_line("stop", status=status, loop_count=0)).kind is kind


class TestRobustness:
    """A hook schema we do not control must degrade the village, never crash the daemon."""

    @pytest.mark.parametrize("line", ["", "   ", "not json", "[1,2,3]", '"a string"', "null"])
    def test_garbage_lines_are_ignored(self, line):
        assert parse_spool_line(line) == []

    def test_unknown_event_name_is_ignored(self):
        assert parse_spool_line(claude_line("SomeFutureEvent2029")) == []

    def test_unknown_source_is_ignored(self):
        assert parse_spool_line(json.dumps({"src": "codex", "ev": "Stop", "p": {}})) == []

    def test_event_without_session_id_is_ignored(self):
        assert parse_spool_line(json.dumps({"src": "claude", "ev": "Stop", "p": {"cwd": "/w"}})) == []

    def test_missing_timestamp_does_not_raise(self):
        events = parse_spool_line(json.dumps({"src": "claude", "ev": "Stop", "p": {"session_id": "s"}}))
        assert len(events) == 1 and events[0].ts is not None

    def test_timestamp_with_z_suffix_parses(self):
        e = one(claude_line("Stop", at(5)))
        assert e.ts == at(5)
