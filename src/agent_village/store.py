"""SQLite history, so the village has a past as well as a present.

Feeds the phase-3 history scrubber and survives daemon restarts. Deliberately append-only and
schema-light: this is a log, not a model.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Agent, AgentState, VillageEvent

DEFAULT_DB = Path.home() / ".agent-village" / "history.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    source      TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    tool_name   TEXT,
    detail      TEXT,
    raw_event   TEXT,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    name        TEXT,
    repo        TEXT
);
CREATE INDEX IF NOT EXISTS transitions_ts ON transitions(ts);
"""


@dataclass
class HistoryStore:
    path: Path = DEFAULT_DB

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with closing(self._conn.cursor()) as cur:
            cur.executescript(SCHEMA)
            self._migrate(cur)
        self._conn.commit()

    @staticmethod
    def _migrate(cur: sqlite3.Cursor) -> None:
        """Bring a database written by an older version up to the current schema.

        `CREATE TABLE IF NOT EXISTS` is a no-op on a table that already exists, so columns
        added later have to be attached by hand. This is an append-only log and nothing
        recomputes the past, so the usage series simply starts at the first event recorded
        after the upgrade.
        """
        have = {row["name"] for row in cur.execute("PRAGMA table_info(events)")}
        for column in ("tokens_in", "tokens_out"):
            if column not in have:
                cur.execute(f"ALTER TABLE events ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        self._conn.close()

    def record_events(self, events: Iterable[VillageEvent]) -> None:
        rows = [
            (
                e.ts.isoformat(),
                str(e.source),
                e.session_id,
                str(e.kind),
                e.tool_name,
                e.tool_detail or e.text,
                e.raw_event_name,
                e.tokens_in,
                e.tokens_out,
            )
            for e in events
        ]
        if not rows:
            return
        with closing(self._conn.cursor()) as cur:
            cur.executemany(
                "INSERT INTO events (ts, source, session_id, kind, tool_name, detail, raw_event, tokens_in, tokens_out)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        self._conn.commit()

    def custom_titles(self) -> dict[str, str]:
        """The latest name given to each renamed session. Empty means it was reset."""
        rows = self._conn.execute("SELECT session_id, detail FROM events WHERE kind = 'rename' ORDER BY id")
        return {row["session_id"]: row["detail"] or "" for row in rows}

    def record_transition(self, agent: Agent, previous: AgentState | None) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO transitions (ts, session_id, from_state, to_state, name, repo) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    agent.state_since.isoformat(),
                    agent.session_id,
                    str(previous) if previous else None,
                    str(agent.state),
                    agent.display_name(),
                    agent.repo,
                ),
            )
        self._conn.commit()

    def recent_transitions(self, limit: int = 500) -> list[dict[str, Any]]:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM transitions ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

    def session_timeline(self, session_id: str, limit: int = 300) -> list[dict[str, Any]]:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT ts, kind, tool_name, detail FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
            return [dict(row) for row in reversed(cur.fetchall())]

    def latest_usage_ts(self) -> datetime | None:
        """The newest token spend already on disk, used as a watermark on restart."""
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT MAX(ts) AS ts FROM events WHERE tokens_in > 0 OR tokens_out > 0")
            row = cur.fetchone()
        return datetime.fromisoformat(row["ts"]) if row and row["ts"] else None

    def usage_series(
        self,
        *,
        window: timedelta,
        bucket: timedelta,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Tokens spent per slice of time, oldest first.

        Quiet slices are returned as zeroes rather than left out. The strip they feed is a
        time axis, and dropping the gaps would make a busy minute next to an idle hour read
        as continuous work.

        Only Claude sessions appear: token counts come from the transcript, and Cursor does
        not write one we can read.
        """
        now = now or datetime.now(UTC)
        width = max(1, int(bucket.total_seconds()))
        count = max(1, int(window.total_seconds()) // width)
        # Snapped to whole slices so the bars stay put between refreshes instead of sliding
        # along by a second each time. The last slice is the one currently filling up.
        start = (int(now.timestamp()) // width + 1 - count) * width

        tokens = [[0, 0] for _ in range(count)]
        by_session: list[dict[str, int]] = [{} for _ in range(count)]

        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT ts, session_id, tokens_in, tokens_out FROM events"
                " WHERE ts >= ? AND (tokens_in > 0 OR tokens_out > 0)",
                (datetime.fromtimestamp(start, tz=UTC).isoformat(),),
            )
            for row in cur.fetchall():
                index = int((datetime.fromisoformat(row["ts"]).timestamp() - start) // width)
                if not 0 <= index < count:
                    continue
                tokens[index][0] += row["tokens_in"]
                tokens[index][1] += row["tokens_out"]
                spend = row["tokens_in"] + row["tokens_out"]
                by_session[index][row["session_id"]] = by_session[index].get(row["session_id"], 0) + spend

        buckets = []
        for index, (tokens_in, tokens_out) in enumerate(tokens):
            busiest = max(by_session[index].items(), key=lambda kv: kv[1], default=None)
            buckets.append(
                {
                    "t": datetime.fromtimestamp(start + index * width, tz=UTC).isoformat(),
                    "in": tokens_in,
                    "out": tokens_out,
                    "sessions": len(by_session[index]),
                    "top": {"sessionId": busiest[0], "tokens": busiest[1]} if busiest else None,
                }
            )
        return {"bucketS": width, "buckets": buckets}

    def daily_stats(self) -> dict[str, Any]:
        """Counters for the HUD: how much did the village actually get through today."""
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT kind, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY kind",
                (start.isoformat(),),
            )
            counts = {row["kind"]: row["n"] for row in cur.fetchall()}
            cur.execute("SELECT COUNT(DISTINCT session_id) AS n FROM events WHERE ts >= ?", (start.isoformat(),))
            sessions = cur.fetchone()["n"]
        return {
            "sessions": sessions,
            "toolCalls": counts.get("tool_start", 0) + counts.get("tool_end", 0),
            "prompts": counts.get("prompt_submit", 0),
            "turns": counts.get("turn_end", 0),
            "benchVisits": counts.get("needs_input", 0),
            "failures": counts.get("tool_fail", 0) + counts.get("turn_fail", 0),
        }
