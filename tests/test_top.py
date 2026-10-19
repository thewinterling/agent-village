"""`agent-top`, the terminal view. The screen is curses' problem; the layout and the API calls are ours."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import pytest

from agent_village import top
from agent_village.top import Daemon, column_widths, detail_lines, fit, format_row, order, row_cells, xterm256


def agent(sid: str, state: str, age: float = 1, **extra) -> dict:
    return {"sessionId": sid, "state": state, "stateAgeS": age, "name": sid, **extra}


class TestOrder:
    def test_the_states_that_need_you_come_first(self):
        ids = [a["sessionId"] for a in order([agent("s", "sleeping"), agent("w", "working"), agent("q", "waiting")])]
        assert ids == ["q", "w", "s"]

    def test_unlisted_states_go_last_and_the_longest_waiting_leads(self):
        ids = [a["sessionId"] for a in order([agent("g", "gone"), agent("new", "done", 5), agent("old", "done", 50)])]
        assert ids == ["old", "new", "g"]


class TestRows:
    def test_every_row_is_exactly_as_wide_as_the_table(self):
        widths = column_widths(120)
        cells = row_cells(agent("a", "working", name="x" * 300, repo="r", gitBranch="b", tokensIn=1500, tokensOut=0))
        assert len(format_row(cells, widths)) == 120

    def test_a_long_name_is_cut_with_an_ellipsis(self):
        assert fit("abcdef", 4) == "abc…"
        assert fit("ab", 4) == "ab  "

    def test_cells_carry_the_same_facts_as_the_web_list(self):
        now = datetime(2026, 10, 19, 12, 0, 10, tzinfo=UTC)
        cells = row_cells(
            agent(
                "a",
                "waiting",
                75,
                waitingInferred=True,
                repo="village",
                gitBranch="main",
                currentTool="Bash",
                model="claude-opus-5-5",
                tokensIn=1_200_000,
                tokensOut=300_000,
                lastEventAt="2026-10-19T12:00:00+00:00",
            ),
            now,
        )
        assert cells == ["waiting?", "1m15s", "a", "village:main", "Bash", "claude-opus-5-5", "1.5M", "10s ago"]


class TestDetail:
    def test_the_chat_summary_is_there_turn_by_turn(self):
        lines = detail_lines(
            {
                "sessionId": "s",
                "name": "village",
                "state": "working",
                "stateAgeS": 3,
                "narrative": [
                    {"ask": "fix the seats", "outcome": "done, tests pass", "at": "2026-10-19T09:41:00", "toolCalls": 4}
                ],
                "resumeCommand": "claude --resume s",
            },
            60,
        )
        text = [t for _, t in lines]
        assert text[0] == "village"
        assert "WHAT HAPPENED (1 turn)" in text
        assert "▸ fix the seats" in text
        assert "  done, tests pass" in text
        assert "  09:41 · 4 tool calls" in text
        assert "  claude --resume s" in text

    def test_nothing_is_wider_than_the_pane(self):
        lines = detail_lines({"sessionId": "s", "name": "n " * 80, "narrative": [{"ask": "word " * 100}]}, 30)
        assert all(len(t) <= 30 for _, t in lines)


def test_state_colours_land_on_the_xterm_cube():
    assert xterm256(0x000000) == 16
    assert xterm256(0xFFFFFF) == 231


class TestDaemon:
    @pytest.fixture
    def sent(self, monkeypatch):
        """Every request the client makes, answered with a token or an ok."""
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            body = {"token": "t0k"} if request.full_url.endswith("/api/session") else {"ok": True}
            return io.BytesIO(json.dumps(body).encode())

        monkeypatch.setattr(top.urllib.request, "urlopen", urlopen)
        return requests

    def test_actions_carry_the_token_and_json_and_no_origin(self, sent):
        Daemon("http://127.0.0.1:8847").act("a/b", "rename", {"title": "x"})
        post = sent[-1]
        assert post.full_url == "http://127.0.0.1:8847/api/agents/a%2Fb/rename"
        assert post.get_method() == "POST"
        assert post.get_header("X-village-token") == "t0k"
        assert post.get_header("Content-type") == "application/json"
        assert post.get_header("Origin") is None
        assert json.loads(post.data) == {"title": "x"}

    def test_the_token_is_fetched_once(self, sent):
        daemon = Daemon("http://127.0.0.1:8847")
        daemon.act("a", "dismiss")
        daemon.act("a", "dismiss")
        assert sum(r.full_url.endswith("/api/session") for r in sent) == 1

    def test_an_unreachable_daemon_is_a_readable_error(self, monkeypatch):
        def refuse(request, timeout):
            raise top.urllib.error.URLError("refused")

        monkeypatch.setattr(top.urllib.request, "urlopen", refuse)
        with pytest.raises(RuntimeError, match="unreachable"):
            Daemon("http://127.0.0.1:1").get("/api/world")
