"""Typing a prompt into a running agent.

This is the most dangerous thing the village does - it makes an agent act, with whatever text
it is handed - so the tests are mostly about what it *refuses*: anything that is not a Claude
CLI process sitting in a tmux pane, and any text that would be read as keystrokes rather than
typed as a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from agent_village.control import (
    ControlConfig,
    Refused,
    flatten_prompt,
    pane_for_pid,
    send_prompt,
    why_not_promptable,
)
from agent_village.daemon import VillageConfig
from agent_village.models import Agent, AgentState, Source
from agent_village.server import create_app
from conftest import at

PID = 4242
TTY = "/dev/pts/7"


@dataclass
class FakeTmux:
    """Records what the village tried to type, and answers `list-panes` however a test needs."""

    panes: str = f"%3 {TTY}\n%4 /dev/pts/9\n"
    returncode: int = 0
    stderr: str = ""
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, **_):
        self.calls.append(argv)
        stdout = self.panes if argv[1] == "list-panes" else ""
        return type("Done", (), {"returncode": self.returncode, "stdout": stdout, "stderr": self.stderr})()

    @property
    def typed(self) -> list[str]:
        return [c[-1] for c in self.calls if c[1] == "send-keys" and "-l" in c]


@pytest.fixture
def proc(tmp_path) -> ControlConfig:
    """A /proc where pid 4242 is a claude process attached to a pty."""
    root = tmp_path / "proc"
    fds = root / str(PID) / "fd"
    fds.mkdir(parents=True)
    (root / str(PID) / "comm").write_text("claude\n")
    (tmp_path / "pts7").touch()
    fds.joinpath("0").symlink_to(TTY)
    return ControlConfig(proc_root=root)


def an_agent(**overrides) -> Agent:
    base = {
        "session_id": "sess",
        "source": Source.CLAUDE,
        "state": AgentState.WORKING,
        "state_since": at(0),
        "last_event_at": at(0),
        "spawned_at": at(0),
        "pid": PID,
    }
    return Agent(**{**base, **overrides})


class TestFindingTheTerminal:
    def test_a_process_is_matched_to_its_pane_by_the_pty_it_holds(self, proc):
        assert pane_for_pid(PID, proc, run=FakeTmux()) == "%3"

    def test_a_process_in_no_pane_of_ours_has_none(self, proc):
        assert pane_for_pid(PID, proc, run=FakeTmux(panes="%4 /dev/pts/9\n")) is None

    def test_a_process_that_is_not_on_a_terminal_has_none(self, tmp_path):
        root = tmp_path / "proc"
        (root / "77" / "fd").mkdir(parents=True)
        root.joinpath("77", "fd", "0").symlink_to("/dev/null")
        assert pane_for_pid(77, ControlConfig(proc_root=root), run=FakeTmux()) is None

    def test_tmux_not_being_installed_is_not_a_crash(self, proc):
        def missing(*_args, **_kwargs):
            raise FileNotFoundError("no tmux here")

        assert pane_for_pid(PID, proc, run=missing) is None


class TestWhatMayBePrompted:
    def test_a_claude_agent_in_a_pane_may_be(self, proc):
        assert why_not_promptable(an_agent(), proc, run=FakeTmux()) is None

    def test_a_cursor_agent_has_no_terminal(self, proc):
        reason = why_not_promptable(an_agent(source=Source.CURSOR), proc, run=FakeTmux())
        assert reason and "no terminal" in reason

    def test_a_subagent_is_told_to_ask_its_parent(self, proc):
        reason = why_not_promptable(an_agent(parent_session_id="boss"), proc, run=FakeTmux())
        assert reason and "parent" in reason

    def test_an_agent_outside_tmux_says_so(self, proc):
        reason = why_not_promptable(an_agent(), proc, run=FakeTmux(panes=""))
        assert reason and "tmux" in reason

    def test_a_recycled_pid_is_not_typed_at(self, tmp_path):
        root = tmp_path / "proc"
        (root / str(PID) / "fd").mkdir(parents=True)
        (root / str(PID) / "comm").write_text("postgres\n")
        reason = why_not_promptable(an_agent(), ControlConfig(proc_root=root), run=FakeTmux())
        assert reason and "no longer an agent" in reason


class TestSending:
    def test_the_prompt_is_typed_then_submitted(self, proc):
        tmux = FakeTmux()
        assert send_prompt(an_agent(), "run the tests", proc, run=tmux) == "%3"
        assert tmux.typed == ["run the tests"]
        assert tmux.calls[-1][-1] == "Enter", "the prompt has to actually be submitted"

    def test_the_text_is_typed_literally_rather_than_read_as_keys(self, proc):
        """Without tmux's `-l`, a prompt saying "C-c" would interrupt the agent instead."""
        tmux = FakeTmux()
        send_prompt(an_agent(), "press C-c then Enter to quit", proc, run=tmux)
        typing = next(c for c in tmux.calls if c[1] == "send-keys" and "-l" in c)
        assert typing[-1] == "press C-c then Enter to quit"

    def test_the_prompt_never_reaches_a_shell(self, proc):
        tmux = FakeTmux()
        send_prompt(an_agent(), "rm -rf / ; echo $(whoami) `id`", proc, run=tmux)
        assert tmux.typed == ["rm -rf / ; echo $(whoami) `id`"]
        assert all(isinstance(call, list) for call in tmux.calls), "argv only, never a shell string"

    def test_an_empty_prompt_is_refused_before_anything_is_typed(self, proc):
        tmux = FakeTmux()
        with pytest.raises(Refused, match="Nothing to send"):
            send_prompt(an_agent(), "   \n  ", proc, run=tmux)
        assert tmux.calls == []

    def test_an_overlong_prompt_is_refused(self, proc):
        tmux = FakeTmux()
        with pytest.raises(Refused, match="limited to"):
            send_prompt(an_agent(), "x" * 3000, proc, run=tmux)
        assert tmux.calls == []

    def test_a_cursor_agent_is_refused(self, proc):
        with pytest.raises(Refused, match="no terminal"):
            send_prompt(an_agent(source=Source.CURSOR), "hello", proc, run=FakeTmux())

    def test_tmux_failing_is_reported_rather_than_swallowed(self, proc):
        tmux = FakeTmux()

        def flaky(argv, **kwargs):
            done = tmux(argv, **kwargs)
            if argv[1] == "send-keys":
                return type("Done", (), {"returncode": 1, "stdout": "", "stderr": "no such pane"})()
            return done

        with pytest.raises(Refused, match="no such pane"):
            send_prompt(an_agent(), "hi", proc, run=flaky)


class TestTheEndpoint:
    """The prompt endpoint sits behind the same guard as stop, and for a better reason."""

    @pytest.fixture
    def client(self, tmp_path) -> TestClient:
        app = create_app(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False, backfill_within=None))
        return TestClient(app)

    def headers(self, client: TestClient) -> dict[str, str]:
        return {
            "x-village-token": client.get("/api/session").json()["token"],
            "content-type": "application/json",
            "origin": "http://127.0.0.1:8847",
            "host": "127.0.0.1:8847",
        }

    def test_a_page_on_another_origin_cannot_type_into_your_terminal(self, client):
        reply = client.post("/api/agents/sess/prompt", json={"text": "rm -rf /"})
        assert reply.status_code == 403

    def test_the_token_alone_is_not_enough_from_elsewhere(self, client):
        headers = {**self.headers(client), "origin": "https://evil.example"}
        reply = client.post("/api/agents/sess/prompt", content='{"text":"hi"}', headers=headers)
        assert reply.status_code == 403

    def test_a_valid_request_for_a_missing_agent_is_a_404(self, client):
        """Proves the guard let it through and it was the lookup that failed."""
        reply = client.post("/api/agents/nobody/prompt", content='{"text":"hi"}', headers=self.headers(client))
        assert reply.status_code == 404

    def test_a_body_that_is_not_json_is_a_400(self, client):
        reply = client.post("/api/agents/sess/prompt", content="not json", headers=self.headers(client))
        assert reply.status_code == 400


class TestFlattenPrompt:
    def test_a_multi_line_prompt_becomes_one_line(self):
        # A newline in the TUI submits, so three lines would arrive as three half-prompts.
        assert flatten_prompt("fix the test\nthen push") == "fix the test then push"

    def test_control_characters_are_stripped(self):
        """This text is typed into a terminal; an escape sequence there is not a prompt."""
        assert flatten_prompt("hello\x1b[2Jworld\x07") == "hello[2Jworld"

    def test_ordinary_text_is_left_alone(self):
        assert flatten_prompt("rebase onto main, then re-run CI") == "rebase onto main, then re-run CI"

    def test_whitespace_only_comes_back_empty(self):
        assert flatten_prompt("  \n\t ") == ""
