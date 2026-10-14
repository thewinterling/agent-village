"""Live titles: a name for each session that keeps up with what it is doing.

The model call itself is swapped out for a fake throughout. What is tested is when a title is
asked for, what the model is told, what comes back is cleaned into, and how it reaches the name.
"""

from __future__ import annotations

from concurrent.futures import Executor, Future
from dataclasses import replace
from datetime import timedelta

from agent_village.daemon import Village, VillageConfig
from agent_village.models import Agent, AgentState, EventKind, Source, VillageEvent
from agent_village.reducer import apply
from agent_village.titling import RECENT_PROMPTS, Titler, TitlerConfig, clean_title, titling_prompt
from conftest import at


class Immediately(Executor):
    """Runs the job on submit, so a test needs no threads and no waiting."""

    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class Model:
    """Stands in for Haiku. Records what it was asked and answers from a script."""

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers) or ["GitHub CI setup"]
        self.asked: list[str] = []

    def __call__(self, prompt: str) -> str | None:
        self.asked.append(prompt)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def agent(**fields) -> Agent:
    base = Agent(
        session_id="sess-1",
        source=Source.CLAUDE,
        state=AgentState.WORKING,
        state_since=at(0),
        last_event_at=at(0),
        spawned_at=at(0),
        cwd="/home/u/workspace/acme-platform",
        git_branch="main",
        recent_prompts=("port the CI to GitHub Actions",),
    )
    return replace(base, **fields)


def titler(model: Model | None = None, **config) -> Titler:
    return Titler(TitlerConfig(enabled=True, **config), generate=model or Model(), executor=Immediately())


def prompt(text: str, ts: int = 0) -> VillageEvent:
    return VillageEvent(source=Source.CLAUDE, session_id="sess-1", kind=EventKind.PROMPT_SUBMIT, ts=at(ts), text=text)


class TestRememberingPrompts:
    def test_keeps_the_latest_few_oldest_first(self):
        from agent_village.models import World

        world = World()
        for i in range(RECENT_PROMPTS + 3):
            world = apply(world, prompt(f"prompt {i}", i))
        kept = world.agents["sess-1"].recent_prompts
        assert kept == tuple(f"prompt {i}" for i in range(3, RECENT_PROMPTS + 3))

    def test_a_live_title_lands_on_the_agent(self):
        from agent_village.models import World

        world = apply(World(), prompt("go"))
        event = VillageEvent(
            source=Source.CLAUDE, session_id="sess-1", kind=EventKind.LIVE_TITLE, ts=at(1), title="CI setup"
        )
        world = apply(world, event)
        assert world.agents["sess-1"].live_title == "CI setup"
        assert world.agents["sess-1"].state is AgentState.THINKING, "a title must not move the avatar"


class TestWhatTheModelIsTold:
    def test_names_the_latest_prompts_newest_last(self):
        text = titling_prompt(agent(recent_prompts=("move the repo", "now port the CI")))
        assert text.index("move the repo") < text.index("now port the CI")

    def test_includes_where_it_is_working(self):
        text = titling_prompt(agent(git_branch="feat/ci"))
        assert "acme-platform" in text and "feat/ci" in text

    def test_clips_a_pasted_wall_of_text(self):
        text = titling_prompt(agent(recent_prompts=("x" * 5000,)))
        assert len(text) < 2000


class TestCleaningTheAnswer:
    def test_takes_the_title_and_nothing_else(self):
        assert clean_title('"GitHub CI setup."\n\nThis session is about...') == "GitHub CI setup"

    def test_drops_a_label_the_model_put_in_front(self):
        assert clean_title("Title: Hotel sizing") == "Hotel sizing"

    def test_refuses_an_empty_or_rambling_answer(self):
        assert clean_title("") is None
        assert clean_title(None) is None
        assert clean_title("word " * 30) is None


class TestWhenATitleIsAskedFor:
    def test_a_new_prompt_gets_a_title(self):
        model = Model("GitHub CI setup")
        t = titler(model)
        t.start_due([agent()], at(0))
        assert t.collect(at(0)) == [("sess-1", "GitHub CI setup")]

    def test_the_same_prompts_are_not_titled_twice(self):
        model = Model()
        t = titler(model)
        t.start_due([agent()], at(0))
        t.start_due([agent()], at(600))
        assert len(model.asked) == 1

    def test_a_chatty_session_waits_its_turn(self):
        model = Model()
        t = titler(model, min_interval=timedelta(minutes=3))
        t.start_due([agent()], at(0))
        t.collect(at(0))
        t.start_due([agent(recent_prompts=("a", "b"))], at(60))
        assert len(model.asked) == 1
        t.start_due([agent(recent_prompts=("a", "b"))], at(200))
        assert len(model.asked) == 2

    def test_sessions_that_are_not_doing_anything_are_left_alone(self):
        """Backfill wakes a hotel full of sleepers. None of them is worth a model call."""
        model = Model()
        t = titler(model)
        quiet = [
            agent(session_id="asleep", state=AgentState.SLEEPING),
            agent(session_id="gone", state=AgentState.GONE),
            agent(session_id="sub", parent_session_id="sess-1"),
            agent(session_id="mute", recent_prompts=()),
        ]
        t.start_due(quiet, at(0))
        assert model.asked == []

    def test_a_failed_call_leaves_the_name_alone_and_is_not_retried_at_once(self):
        model = Model(None)
        t = titler(model)
        t.start_due([agent()], at(0))
        assert t.collect(at(0)) == []
        t.start_due([agent()], at(10))
        assert len(model.asked) == 1

    def test_disabled_asks_nothing(self):
        model = Model()
        t = Titler(TitlerConfig(enabled=False), generate=model, executor=Immediately())
        t.start_due([agent()], at(0))
        assert model.asked == []


class TestInTheDaemon:
    def test_a_prompt_renames_the_avatar(self, tmp_path):
        village = Village(
            VillageConfig(spool_dir=tmp_path / "spool", enable_history=False, backfill_within=None),
            titler=titler(Model("GitHub CI setup")),
        )
        village.world = apply(village.world, replace(prompt("port the CI"), git_branch="feat/ci"))
        village.poll_once(at(1))
        village.poll_once(at(2))
        assert village.world.agents["sess-1"].display_name() == "GitHub CI setup - feat/ci"

    def test_off_unless_asked_for(self):
        assert VillageConfig().titler.enabled is False
