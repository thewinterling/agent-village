"""The daemon loop: pull events in, fold them into the world, push the world out.

`poll_once` is deliberately synchronous and side-effect-visible so the whole daemon can be
driven from a test without an event loop or a socket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from .control import ControlConfig, Refused, send_prompt, stop, why_not_promptable, why_not_stoppable
from .identity import appearance
from .models import Agent, AgentState, EventKind, Source, VillageEvent, World, utcnow
from .reaper import ReaperConfig, dead_sessions
from .reducer import ReducerConfig, apply, tick
from .spool import DEFAULT_SPOOL, SpoolReader
from .store import DEFAULT_DB, HistoryStore
from .titling import Titler, TitlerConfig
from .transcript import (
    read_claude_enrichment,
    recent_claude_transcripts,
    recent_cursor_transcripts,
    session_narrative,
    summarise_claude,
    summarise_cursor,
    summary_to_events,
    transcript_for,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VillageConfig:
    spool_dir: Path = DEFAULT_SPOOL
    db_path: Path = DEFAULT_DB
    poll_interval_s: float = 0.25
    #: How far back to look for sessions that were already running when the daemon started.
    backfill_within: timedelta = timedelta(hours=12)
    #: How long a departed avatar lingers so the client can animate the exit.
    departure_grace: timedelta = timedelta(seconds=15)
    #: How often to re-read transcripts for titles, token counts and merge request links.
    enrich_interval: timedelta = timedelta(seconds=3)
    #: How much of the recent past the usage strip covers, and how finely it is sliced.
    usage_window: timedelta = timedelta(hours=3)
    usage_bucket: timedelta = timedelta(minutes=1)
    reducer: ReducerConfig = field(default_factory=ReducerConfig)
    reaper: ReaperConfig = field(default_factory=ReaperConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    titler: TitlerConfig = field(default_factory=TitlerConfig)
    enable_history: bool = True


class Village:
    def __init__(self, config: VillageConfig | None = None, titler: Titler | None = None) -> None:
        self.config = config or VillageConfig()
        self.titler = titler or Titler(self.config.titler)
        self.world = World()
        self.spool = SpoolReader(self.config.spool_dir)
        self.store = HistoryStore(self.config.db_path) if self.config.enable_history else None
        self._known_states: dict[str, AgentState] = {}
        #: Byte offset already consumed from each Claude transcript. Only ever moves forward.
        self._enrich_offsets: dict[Path, int] = {}
        #: Parsed session narratives, keyed by session and stamped with the transcript's
        #: size and mtime so a growing file is re-read and a finished one is not.
        self._narratives: dict[str, tuple[tuple[int, float], list[dict]]] = {}
        self._last_enrich: datetime | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def backfill(self) -> int:
        """Reconstruct sessions that predate the daemon.

        Hooks only fire going forward, so without this a daemon restart would empty the village
        even though a dozen agents are still running.
        """
        events: list[VillageEvent] = []
        within = self.config.backfill_within

        for path in recent_claude_transcripts(within):
            summary = summarise_claude(path)
            if summary and summary.session_id:
                events.extend(summary_to_events(summary))
                enriched, offset = read_claude_enrichment(path, 0)
                events.extend(enriched)
                self._enrich_offsets[path] = offset

        for path in recent_cursor_transcripts(within):
            summary = summarise_cursor(path)
            if summary and summary.session_id:
                events.extend(summary_to_events(summary))

        events.sort(key=lambda e: e.ts)
        for event in events:
            self.world = apply(self.world, event, self.config.reducer)
        self._record_backfilled_usage(events)
        self._sync_states(record=False)
        log.info("backfilled %d agents from %d events", len(self.world.agents), len(events))
        return len(self.world.agents)

    def _record_backfilled_usage(self, events: list[VillageEvent]) -> None:
        """Keep the usage strip's history across a restart, without doubling it.

        Backfill re-reads every transcript from byte zero, because that is the only way to
        rebuild an agent's running token total. Writing all of that to the log again would add
        a second copy of every bar on the strip, so only spend newer than what is already on
        disk is kept.

        A session that was quiet for longer than the gap and then resumed can lose a stretch
        this way. Worth it: a wrong total is harder to notice than a missing one.
        """
        if self.store is None:
            return
        watermark = self.store.latest_usage_ts()
        self.store.record_events(
            e for e in events if (e.tokens_in or e.tokens_out) and (watermark is None or e.ts > watermark)
        )

    # -- the loop ----------------------------------------------------------------------

    def poll_once(self, now: datetime | None = None) -> bool:
        """One iteration. Returns True when the world changed and subscribers need a frame."""
        now = now or utcnow()
        before = self.world

        events = self.spool.drain()
        events.extend(self._fresh_enrichment(now))
        events.extend(self._live_titles(now))
        for event in events:
            self.world = apply(self.world, event, self.config.reducer)
        if events and self.store:
            self.store.record_events(events)

        self.world = tick(self.world, now, self.config.reducer)

        for session_id in dead_sessions(self.world, self.config.reaper):
            agent = self.world.agents[session_id]
            self.world = self.world.with_agent(
                replace(agent, state=AgentState.GONE, state_since=now, waiting_reason="process_exited")
            )

        self._sync_states()
        self._retire_departed(now)
        self.titler.start_due(self.world.agents.values(), now)
        return self.world is not before or bool(events)

    def _live_titles(self, now: datetime) -> list[VillageEvent]:
        """Titles the model has finished writing since the last poll, as events."""
        out = []
        for session_id, title in self.titler.collect(now):
            agent = self.world.agents.get(session_id)
            if agent is not None:
                out.append(
                    VillageEvent(
                        source=agent.source, session_id=session_id, kind=EventKind.LIVE_TITLE, ts=now, title=title
                    )
                )
        return out

    def _fresh_enrichment(self, now: datetime) -> list[VillageEvent]:
        """Keep reading transcripts after the daemon has started.

        Hooks carry state, but titles, token counts and merge request links only exist in the
        transcript. Reading those once at backfill meant an MR opened while the village was
        running never appeared - you had to restart the daemon to see it.

        Only the appended bytes are parsed, and only every `enrich_interval`: a directory full
        of transcripts is not worth re-reading at the poll rate.
        """
        interval = self.config.enrich_interval
        # The same window decides which transcripts are worth looking at. Without one there is
        # nothing to look at, which is what `backfill_within=None` asks for.
        if self.config.backfill_within is None:
            return []
        if self._last_enrich is not None and (now - self._last_enrich) < interval:
            return []
        self._last_enrich = now

        out: list[VillageEvent] = []
        for path in recent_claude_transcripts(self.config.backfill_within):
            offset = self._enrich_offsets.get(path, 0)
            events, new_offset = read_claude_enrichment(path, offset, fallback_ts=now)
            self._enrich_offsets[path] = new_offset
            out.extend(events)
        return out

    def _retire_departed(self, now: datetime) -> None:
        """Drop GONE agents, but only after a grace period.

        The frontend needs to see the GONE state at least once to animate the avatar walking out
        of the front door, rather than having it blink out of existence.
        """
        for session_id, agent in list(self.world.agents.items()):
            if agent.state is AgentState.GONE and (now - agent.state_since) > self.config.departure_grace:
                self.world = self.world.without_agent(session_id)
                self._known_states.pop(session_id, None)
                self._narratives.pop(session_id, None)
                self.titler.forget(session_id)

    def _sync_states(self, record: bool = True) -> None:
        for session_id, agent in self.world.agents.items():
            previous = self._known_states.get(session_id)
            if previous is not agent.state:
                if record and self.store:
                    self.store.record_transition(agent, previous)
                self._known_states[session_id] = agent.state

    # -- views -------------------------------------------------------------------------

    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        payload = self.world.to_dict(now)
        for entry, agent in zip(payload["agents"], self.world.agents.values()):
            entry["appearance"] = appearance(agent.session_id, agent.model)
        payload["counts"] = self.counts()
        if self.store:
            payload["stats"] = self.store.daily_stats()
        return payload

    def counts(self) -> dict[str, int]:
        counts = {str(state): 0 for state in AgentState}
        for agent in self.world.agents.values():
            counts[str(agent.state)] += 1
        counts["total"] = len(self.world.agents)
        counts["claude"] = sum(1 for a in self.world.agents.values() if a.source is Source.CLAUDE)
        counts["cursor"] = sum(1 for a in self.world.agents.values() if a.source is Source.CURSOR)
        return counts

    def usage(self, now: datetime | None = None) -> dict:
        """Token spend over time for the bottom strip, with names on the busiest session."""
        if self.store is None:
            return {"bucketS": 0, "buckets": []}
        series = self.store.usage_series(
            window=self.config.usage_window,
            bucket=self.config.usage_bucket,
            now=now,
        )
        for bucket in series["buckets"]:
            top = bucket["top"]
            if top is None:
                continue
            # Named from the live world where possible: the agent may have been renamed since
            # the tokens were spent, and a stale name on a clickable bar is a trap.
            agent = self.world.agents.get(top["sessionId"])
            top["name"] = agent.display_name() if agent else top["sessionId"][:8]
        return series

    def detail(self, session_id: str) -> dict | None:
        agent = self.world.agents.get(session_id)
        if agent is None:
            return None
        data = agent.to_dict()
        data["appearance"] = appearance(agent.session_id, agent.model)
        data["resumeCommand"] = f"claude --resume {agent.session_id}" if agent.source is Source.CLAUDE else None
        # Shipped with the detail so the drawer can grey the Stop button out and say why,
        # instead of offering an action that is going to be refused.
        blocked = why_not_stoppable(agent, self.config.control)
        data["canStop"] = blocked is None
        data["stopBlockedReason"] = blocked
        no_prompt = why_not_promptable(agent, self.config.control)
        data["canPrompt"] = no_prompt is None
        data["promptBlockedReason"] = no_prompt
        data["narrative"] = self._narrative(agent)
        if self.store:
            data["timeline"] = self.store.session_timeline(session_id)
        return data

    def _narrative(self, agent: Agent) -> list[dict]:
        """What the session got through, parsed from its transcript and cached.

        Transcripts run to megabytes and the whole file has to be walked to pair prompts with
        outcomes, which is too much to redo every time a drawer is reopened. Keyed on the
        file's size and mtime, so a live session still re-reads the moment it grows.
        """
        path = transcript_for(agent.session_id, agent.source)
        if path is None:
            return []
        try:
            stat = path.stat()
        except OSError:
            return []

        stamp = (stat.st_size, stat.st_mtime)
        cached = self._narratives.get(agent.session_id)
        if cached and cached[0] == stamp:
            return cached[1]

        turns = [turn.to_dict() for turn in session_narrative(agent.session_id, agent.source)]
        self._narratives[agent.session_id] = (stamp, turns)
        return turns

    # -- control -----------------------------------------------------------------------

    def stop_agent(self, session_id: str, now: datetime | None = None) -> int:
        """Signal an agent's process. Raises `Refused` with a human-readable reason."""
        agent = self.world.agents.get(session_id)
        if agent is None:
            raise Refused("No such agent.", status=404)
        pid = stop(agent, self.config.control)
        # Do not pre-empt the state here. The agent's own SessionEnd hook, or failing that the
        # reaper, decides when it is actually gone - and if the signal did not take, the avatar
        # should stay put rather than lie about it.
        log.info("sent stop signal to %s (pid %d)", session_id, pid)
        return pid

    def prompt_agent(self, session_id: str, text: str) -> str:
        """Type a prompt into an agent's terminal. Returns the tmux pane it went to."""
        agent = self.world.agents.get(session_id)
        if agent is None:
            raise Refused("No such agent.", status=404)
        pane = send_prompt(agent, text, self.config.control)
        # Deliberately no state change here. The agent's own hooks report that it started
        # working, the same as if you had typed it - and if it was busy, Claude has queued
        # the prompt and nothing should happen until it gets to it.
        log.info("sent a prompt to %s (pane %s)", session_id, pane)
        return pane

    def dismiss_agent(self, session_id: str, now: datetime | None = None) -> None:
        """Retire an avatar without touching the agent behind it."""
        agent = self.world.agents.get(session_id)
        if agent is None:
            raise Refused("No such agent.", status=404)
        self.world = self.world.with_agent(
            replace(agent, state=AgentState.GONE, state_since=now or utcnow(), waiting_reason="dismissed")
        )
        self._sync_states()
