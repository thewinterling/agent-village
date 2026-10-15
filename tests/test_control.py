"""Stopping, dismissing and renaming agents.

Two things are being pinned down here. First, that the village refuses to signal anything it
cannot positively identify as a Claude CLI process - a Cursor pid is the editor, and a recycled
pid is a stranger. Second, that the write endpoints cannot be driven by a web page you happen
to have open.
"""

from __future__ import annotations

import json
import signal
from dataclasses import replace
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from agent_village.control import (
    ControlConfig,
    Refused,
    check_request,
    host_is_loopback,
    origin_is_loopback,
    stop,
    why_not_stoppable,
)
from agent_village.daemon import Village, VillageConfig
from agent_village.models import Agent, AgentState, Source
from agent_village.server import create_app
from conftest import at, claude_line, cursor_line


def fake_proc(tmp_path, pid: int, comm: str):
    """A /proc with one process in it, named however the test needs."""
    root = tmp_path / f"proc-{pid}-{comm}"
    (root / str(pid)).mkdir(parents=True)
    (root / str(pid) / "comm").write_text(f"{comm}\n")
    return ControlConfig(proc_root=root)


def an_agent(**overrides) -> Agent:
    base = {
        "session_id": "sess",
        "source": Source.CLAUDE,
        "state": AgentState.WORKING,
        "state_since": at(0),
        "last_event_at": at(0),
        "spawned_at": at(0),
        "pid": 4242,
    }
    return Agent(**{**base, **overrides})


class TestWhatMayBeStopped:
    def test_a_live_claude_process_may_be_stopped(self, tmp_path):
        config = fake_proc(tmp_path, 4242, "claude")
        assert why_not_stoppable(an_agent(), config) is None

    def test_a_cursor_agent_may_never_be_stopped(self, tmp_path):
        """Its pid is the shared editor. Signalling it would close the whole IDE."""
        config = fake_proc(tmp_path, 4242, "claude")
        reason = why_not_stoppable(an_agent(source=Source.CURSOR), config)
        assert reason and "Cursor" in reason

    def test_a_subagent_is_refused_because_it_shares_its_parents_process(self, tmp_path):
        config = fake_proc(tmp_path, 4242, "claude")
        child = an_agent(session_id="sess:child", parent_session_id="sess")
        reason = why_not_stoppable(child, config)
        assert reason and "parent" in reason

    def test_an_agent_with_no_pid_is_refused(self, tmp_path):
        config = fake_proc(tmp_path, 4242, "claude")
        assert why_not_stoppable(an_agent(pid=None), config) is not None

    def test_a_dead_process_is_refused(self, tmp_path):
        config = ControlConfig(proc_root=tmp_path / "empty")
        reason = why_not_stoppable(an_agent(), config)
        assert reason and "already exited" in reason

    def test_a_recycled_pid_is_refused(self, tmp_path):
        """The whole point of checking /proc/<pid>/comm: pid 4242 is somebody else now."""
        config = fake_proc(tmp_path, 4242, "postgres")
        reason = why_not_stoppable(an_agent(), config)
        assert reason and "postgres" in reason


class TestStop:
    def test_it_sends_sigint_to_the_right_pid(self, tmp_path):
        sent = []
        stop(an_agent(), fake_proc(tmp_path, 4242, "claude"), kill=lambda p, s: sent.append((p, s)))
        assert sent == [(4242, signal.SIGINT)]

    def test_it_refuses_rather_than_signalling_a_stranger(self, tmp_path):
        sent = []
        with pytest.raises(Refused):
            stop(an_agent(), fake_proc(tmp_path, 4242, "sshd"), kill=lambda p, s: sent.append((p, s)))
        assert sent == []

    def test_a_process_that_dies_mid_call_is_reported_not_raised_raw(self, tmp_path):
        def vanished(pid, sig):
            raise ProcessLookupError

        with pytest.raises(Refused, match="already exited"):
            stop(an_agent(), fake_proc(tmp_path, 4242, "claude"), kill=vanished)


class TestRequestGuard:
    """A loopback bind keeps the daemon off the network. It does nothing about your browser."""

    GOOD: ClassVar[dict] = {
        "token": "secret",
        "supplied_token": "secret",
        "host": "127.0.0.1:8847",
        "origin": "http://127.0.0.1:8847",
        "content_type": "application/json",
    }

    def test_a_well_formed_same_origin_request_passes(self):
        check_request(**self.GOOD)

    @pytest.mark.parametrize(
        ("field", "value", "status"),
        [
            ("supplied_token", "wrong", 403),
            ("supplied_token", None, 403),
            ("origin", "https://evil.example", 403),
            ("host", "evil.example", 403),  # DNS rebinding: resolves here, says otherwise
            ("content_type", "text/plain", 415),  # a simple request, which dodges preflight
            ("content_type", "application/x-www-form-urlencoded", 415),
        ],
    )
    def test_it_refuses(self, field, value, status):
        with pytest.raises(Refused) as caught:
            check_request(**{**self.GOOD, field: value})
        assert caught.value.status == status

    def test_a_missing_origin_is_allowed_because_that_is_curl_not_a_browser(self):
        check_request(**{**self.GOOD, "origin": None})

    def test_loopback_names(self):
        assert host_is_loopback("localhost:8847")
        assert host_is_loopback("[::1]:8847")
        assert not host_is_loopback("10.0.0.5:8847")
        assert not host_is_loopback(None)
        assert origin_is_loopback("http://localhost:1234")
        assert not origin_is_loopback("http://127.0.0.1.evil.example")


class TestDismiss:
    def test_dismissing_sends_an_avatar_to_the_door(self, tmp_path):
        village = Village(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False))
        (tmp_path / "spool").mkdir(exist_ok=True)
        (tmp_path / "spool" / "0001.json").write_text(claude_line("Stop", at(0), pid=None))
        village.poll_once(at(1))

        village.dismiss_agent("sess-claude", at(2))
        assert village.world.agents["sess-claude"].state is AgentState.GONE

    def test_dismissing_does_not_signal_anything(self, tmp_path):
        """Dismiss is cosmetic. It is the one action that is safe on a Cursor agent."""
        village = Village(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False))
        (tmp_path / "spool").mkdir(exist_ok=True)
        (tmp_path / "spool" / "0001.json").write_text(cursor_line("afterAgentResponse", at(0), text="hi"))
        village.poll_once(at(1))

        village.dismiss_agent("conv-cursor", at(2))
        assert village.world.agents["conv-cursor"].state is AgentState.GONE

    def test_dismissing_an_unknown_agent_is_refused(self, tmp_path):
        village = Village(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False))
        with pytest.raises(Refused) as caught:
            village.dismiss_agent("nobody")
        assert caught.value.status == 404


def village_with_one(tmp_path, *, history: bool = False) -> Village:
    village = Village(
        VillageConfig(spool_dir=tmp_path / "spool", enable_history=history, db_path=tmp_path / "history.sqlite3")
    )
    (tmp_path / "spool").mkdir(exist_ok=True)
    (tmp_path / "spool" / "0001.json").write_text(claude_line("Stop", at(0), pid=None))
    village.poll_once(at(1))
    return village


class TestRename:
    def test_a_given_name_replaces_the_generated_one(self, tmp_path):
        village = village_with_one(tmp_path)
        village.rename_agent("sess-claude", "  the   flaky test hunt ")
        assert village.world.agents["sess-claude"].display_name() == "the flaky test hunt"

    def test_an_empty_name_goes_back_to_the_generated_one(self, tmp_path):
        village = village_with_one(tmp_path)
        generated = village.world.agents["sess-claude"].display_name()
        village.rename_agent("sess-claude", "mine")
        village.rename_agent("sess-claude", "   ")
        assert village.world.agents["sess-claude"].display_name() == generated

    def test_a_novel_is_refused(self, tmp_path):
        village = village_with_one(tmp_path)
        with pytest.raises(Refused) as caught:
            village.rename_agent("sess-claude", "x" * 500)
        assert caught.value.status == 400

    def test_renaming_an_unknown_agent_is_refused(self, tmp_path):
        village = Village(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False))
        with pytest.raises(Refused) as caught:
            village.rename_agent("nobody", "x")
        assert caught.value.status == 404

    def test_the_name_survives_a_restart(self, tmp_path):
        village_with_one(tmp_path, history=True).rename_agent("sess-claude", "kept")
        (tmp_path / "spool" / "0001.json").unlink(missing_ok=True)
        restarted = village_with_one(tmp_path, history=True)
        restarted._restore_custom_titles()
        assert restarted.world.agents["sess-claude"].display_name() == "kept"


class TestEndpoints:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False, backfill_within=None))
        return TestClient(app)

    def test_the_page_can_fetch_a_token(self, client):
        token = client.get("/api/session").json()["token"]
        assert len(token) > 20

    def test_a_post_without_the_token_is_rejected(self, client):
        reply = client.post("/api/agents/whoever/dismiss", json={})
        assert reply.status_code == 403

    def test_a_post_from_another_origin_is_rejected_even_with_the_token(self, client):
        token = client.get("/api/session").json()["token"]
        reply = client.post(
            "/api/agents/whoever/dismiss",
            json={},
            headers={"x-village-token": token, "origin": "https://evil.example"},
        )
        assert reply.status_code == 403

    def test_a_valid_request_for_a_missing_agent_is_a_404_not_a_403(self, client):
        """Proves the guard passed and it was the lookup that failed."""
        token = client.get("/api/session").json()["token"]
        reply = client.post(
            "/api/agents/whoever/dismiss",
            content=json.dumps({}),
            headers={
                "x-village-token": token,
                "content-type": "application/json",
                "origin": "http://127.0.0.1:8847",
                "host": "127.0.0.1:8847",
            },
        )
        assert reply.status_code == 404


def test_detail_explains_why_an_agent_cannot_be_stopped(tmp_path):
    """The drawer greys the button out using this, rather than offering a doomed action."""
    village = Village(
        VillageConfig(
            spool_dir=tmp_path / "spool",
            enable_history=False,
            control=ControlConfig(proc_root=tmp_path / "empty"),
        )
    )
    (tmp_path / "spool").mkdir(exist_ok=True)
    (tmp_path / "spool" / "0001.json").write_text(cursor_line("afterAgentResponse", at(0), text="hi"))
    village.poll_once(at(1))

    detail = village.detail("conv-cursor")
    assert detail["canStop"] is False
    assert "Cursor" in detail["stopBlockedReason"]


def test_replace_keeps_an_agent_stoppable(tmp_path):
    """Guards against a future refactor quietly dropping the pid off an updated agent."""
    config = fake_proc(tmp_path, 4242, "claude")
    agent = replace(an_agent(), state=AgentState.THINKING)
    assert why_not_stoppable(agent, config) is None

    def test_rename_needs_a_title(self, client):
        token = client.get("/api/session").json()["token"]
        reply = client.post(
            "/api/agents/whoever/rename",
            content=json.dumps({"name": "wrong key"}),
            headers={
                "x-village-token": token,
                "content-type": "application/json",
                "origin": "http://127.0.0.1:8847",
                "host": "127.0.0.1:8847",
            },
        )
        assert reply.status_code == 400
