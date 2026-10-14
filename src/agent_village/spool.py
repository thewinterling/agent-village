"""Consume the spool directory the hook script writes into."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import parse_spool_line
from .models import VillageEvent

log = logging.getLogger(__name__)

DEFAULT_SPOOL = Path.home() / ".agent-village" / "spool"


@dataclass
class SpoolReader:
    """Drains event files in timestamp order and deletes them as it goes.

    File names start with the emitting wall clock, which is fixed-width for the next few
    centuries, so a lexicographic sort is also a chronological one.
    """

    spool_dir: Path = field(default_factory=lambda: DEFAULT_SPOOL)
    #: If the daemon was down for a long time the spool can hold a lot. Replaying days of stale
    #: events would only produce a village of ghosts, so drop everything but the newest batch.
    max_batch: int = 5000
    dropped: int = 0

    def __post_init__(self) -> None:
        self.spool_dir.mkdir(parents=True, exist_ok=True)

    def drain(self) -> list[VillageEvent]:
        try:
            paths = sorted(self.spool_dir.glob("*.json"))
        except OSError:
            return []

        if len(paths) > self.max_batch:
            stale, paths = paths[: -self.max_batch], paths[-self.max_batch :]
            for path in stale:
                path.unlink(missing_ok=True)
            self.dropped += len(stale)
            log.warning("spool backlog: dropped %d stale events", len(stale))

        events: list[VillageEvent] = []
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            events.extend(parse_spool_line(text))
            path.unlink(missing_ok=True)
        return events
