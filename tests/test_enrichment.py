"""Reading transcripts while they are still being written.

Hooks carry state; titles, token counts and merge request links exist only in the transcript.
Reading those once at startup meant an MR opened while the village was running never showed up.
These tests pin down the incremental read, whose two failure modes are both silent: dropping a
record that was half-written at the moment we looked, and replaying one we already counted.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from agent_village.daemon import Village, VillageConfig
from agent_village.models import EventKind
from agent_village.transcript import claude_enrichment, read_claude_enrichment
from conftest import at


def record(**fields) -> str:
    base = {"sessionId": "sess-claude", "cwd": "/w/repo", "timestamp": at(0).isoformat()}
    return json.dumps({**base, **fields}) + "\n"


def title(text: str) -> str:
    return record(type="ai-title", aiTitle=text)


def renamed(text: str) -> str:
    """What `/rename` writes. Claude Code leaves the timestamp off this one."""
    return json.dumps({"type": "custom-title", "customTitle": text, "sessionId": "sess-claude"}) + "\n"


def mr(number: int, repo: str = "acme/acme-platform") -> str:
    return record(
        type="pr-link",
        prUrl=f"https://gitlab.example/{repo}/-/merge_requests/{number}",
        prNumber=number,
        prRepository=repo,
    )


def usage(tokens_in: int, tokens_out: int) -> str:
    return record(
        type="assistant",
        message={"model": "claude-opus-5-5", "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}},
    )


class TestIncrementalRead:
    def test_it_returns_nothing_for_a_file_that_has_not_grown(self, tmp_path):
        path = tmp_path / "sess.jsonl"
        path.write_text(title("first"))
        _, offset = read_claude_enrichment(path, 0)
        assert read_claude_enrichment(path, offset) == ([], offset)

    def test_it_only_reads_what_was_appended(self, tmp_path):
        path = tmp_path / "sess.jsonl"
        path.write_text(title("first"))
        first, offset = read_claude_enrichment(path, 0)
        assert [e.title for e in first] == ["first"]

        with path.open("a") as handle:
            handle.write(mr(2752))
        second, offset = read_claude_enrichment(path, offset)
        assert [e.kind for e in second] == [EventKind.PR_LINK]
        assert second[0].pr_number == 2752

    def test_a_half_written_record_is_left_for_the_next_pass(self, tmp_path):
        """A live transcript's tail is routinely mid-write. Dropping it would lose the record."""
        path = tmp_path / "sess.jsonl"
        path.write_text(title("done") + '{"type":"pr-link","prUrl":"https://gi')
        events, offset = read_claude_enrichment(path, 0)
        assert [e.title for e in events] == ["done"]
        assert offset == len(title("done"))  # stopped at the last newline

        # The writer finishes the line.
        path.write_text(title("done") + mr(99))
        events, _ = read_claude_enrichment(path, offset)
        assert [e.pr_number for e in events] == [99]

    def test_tokens_are_never_counted_twice(self, tmp_path):
        """USAGE accumulates in the reducer, so a replayed stretch would inflate the total."""
        path = tmp_path / "sess.jsonl"
        path.write_text(usage(100, 20))
        first, offset = read_claude_enrichment(path, 0)
        again, _ = read_claude_enrichment(path, offset)
        assert sum(e.tokens_in for e in first) == 100
        assert again == []

    def test_a_truncated_file_is_read_from_the_start_again(self, tmp_path):
        """Seeking past the end would otherwise stall that transcript forever."""
        path = tmp_path / "sess.jsonl"
        path.write_text(title("long") * 5)
        _, offset = read_claude_enrichment(path, 0)
        path.write_text(title("short"))
        events, new_offset = read_claude_enrichment(path, offset)
        assert [e.title for e in events] == ["short"]
        assert new_offset == len(title("short"))

    def test_a_missing_file_does_not_raise(self, tmp_path):
        assert read_claude_enrichment(tmp_path / "nope.jsonl", 0) == ([], 0)

    def test_the_whole_file_helper_still_works(self, tmp_path):
        path = tmp_path / "sess.jsonl"
        path.write_text(title("t") + mr(1) + usage(5, 5))
        kinds = [e.kind for e in claude_enrichment(path)]
        assert kinds == [EventKind.TITLE, EventKind.PR_LINK, EventKind.USAGE]


class TestLiveMergeRequests:
    """The behaviour the whole change exists for: an MR opened after the daemon started."""

    @pytest.fixture
    def village(self, tmp_path, monkeypatch):
        transcripts = tmp_path / "projects" / "repo"
        transcripts.mkdir(parents=True)
        monkeypatch.setattr(
            "agent_village.daemon.recent_claude_transcripts",
            lambda within: sorted(transcripts.glob("*.jsonl")),
        )
        village = Village(
            VillageConfig(
                spool_dir=tmp_path / "spool",
                enable_history=False,
                enrich_interval=timedelta(0),
            )
        )
        return village, transcripts

    def test_an_mr_opened_while_running_appears_without_a_restart(self, village):
        village, transcripts = village
        path = transcripts / "sess-claude.jsonl"
        path.write_text(title("Rolling upload safety"))
        village.poll_once(at(1))
        assert village.world.agents["sess-claude"].pr_links == ()

        with path.open("a") as handle:
            handle.write(mr(2752))
        village.poll_once(at(2))

        links = village.world.agents["sess-claude"].pr_links
        assert [p.number for p in links] == [2752]
        assert links[0].repo == "acme/acme-platform"
        assert village.detail("sess-claude")["prLinks"][0]["number"] == 2752

    def test_a_rename_in_claude_code_names_the_agent(self, village):
        village, transcripts = village
        path = transcripts / "sess-claude.jsonl"
        path.write_text(title("Rolling upload safety"))
        village.poll_once(at(1))

        with path.open("a") as handle:
            handle.write(renamed("  upload   safety "))
        village.poll_once(at(2))
        assert village.world.agents["sess-claude"].display_name() == "upload safety"

    def test_the_latest_rename_wins(self, village):
        """Claude Code writes the record again on every rename, and the UI can rename too."""
        village, transcripts = village
        path = transcripts / "sess-claude.jsonl"
        path.write_text(title("t") + renamed("first") + renamed("second"))
        village.poll_once(at(1))
        assert village.world.agents["sess-claude"].display_name() == "second"

        village.rename_agent("sess-claude", "from the ui")
        assert village.world.agents["sess-claude"].display_name() == "from the ui"

        with path.open("a") as handle:
            handle.write(renamed("second"))
        village.poll_once(at(2))
        assert village.world.agents["sess-claude"].display_name() == "from the ui", "a repeat is not a rename"

        with path.open("a") as handle:
            handle.write(renamed("third"))
        village.poll_once(at(3))
        assert village.world.agents["sess-claude"].display_name() == "third"

    def test_the_same_mr_is_not_listed_twice(self, village):
        """Claude writes a pr-link record on every push to the branch, not just the first."""
        village, transcripts = village
        path = transcripts / "sess-claude.jsonl"
        path.write_text(title("t") + mr(2752))
        village.poll_once(at(1))
        with path.open("a") as handle:
            handle.write(mr(2752))
        village.poll_once(at(2))
        assert len(village.world.agents["sess-claude"].pr_links) == 1

    def test_several_mrs_are_all_kept(self, village):
        village, transcripts = village
        path = transcripts / "sess-claude.jsonl"
        path.write_text(title("t") + mr(2751) + mr(2752))
        village.poll_once(at(1))
        assert [p.number for p in village.world.agents["sess-claude"].pr_links] == [2751, 2752]

    def test_enrichment_is_throttled(self, tmp_path, monkeypatch):
        """At the poll rate this would re-stat a directory of transcripts four times a second."""
        calls = []
        monkeypatch.setattr(
            "agent_village.daemon.recent_claude_transcripts",
            lambda within: calls.append(1) or [],
        )
        village = Village(
            VillageConfig(
                spool_dir=tmp_path / "spool",
                enable_history=False,
                enrich_interval=timedelta(seconds=3),
            )
        )
        village.poll_once(at(0))
        village.poll_once(at(1))
        village.poll_once(at(2))
        assert len(calls) == 1
        village.poll_once(at(10))
        assert len(calls) == 2
