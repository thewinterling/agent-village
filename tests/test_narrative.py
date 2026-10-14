"""What a session actually got through, read back out of its transcript.

The drawer used to show only the last prompt and the last reply, which says nothing about a
session that has been running for three hours. The transcript already holds the whole story:
every prompt, and the agent's own sign-off for each one. These tests pin down how that flat
stream of records is folded back into turns, and what happens to the shapes that do not fit -
a turn still in flight, a sidechain belonging to a subagent, a half-written last line.
"""

from __future__ import annotations

import json
import os

import pytest

from agent_village import transcript as tx
from agent_village.daemon import Village, VillageConfig
from agent_village.models import Source
from agent_village.transcript import session_narrative, transcript_for
from conftest import at, claude_line

SESSION = "sess-claude"


def ask(text: str, *, seconds: float = 0, **extra) -> str:
    return json.dumps({"type": "user", "timestamp": at(seconds).isoformat(), "message": {"content": text}, **extra})


def said(text: str, **extra) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}, **extra})


def tool(name: str = "Read") -> str:
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": {}}]}})


@pytest.fixture
def claude(tmp_path, monkeypatch):
    """Writes a Claude transcript for SESSION and points the module at it."""
    projects = tmp_path / "projects"
    (projects / "-w-repo").mkdir(parents=True)
    monkeypatch.setattr(tx, "CLAUDE_PROJECTS", projects)

    def write(*lines: str, session: str = SESSION):
        path = projects / "-w-repo" / f"{session}.jsonl"
        path.write_text("\n".join(lines) + "\n")
        return path

    return write


class TestFoldingATranscriptIntoTurns:
    def test_each_prompt_is_paired_with_how_that_turn_ended(self, claude):
        claude(
            ask("run the tests", seconds=0),
            tool(),
            said("All 1319 tests pass."),
            ask("now open the MR", seconds=60),
            said("Opened !2817."),
        )
        turns = session_narrative(SESSION, Source.CLAUDE)

        assert [(t.ask, t.outcome) for t in turns] == [
            ("run the tests", "All 1319 tests pass."),
            ("now open the MR", "Opened !2817."),
        ]

    def test_the_turn_keeps_the_last_thing_said_not_the_first(self, claude):
        """Agents narrate as they go. Only the closing line describes the finished turn."""
        claude(
            ask("fix the flake"),
            said("Let me look at the failing test first."),
            tool(),
            said("It was a missing await. Fixed and the suite is green."),
        )
        (turn,) = session_narrative(SESSION, Source.CLAUDE)

        assert turn.outcome == "It was a missing await. Fixed and the suite is green."

    def test_a_turn_still_running_has_no_outcome_yet(self, claude):
        claude(ask("done"), said("Done."), ask("now deploy"), tool())
        turns = session_narrative(SESSION, Source.CLAUDE)

        assert turns[-1].ask == "now deploy"
        assert turns[-1].outcome is None, "nothing should be invented for a turn in flight"

    def test_the_work_done_in_a_turn_is_counted(self, claude):
        claude(ask("refactor it"), tool("Read"), tool("Edit"), said("thinking"), tool("Shell"), said("Done."))
        (turn,) = session_narrative(SESSION, Source.CLAUDE)

        assert turn.tool_calls == 3

    def test_turns_carry_the_time_they_were_asked(self, claude):
        claude(ask("first", seconds=0), said("ok"), ask("second", seconds=120), said("ok"))
        turns = session_narrative(SESSION, Source.CLAUDE)

        assert (turns[1].at - turns[0].at).total_seconds() == 120

    def test_only_the_tail_is_kept(self, claude):
        claude(*[line for n in range(10) for line in (ask(f"ask {n}"), said(f"out {n}"))])
        turns = session_narrative(SESSION, Source.CLAUDE, limit=3)

        assert [t.ask for t in turns] == ["ask 7", "ask 8", "ask 9"]


class TestWhatIsLeftOut:
    def test_a_subagents_sidechain_is_not_part_of_the_story(self, claude):
        """Sidechain records are a subagent's own conversation. It has its own avatar."""
        claude(
            ask("delegate this"),
            ask("you are a subagent, go", isSidechain=True),
            said("Subagent finished.", isSidechain=True),
            said("The subagent reported back; merged its work."),
        )
        turns = session_narrative(SESSION, Source.CLAUDE)

        assert [t.ask for t in turns] == ["delegate this"]
        assert turns[0].outcome == "The subagent reported back; merged its work."

    def test_tool_results_posing_as_prompts_are_ignored(self, claude):
        """Tool output comes back as a `user` record. It is not something the human asked."""
        claude(ask("check CI"), ask("<tool_result>exit 0</tool_result>"), said("CI is green."))
        turns = session_narrative(SESSION, Source.CLAUDE)

        assert [t.ask for t in turns] == ["check CI"]

    def test_assistant_chatter_before_the_first_prompt_is_dropped(self, claude):
        claude(said("resuming where we left off"), ask("carry on"), said("Done."))
        turns = session_narrative(SESSION, Source.CLAUDE)

        assert len(turns) == 1

    def test_a_half_written_last_line_does_not_lose_the_rest(self, claude, tmp_path):
        """The file is live. The record being appended as we read is simply skipped."""
        path = claude(ask("go"), said("Done."))
        path.write_text(path.read_text() + '{"type": "user", "mess')

        turns = session_narrative(SESSION, Source.CLAUDE)

        assert [(t.ask, t.outcome) for t in turns] == [("go", "Done.")]

    def test_long_text_is_clipped_rather_than_sent_whole(self, claude):
        claude(ask("x" * 500), said("y" * 900))
        (turn,) = session_narrative(SESSION, Source.CLAUDE)

        assert len(turn.ask) < 260 and len(turn.outcome) < 420


class TestTheDrawer:
    """Opening a drawer must not re-parse megabytes of transcript every time."""

    def village(self, tmp_path) -> Village:
        village = Village(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False, backfill_within=None))
        village.config.spool_dir.mkdir(parents=True, exist_ok=True)
        village.config.spool_dir.joinpath("1760000000.000000-0.json").write_text(claude_line("UserPromptSubmit", at(0)))
        village.poll_once(at(1))
        return village

    def test_the_detail_carries_the_story(self, tmp_path, claude):
        claude(ask("run the tests"), said("All green."))
        detail = self.village(tmp_path).detail(SESSION)

        assert [t["ask"] for t in detail["narrative"]] == ["run the tests"]
        assert detail["narrative"][0]["outcome"] == "All green."

    def test_an_unchanged_transcript_is_only_parsed_once(self, tmp_path, claude, monkeypatch):
        claude(ask("go"), said("Done."))
        village = self.village(tmp_path)
        reads = {"n": 0}
        real = tx.session_narrative

        def counted(*args, **kwargs):
            reads["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr("agent_village.daemon.session_narrative", counted)
        village.detail(SESSION)
        village.detail(SESSION)

        assert reads["n"] == 1

    def test_a_session_that_has_moved_on_is_read_again(self, tmp_path, claude, monkeypatch):
        path = claude(ask("go"), said("Done."))
        village = self.village(tmp_path)
        assert len(village.detail(SESSION)["narrative"]) == 1

        path.write_text(path.read_text() + ask("and again") + "\n" + said("Done again.") + "\n")
        # mtime granularity would otherwise hide a change made in the same instant.
        os.utime(path, (0, path.stat().st_mtime + 2))

        assert len(village.detail(SESSION)["narrative"]) == 2


class TestFindingTheTranscript:
    def test_a_session_id_resolves_to_its_file(self, claude):
        path = claude(ask("hi"), said("hello"))
        assert transcript_for(SESSION, Source.CLAUDE) == path

    def test_an_unknown_session_has_no_transcript(self, claude):
        claude(ask("hi"))
        assert transcript_for("nobody-here", Source.CLAUDE) is None

    @pytest.mark.parametrize("nasty", ["../../../etc/passwd", "*", "sess/../other", "a b", ""])
    def test_a_session_id_from_a_url_is_never_pasted_into_a_glob(self, claude, nasty):
        """The id arrives from a URL path. Anything but a plain id is refused outright."""
        claude(ask("hi"))
        assert transcript_for(nasty, Source.CLAUDE) is None

    def test_a_missing_transcript_gives_an_empty_narrative(self, claude):
        assert session_narrative("sess-gone", Source.CLAUDE) == []


class TestCursorSessions:
    """Cursor records look different enough to be worth their own reader.

    Its `user` records carry everything the model was handed - a timestamp preamble, attached
    skills, pasted files - alongside the prompt itself, so picking the prompt back out is the
    whole job.
    """

    @pytest.fixture
    def cursor(self, tmp_path, monkeypatch):
        projects = tmp_path / "cursor"
        folder = projects / "proj" / "agent-transcripts" / "conv"
        folder.mkdir(parents=True)
        monkeypatch.setattr(tx, "CURSOR_PROJECTS", projects)

        def write(*records: dict):
            folder.joinpath("conv-cursor.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

        return write

    def user(self, *texts: str) -> dict:
        return {"role": "user", "message": {"content": [{"type": "text", "text": t} for t in texts]}}

    def assistant(self, text: str) -> dict:
        return {"role": "assistant", "message": {"content": [{"type": "text", "text": text}]}}

    def test_a_cursor_transcript_reads_the_same_way(self, cursor):
        cursor(
            self.user("<timestamp>Wed, Oct 14</timestamp>\n<user_query>\ntidy the imports\n</user_query>"),
            self.assistant("Tidied."),
        )
        (turn,) = session_narrative("conv-cursor", Source.CURSOR)

        assert (turn.ask, turn.outcome) == ("tidy the imports", "Tidied.")

    def test_context_cursor_injects_is_not_mistaken_for_a_prompt(self, cursor):
        cursor(
            self.user("<manually_attached_skills>\nSkill Name: mono\n...\n</manually_attached_skills>"),
            self.user("<user_query>now ship it</user_query>"),
            self.assistant("Shipped."),
        )
        turns = session_narrative("conv-cursor", Source.CURSOR)

        assert [t.ask for t in turns] == ["now ship it"]
