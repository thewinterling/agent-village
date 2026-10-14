"""What an agent is called, and the plan it is working through.

The name has to say what the session is doing *now*. Claude Code's own title is written once,
from the opening of the conversation, and an hour later it is often wrong; a third of sessions
never get one at all. So the name leads with a live title regenerated from the latest prompts,
falls back to that opening title and then to the plan being followed, and only names the
branch when the branch says something - `main` does not.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_village.models import Agent, AgentState, EventKind, Source, plan_topic
from agent_village.transcript import claude_enrichment
from conftest import at


def agent(**fields) -> Agent:
    """An agent at rest, with whatever naming material the test cares about."""
    base = Agent(
        session_id="sess-claude",
        source=Source.CLAUDE,
        state=AgentState.WORKING,
        state_since=at(0),
        last_event_at=at(0),
        spawned_at=at(0),
        cwd="/home/u/workspace/acme-platform",
    )
    return replace(base, **fields)


class TestWhatAnAgentIsCalled:
    def test_the_live_title_leads(self):
        named = agent(
            git_branch="feat/ci", live_title="GitHub CI setup", title="Repo move", plan_mentions=(("0067-x", 9),)
        )
        assert named.display_name() == "GitHub CI setup - feat/ci"

    def test_the_session_title_leads_until_there_is_a_live_one(self):
        named = agent(git_branch="feature/blabla-graphql-abc", title="Lint check and push")
        assert named.display_name() == "Lint check and push - feature/blabla-graphql-abc"

    def test_the_title_wins_over_the_plan(self):
        """A plan named often enough is a guess about the job; the title was written about it."""
        named = agent(
            git_branch="feat/x", title="Lint check and push", plan_mentions=(("0074-remote-oauth-reader", 9),)
        )
        assert named.display_name() == "Lint check and push - feat/x"

    def test_the_plan_leads_when_there_is_no_title(self):
        named = agent(git_branch="feature/blabla-graphql-abc", plan_mentions=(("0067-graphql-schema-migration", 9),))
        assert named.display_name() == "GraphQL schema migration - feature/blabla-graphql-abc"

    @pytest.mark.parametrize("branch", ["main", "master", "develop", "HEAD"])
    def test_a_default_branch_is_left_off(self, branch):
        """Every other session is on `main`. Saying so tells two of them apart no better."""
        assert agent(git_branch=branch, live_title="GitHub CI setup").display_name() == "GitHub CI setup"

    def test_with_nothing_to_lead_on_the_repo_and_branch_name_it(self):
        """Falling back to a bare `main` would make three sessions in one repo identical."""
        assert agent(git_branch="main").display_name() == "acme-platform / main"

    def test_without_a_branch_the_title_still_leads(self):
        assert agent(title="Lint check and push").display_name() == "Lint check and push"

    def test_without_a_branch_or_a_title_the_repo_names_it(self):
        assert agent().display_name() == "acme-platform"


class TestTheSubtitle:
    def test_the_plan_file_is_the_subtitle_when_there_is_one(self):
        named = agent(
            git_branch="main", live_title="Schema work", plan_mentions=(("0067-graphql-schema-migration", 9),)
        )
        assert named.subtitle() == "0067-graphql-schema-migration.md"

    def test_otherwise_the_latest_prompt(self):
        named = agent(git_branch="main", live_title="Push it", title="Lint check and push", last_prompt="now push it")
        assert named.subtitle() == "now push it"

    def test_the_title_is_not_printed_twice(self):
        """With nothing newer the title leads the name, so repeating it underneath says nothing."""
        assert agent(git_branch="main", title="Lint check and push").subtitle() is None

    def test_the_opening_title_shows_once_a_live_one_has_taken_the_name(self):
        assert agent(live_title="Push it", title="Lint check and push").subtitle() == "Lint check and push"


class TestReadingAPlanName:
    @pytest.mark.parametrize(
        ("filename", "topic"),
        [
            ("0067-graphql-schema-migration-pipeline", "GraphQL schema migration pipeline"),
            ("0068-oauth-token-storage", "OAuth token storage"),
            ("quality-worker-graphql-ios-profiles", "Quality worker GraphQL iOS profiles"),
            ("0041-oauth-github-login-flow", "OAuth GitHub login flow"),
        ],
    )
    def test_a_filename_reads_as_a_topic(self, filename, topic):
        assert plan_topic(filename) == topic

    def test_a_plan_with_no_name_has_no_topic(self):
        assert plan_topic(None) is None


class TestSpottingThePlanBeingFollowed:
    def line(self, text: str) -> str:
        return json.dumps({"type": "user", "sessionId": "s", "message": {"role": "user", "content": text}}) + "\n"

    def said(self, text: str) -> str:
        content = [{"type": "text", "text": text}]
        return (
            json.dumps({"type": "assistant", "sessionId": "s", "message": {"role": "assistant", "content": content}})
            + "\n"
        )

    def tool_call(self, **tool_input) -> str:
        content = [{"type": "tool_use", "id": "t1", "name": "Read", "input": tool_input}]
        return (
            json.dumps({"type": "assistant", "sessionId": "s", "message": {"role": "assistant", "content": content}})
            + "\n"
        )

    def tool_output(self, text: str) -> str:
        content = [{"type": "tool_result", "tool_use_id": "t1", "content": text}]
        return json.dumps({"type": "user", "sessionId": "s", "message": {"role": "user", "content": content}}) + "\n"

    def plans_in(self, tmp_path, *lines: str) -> list[str]:
        path = tmp_path / "s.jsonl"
        path.write_text("".join(lines))
        return [e.file_path for e in claude_enrichment(path) if e.kind is EventKind.PLAN]

    def test_every_mention_is_reported_so_they_can_be_counted(self, tmp_path):
        found = self.plans_in(
            tmp_path,
            self.line("open ~/workspace/dotfiles/plans/0067-graphql.md"),
            self.said("step 3 of plans/0067-graphql.md is done"),
            self.tool_call(file_path="/home/u/workspace/dotfiles/plans/0067-graphql.md"),
        )
        assert found == ["0067-graphql", "0067-graphql", "0067-graphql"]

    def test_text_the_agent_merely_read_is_not_a_mention(self, tmp_path):
        """A code comment or a grep hit that names a plan says nothing about this session's job.

        Reading this very module five times once named a session after the example in its
        docstring.
        """
        found = self.plans_in(tmp_path, *[self.tool_output("#: so that `~/w/plans/0067-x.md` and")] * 9)
        assert found == []

    def test_the_path_in_front_of_the_filename_does_not_matter(self, tmp_path):
        """The same plan gets referred to by absolute path, by `~` and bare. It is one plan."""
        found = self.plans_in(
            tmp_path,
            self.line("/home/u/workspace/dotfiles/plans/0074-reader.md and plans/0074-reader.md"),
        )
        assert found == ["0074-reader", "0074-reader"]

    def test_a_markdown_file_outside_a_plans_folder_is_not_a_plan(self, tmp_path):
        assert self.plans_in(tmp_path, self.line("see docs/design/command-center.md and README.md")) == []

    def test_a_bare_plans_directory_is_not_a_plan(self, tmp_path):
        """The house rule about where plans live is quoted into most sessions' context."""
        assert self.plans_in(tmp_path, self.line("plans go in /home/u/workspace/dotfiles/plans/")) == []


class TestWhichPlanCounts:
    def mentions(self, *names: str):
        return agent(git_branch="main", plan_mentions=tuple(sorted({n: names.count(n) for n in names}.items())))

    def test_a_plan_worked_through_all_session_is_the_plan(self):
        assert self.mentions(*["0067-graphql"] * 20).plan == "0067-graphql"

    def test_a_plan_named_once_in_passing_is_not(self):
        """Agents name-drop neighbouring plans constantly. Only the one in hand counts."""
        assert self.mentions("0070-ios", "0070-ios").plan is None

    def test_the_one_referenced_most_wins(self):
        busy = ["0067-graphql"] * 46 + ["0068-oauth-storage"] * 6
        assert self.mentions(*busy).plan == "0067-graphql"
