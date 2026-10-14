"""Token spend over time, which is what the strip along the bottom of the village draws.

The two things worth pinning down: the series is a *time* axis, so quiet minutes have to show
up as gaps rather than be squeezed out, and every bar has to be able to say whose tokens it is
so that clicking one opens the right agent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_village.daemon import Village, VillageConfig
from agent_village.models import EventKind, Source, VillageEvent
from agent_village.reaper import ReaperConfig
from agent_village.store import HistoryStore

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


def usage_event(session_id: str, when: datetime, tokens_in: int, tokens_out: int) -> VillageEvent:
    return VillageEvent(
        kind=EventKind.USAGE,
        source=Source.CLAUDE,
        session_id=session_id,
        ts=when,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


@pytest.fixture
def store(tmp_path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.sqlite3")


def series(store: HistoryStore, *, minutes: int = 10) -> list[dict]:
    return store.usage_series(window=timedelta(minutes=minutes), bucket=timedelta(minutes=1), now=NOW)["buckets"]


class TestUsageSeries:
    def test_tokens_land_in_the_slice_they_were_spent_in(self, store):
        store.record_events([usage_event("a", NOW - timedelta(minutes=3, seconds=30), 900, 100)])
        buckets = series(store)
        assert [(b["in"], b["out"]) for b in buckets if b["in"]] == [(900, 100)]
        # Ten whole minutes ending at the one in progress, so 3m30s ago is the sixth.
        assert buckets[5]["in"] == 900

    def test_quiet_minutes_are_zeroes_rather_than_missing(self, store):
        store.record_events([usage_event("a", NOW - timedelta(minutes=9), 500, 0)])
        buckets = series(store)
        assert len(buckets) == 10
        assert sum(b["in"] + b["out"] for b in buckets) == 500

    def test_the_slices_run_oldest_first_and_end_at_now(self, store):
        buckets = series(store)
        stamps = [datetime.fromisoformat(b["t"]) for b in buckets]
        assert stamps == sorted(stamps)
        assert NOW - stamps[-1] < timedelta(minutes=1)

    def test_everything_in_one_slice_is_added_up(self, store):
        minute = NOW - timedelta(minutes=2, seconds=30)
        store.record_events([usage_event("a", minute, 100, 10), usage_event("b", minute, 300, 30)])
        hot = next(b for b in series(store) if b["in"])
        assert (hot["in"], hot["out"], hot["sessions"]) == (400, 40, 2)

    def test_a_slice_names_the_session_that_spent_the_most(self, store):
        minute = NOW - timedelta(minutes=2, seconds=30)
        store.record_events([usage_event("quiet", minute, 100, 0), usage_event("hungry", minute, 9000, 500)])
        hot = next(b for b in series(store) if b["in"])
        assert hot["top"] == {"sessionId": "hungry", "tokens": 9500}

    def test_an_empty_slice_has_nobody_to_blame(self, store):
        assert all(b["top"] is None for b in series(store))

    def test_spend_older_than_the_window_is_left_out(self, store):
        store.record_events([usage_event("a", NOW - timedelta(hours=2), 5000, 500)])
        assert sum(b["in"] for b in series(store)) == 0

    def test_events_without_tokens_do_not_create_bars(self, store):
        store.record_events(
            [VillageEvent(kind=EventKind.TOOL_END, source=Source.CLAUDE, session_id="a", ts=NOW, tool_name="Bash")]
        )
        assert all(b["top"] is None for b in series(store))


class TestOlderDatabases:
    def test_a_database_without_the_token_columns_is_upgraded_in_place(self, tmp_path):
        """The columns were added later, and an existing village must not lose its log."""
        import sqlite3

        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
            " source TEXT NOT NULL, session_id TEXT NOT NULL, kind TEXT NOT NULL,"
            " tool_name TEXT, detail TEXT, raw_event TEXT);"
            "INSERT INTO events (ts, source, session_id, kind) VALUES ('2026-01-01T00:00:00+00:00','claude','a','turn_end');"
        )
        legacy.commit()
        legacy.close()

        store = HistoryStore(path)
        store.record_events([usage_event("a", NOW - timedelta(minutes=1), 700, 70)])
        assert sum(b["in"] for b in series(store)) == 700
        assert len(store.session_timeline("a")) == 2


class TestVillageUsage:
    @pytest.fixture
    def village(self, tmp_path) -> Village:
        proc = tmp_path / "proc"
        proc.mkdir()
        return Village(
            VillageConfig(
                spool_dir=tmp_path / "spool",
                db_path=tmp_path / "history.sqlite3",
                backfill_within=timedelta(0),
                usage_window=timedelta(minutes=10),
                usage_bucket=timedelta(minutes=1),
                reaper=ReaperConfig(proc_root=proc),
            )
        )

    def test_the_busiest_session_is_named_so_a_bar_can_be_clicked(self, village):
        village.store.record_events([usage_event("sess-claude", NOW - timedelta(minutes=1), 4000, 400)])
        village.poll_once(NOW)
        hot = next(b for b in village.usage(NOW)["buckets"] if b["top"])
        assert hot["top"]["sessionId"] == "sess-claude"
        assert hot["top"]["name"]

    def test_restarting_does_not_double_the_bars(self, village):
        """Backfill re-reads every transcript from byte zero, so it must not re-log spend."""
        spent = [usage_event("sess-claude", NOW - timedelta(minutes=2), 1000, 100)]
        village.store.record_events(spent)
        village._record_backfilled_usage(spent)
        assert sum(b["in"] for b in village.usage(NOW)["buckets"]) == 1000

    def test_backfill_keeps_spend_the_log_has_never_seen(self, village):
        village._record_backfilled_usage([usage_event("sess-claude", NOW - timedelta(minutes=2), 1000, 100)])
        assert sum(b["in"] for b in village.usage(NOW)["buckets"]) == 1000

    def test_a_village_keeping_no_history_reports_no_usage(self, tmp_path):
        quiet = Village(VillageConfig(spool_dir=tmp_path / "spool", enable_history=False))
        assert quiet.usage(NOW) == {"bucketS": 0, "buckets": []}


def test_the_endpoint_serves_the_series(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from agent_village.server import create_app

    app = create_app(VillageConfig(spool_dir=tmp_path / "spool", db_path=tmp_path / "history.sqlite3"))
    with TestClient(app) as client:
        payload = client.get("/api/usage").json()
    assert payload["bucketS"] == 60
    assert len(payload["buckets"]) == 180
