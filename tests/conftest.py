from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

T0 = datetime(2026, 10, 13, 8, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """A timestamp `seconds` after the scenario start."""
    return T0 + timedelta(seconds=seconds)


def claude_line(event: str, ts: datetime = T0, *, pid: int | None = 4242, **payload: Any) -> str:
    """One spool line as `village-emit.sh` would write it for Claude Code."""
    payload.setdefault("session_id", "sess-claude")
    payload.setdefault("cwd", "/home/u/workspace/acme-platform")
    payload["hook_event_name"] = event
    return json.dumps({"v": 1, "src": "claude", "ev": event, "ts": ts.isoformat(), "pid": pid, "p": payload})


def cursor_line(event: str, ts: datetime = T0, *, pid: int | None = 11201, **payload: Any) -> str:
    """One spool line as `village-emit.sh` would write it for Cursor."""
    payload.setdefault("conversation_id", "conv-cursor")
    payload.setdefault("workspace_roots", ["/home/u/workspace/acme-platform"])
    payload["hook_event_name"] = event
    return json.dumps({"v": 1, "src": "cursor", "ev": event, "ts": ts.isoformat(), "pid": pid, "p": payload})


@pytest.fixture
def t0() -> datetime:
    return T0
