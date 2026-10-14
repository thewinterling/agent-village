"""Notice agents that died without saying goodbye.

`SessionEnd` fires on a clean exit. It does not fire when a terminal window is closed or the
process is killed, and without this module the village slowly fills with ghosts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import AgentState, Source, World


@dataclass(frozen=True, slots=True)
class ReaperConfig:
    proc_root: Path = Path("/proc")
    #: Only Claude CLI pids identify a single session. Under Cursor the hook walks up to the
    #: shared Cursor application process, so a live pid there says nothing about whether one
    #: particular conversation is still open. Reaping on it would retire every Cursor avatar the
    #: moment the editor restarted, so Cursor agents are left to the staleness timer instead.
    reap_sources: frozenset[Source] = frozenset({Source.CLAUDE})


DEFAULT_REAPER = ReaperConfig()

#: States where a vanished process is expected rather than news. An agent only reaches the
#: hotel after an hour of silence, by which time its terminal has usually been closed - reaping
#: on that would empty the hotel as fast as it filled, which is the opposite of the point.
_SETTLED = frozenset({AgentState.GONE, AgentState.SLEEPING})


def dead_sessions(world: World, config: ReaperConfig = DEFAULT_REAPER) -> list[str]:
    """Session ids whose owning process is gone."""
    dead = []
    for agent in world.agents.values():
        if agent.state in _SETTLED or agent.source not in config.reap_sources:
            continue
        if not agent.pid:
            continue
        if agent.is_subagent:
            continue  # a subagent shares its parent's pid; its own stop event retires it
        if not (config.proc_root / str(agent.pid)).exists():
            dead.append(agent.session_id)
    return dead
