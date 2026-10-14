"""What an agent is called, and the plan it is working through.

A session title describes how the conversation opened; an hour later it is often wrong about
what the agent is doing. The branch is always right about that but says nothing about why. So
the name is both: a topic, then the branch. The topic comes from the plan file the session has
been working through where there is one, because that is the thing that stays true all day.
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
    def test_the_plan_it_is_following_leads_the_branch(self):
        named = agent(git_branch="feature/blabla-graphql-abc", plan_mentions=(("0067-graphql-schema-migration", 9),))
        assert named.display_name() == "GraphQL schema migration - feature/blabla-graphql-abc"

    def test_the_session_title_leads_when_there_is_no_plan(self):
        named = agent(git_branch="main", title="Lint check and push")
        assert named.display_name() == "Lint check and push - main"

    def test_the_plan_wins_over_the_title(self):
        """The title is the last thing discussed; the plan is the job. Prefer the job."""
        named = agent(git_branch="main", title="Lint check and push", plan_mentions=(("0074-remote-oauth-reader", 9),))
        assert named.display_name() == "Remote OAuth reader - main"

    def test_a_branch_with_nothing_to_say_about_it_keeps_the_repo(self):
        """Falling back to a bare `main` would make three sessions in one repo identical."""
        assert agent(git_branch="main").display_name() == "acme-platform / main"

    def test_without_a_branch_there_is_nothing_to_prefix(self):
        assert agent(title="Lint check and push").display_name() == "acme-platform"


class TestTheSubtitle:
    def test_the_plan_file_is_the_subtitle_when_there_is_one(self):
        named = agent(git_branch="main", plan_mentions=(("0067-graphql-schema-migration", 9),))
        assert named.subtitle() == "0067-graphql-schema-migration.md"

    def test_the_title_is_not_printed_twice(self):
        """With no plan the title leads the name, so repeating it underneath says nothing."""
        named = agent(git_branch="main", title="Lint check and push", last_prompt="now push it")
        assert named.display_name() == "Lint check and push - main"
        assert named.subtitle() == "now push it"

    def test_the_title_still_shows_when_the_name_did_not_use_it(self):
        assert agent(title="Lint check and push").subtitle() == "Lint check and push"


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

    def plans_in(self, tmp_path, *lines: str) -> list[str]:
        path = tmp_path / "s.jsonl"
        path.write_text("".join(lines))
        return [e.file_path for e in claude_enrichment(path) if e.kind is EventKind.PLAN]

    def test_every_mention_is_reported_so_they_can_be_counted(self, tmp_path):
        found = self.plans_in(
            tmp_path,
            self.line("open ~/workspace/dotfiles/plans/0067-graphql.md"),
            self.line("step 3 of plans/0067-graphql.md is done"),
        )
        assert found == ["0067-graphql", "0067-graphql"]

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
