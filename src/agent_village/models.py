"""Core domain types for the village.

Everything visual downstream derives from `AgentState`. Events arriving from the two very
different hook systems are normalised into `VillageEvent` first, so the reducer never has to
know whether it is looking at Claude Code or Cursor.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MAX_RECENT_TOOLS = 25
MAX_FILES_TOUCHED = 50
TEXT_CLIP = 2000


def utcnow() -> datetime:
    return datetime.now(UTC)


class Source(StrEnum):
    CLAUDE = "claude"
    CURSOR = "cursor"


class AgentState(StrEnum):
    SPAWNING = "spawning"
    THINKING = "thinking"
    WORKING = "working"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPACTING = "compacting"
    DONE = "done"
    #: Finished long enough ago to have turned in for the night, but still worth looking at.
    SLEEPING = "sleeping"
    GONE = "gone"


#: States an avatar can never leave. Used by the store to retire agents.
TERMINAL_STATES = frozenset({AgentState.GONE})


class EventKind(StrEnum):
    """Normalised verbs. Both hook vocabularies collapse onto these."""

    SESSION_START = "session_start"
    SESSION_RESUME = "session_resume"
    SESSION_END = "session_end"
    PROMPT_SUBMIT = "prompt_submit"
    THOUGHT = "thought"
    RESPONSE = "response"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_FAIL = "tool_fail"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    TURN_END = "turn_end"
    TURN_FAIL = "turn_fail"
    COMPACT_START = "compact_start"
    COMPACT_END = "compact_end"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    TITLE = "title"
    #: A title regenerated from the latest prompts. Kept apart from `TITLE`, which is the one
    #: Claude Code wrote at the start of the session and stays the fallback.
    LIVE_TITLE = "live_title"
    #: A name the user gave the session from the UI. Logged so it survives a restart.
    RENAME = "rename"
    PR_LINK = "pr_link"
    #: One mention of a plan file. Emitted per mention rather than once per plan, because
    #: which plan is *being followed* is a question of how often it comes up.
    PLAN = "plan"
    FILE_EDIT = "file_edit"
    USAGE = "usage"
    #: Pure metadata with no bearing on state. Used by transcript enrichment, which must be
    #: able to fill in a title or a last prompt without moving the avatar or counting a turn.
    CONTEXT = "context"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True, slots=True)
class VillageEvent:
    """One normalised thing that happened to one agent."""

    source: Source
    session_id: str
    kind: EventKind
    ts: datetime
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None
    tool_name: str | None = None
    tool_detail: str | None = None
    tool_id: str | None = None
    text: str | None = None
    title: str | None = None
    reason: str | None = None
    parent_session_id: str | None = None
    agent_type: str | None = None
    agent_id: str | None = None
    is_background: bool = False
    permission_mode: str | None = None
    pid: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    thinking_ms: int = 0
    file_path: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    pr_repo: str | None = None
    raw_event_name: str = ""


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    started_at: datetime
    detail: str | None = None
    tool_id: str | None = None
    ended_at: datetime | None = None
    ok: bool | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.ended_at is None:
            return None
        return int((self.ended_at - self.started_at).total_seconds() * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "detail": self.detail,
            "startedAt": self.started_at.isoformat(),
            "endedAt": self.ended_at.isoformat() if self.ended_at else None,
            "ok": self.ok,
            "durationMs": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class PrLink:
    url: str
    number: int | None = None
    repo: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "number": self.number, "repo": self.repo}


@dataclass(frozen=True, slots=True)
class Agent:
    session_id: str
    source: Source
    state: AgentState
    state_since: datetime
    last_event_at: datetime
    spawned_at: datetime
    title: str | None = None
    #: What the session is doing now, in a few words. See `titling`.
    live_title: str | None = None
    #: A name the user gave it. Beats every generated title, branch suffix included.
    custom_title: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None
    last_prompt: str | None = None
    #: The latest few prompts, oldest first. What a live title is written from.
    recent_prompts: tuple[str, ...] = ()
    last_assistant: str | None = None
    last_thought: str | None = None
    permission_mode: str | None = None
    pid: int | None = None
    parent_session_id: str | None = None
    agent_type: str | None = None
    is_background: bool = False
    current_tool: str | None = None
    open_tools: tuple[ToolCall, ...] = ()
    recent_tools: tuple[ToolCall, ...] = ()
    files_touched: tuple[str, ...] = ()
    pr_links: tuple[PrLink, ...] = ()
    tokens_in: int = 0
    tokens_out: int = 0
    thinking_ms: int = 0
    turns: int = 0
    tool_count: int = 0
    #: True when WAITING was inferred from a dangling tool rather than reported by a hook.
    #: The UI renders inferred waiting with a question mark instead of an exclamation mark,
    #: so a guess never looks like a fact.
    waiting_inferred: bool = False
    waiting_reason: str | None = None
    #: How often each plan file has come up, as `(name, count)`. See `plan`.
    plan_mentions: tuple[tuple[str, int], ...] = ()
    #: Finished and not yet looked at. Set by Claude's `idle_prompt`, cleared by the next prompt.
    #: Distinct from WAITING: the agent is not stuck, it just has something for you to read.
    needs_attention: bool = False

    @property
    def repo(self) -> str | None:
        return os.path.basename(self.cwd.rstrip("/")) if self.cwd else None

    @property
    def is_subagent(self) -> bool:
        return self.parent_session_id is not None

    @property
    def plan(self) -> str | None:
        """The plan file this session is working through, if it is working through one.

        Decided by how often each plan comes up. Agents name neighbouring plans in passing all
        the time; the one actually in hand gets read, quoted and ticked off dozens of times, so
        the leader is obvious and a floor is enough to keep the passing mentions out.
        """
        if not self.plan_mentions:
            return None
        name, count = max(self.plan_mentions, key=lambda item: item[1])
        return name if count >= PLAN_MENTIONS_NEEDED else None

    def _lead(self) -> str | None:
        """The few words saying what this session is about.

        The live title first, because it is the only one that keeps up with the work. Claude
        Code's own title describes how the conversation opened, which is still better than a
        plan inferred from how often a filename comes up.
        """
        return self.live_title or self.title or plan_topic(self.plan)

    def display_name(self) -> str:
        """What to call this agent everywhere in the UI: what it is doing, and where.

        The branch goes after the topic when it says something about the work. A default
        branch does not - most sessions sit on one - so it is left off rather than making
        every other name end in `- main`.
        """
        if self.custom_title:
            return self.custom_title
        lead = self._lead()
        branch = self.git_branch if self.git_branch not in DEFAULT_BRANCHES else None
        if lead:
            return f"{lead} - {branch}" if branch else lead
        if self.git_branch:
            # With nothing to lead on, the repo at least tells two branches of the same name
            # in different checkouts apart.
            return f"{self.repo} / {self.git_branch}"
        return self.repo or f"{self.source} {self.session_id[:8]}"

    def subtitle(self) -> str | None:
        """The line under the name: the detail the name had no room for."""
        if self.plan:
            return f"{self.plan}.md"
        lead = self._lead()
        gist = prompt_gist(self.last_prompt)
        if gist and gist != lead:
            return gist
        if self.title and self.title != lead:
            return self.title
        return f"{self.agent_type} subagent" if self.agent_type else None

    def state_age_s(self, now: datetime) -> float:
        return max(0.0, (now - self.state_since).total_seconds())

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        return {
            "sessionId": self.session_id,
            "source": str(self.source),
            "state": str(self.state),
            "stateSince": self.state_since.isoformat(),
            "stateAgeS": round(self.state_age_s(now), 1),
            "lastEventAt": self.last_event_at.isoformat(),
            "spawnedAt": self.spawned_at.isoformat(),
            "name": self.display_name(),
            "subtitle": self.subtitle(),
            "title": self.title,
            "liveTitle": self.live_title,
            "customTitle": self.custom_title,
            "cwd": self.cwd,
            "repo": self.repo,
            "gitBranch": self.git_branch,
            "model": self.model,
            "lastPrompt": self.last_prompt,
            "lastAssistant": self.last_assistant,
            "lastThought": self.last_thought,
            "permissionMode": self.permission_mode,
            "pid": self.pid,
            "parentSessionId": self.parent_session_id,
            "agentType": self.agent_type,
            "isSubagent": self.is_subagent,
            "isBackground": self.is_background,
            "currentTool": self.current_tool,
            "openTools": [t.to_dict() for t in self.open_tools],
            "recentTools": [t.to_dict() for t in self.recent_tools],
            "filesTouched": list(self.files_touched),
            "prLinks": [p.to_dict() for p in self.pr_links],
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "thinkingMs": self.thinking_ms,
            "turns": self.turns,
            "toolCount": self.tool_count,
            "waitingInferred": self.waiting_inferred,
            "waitingReason": self.waiting_reason,
            "needsAttention": self.needs_attention,
        }


@dataclass(frozen=True, slots=True)
class World:
    agents: dict[str, Agent] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=utcnow)

    def with_agent(self, agent: Agent) -> World:
        agents = dict(self.agents)
        agents[agent.session_id] = agent
        return replace(self, agents=agents, updated_at=agent.last_event_at)

    def without_agent(self, session_id: str) -> World:
        if session_id not in self.agents:
            return self
        agents = dict(self.agents)
        del agents[session_id]
        return replace(self, agents=agents, updated_at=utcnow())

    def live(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.state is not AgentState.GONE]

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        return {
            "updatedAt": self.updated_at.isoformat(),
            "now": now.isoformat(),
            "agents": [a.to_dict(now) for a in self.agents.values()],
        }


#: A line that is scaffolding rather than the request: a lone wrapper tag such as
#: `<task-notification>`, or one of the bare `@path/to/file` references an editor puts on its
#: own line when you attach a file to a prompt.
_SCAFFOLDING = re.compile(r"^\s*(?:</?[\w-]+\s*/?>|@\S+)\s*$")


#: How often a plan must come up before it counts as the plan being followed. Observed on real
#: sessions: the plan in hand lands 20-60 mentions, one named in passing lands two or three.
PLAN_MENTIONS_NEEDED = 5

#: Branches that say nothing about the work, because nearly every session starts on one.
DEFAULT_BRANCHES = frozenset({"main", "master", "develop", "trunk", "HEAD"})

#: How many of the latest prompts an agent remembers for its live title.
RECENT_PROMPTS = 5

#: Words whose capitalisation cannot be guessed from a kebab-case filename. Anything missing
#: here just comes out lower case, which is survivable - add to it as names come up.
_HOUSE_CASING = {
    "ai": "AI",
    "api": "API",
    "ci": "CI",
    "cli": "CLI",
    "github": "GitHub",
    "graphql": "GraphQL",
    "ios": "iOS",
    "mr": "MR",
    "mrs": "MRs",
    "oauth": "OAuth",
    "s3": "S3",
}

#: The sequence number plans are filed under. Useful in a filename, noise in a title.
_PLAN_SEQUENCE = re.compile(r"^\d{2,}-")


def plan_topic(plan: str | None, limit: int = 44) -> str | None:
    """A plan's filename read back as a topic: `0067-graphql-schema-mig` -> `GraphQL schema mig`."""
    if not plan:
        return None
    words = _PLAN_SEQUENCE.sub("", plan).split("-")
    spelled = [_HOUSE_CASING.get(word.lower(), word) for word in words if word]
    if not spelled:
        return None
    if spelled[0] not in _HOUSE_CASING.values():
        spelled[0] = spelled[0][:1].upper() + spelled[0][1:]
    return clip(" ".join(spelled), limit)


def prompt_gist(prompt: str | None, limit: int = 48) -> str | None:
    """The first line of a prompt that actually says something.

    Prompts routinely open with a wrapper tag or a row of attached file paths, so line one
    taken verbatim is often not the request at all.
    """
    for line in (prompt or "").splitlines():
        if line.strip() and not _SCAFFOLDING.match(line):
            return clip(line, limit)
    return None


def clip(text: str | None, limit: int = TEXT_CLIP) -> str | None:
    """Trim free text so one runaway prompt cannot bloat every websocket frame."""
    if text is None:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"
