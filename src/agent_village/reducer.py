"""The pure heart of the village: `(World, VillageEvent) -> World`.

No I/O, no clocks of its own. `apply` handles things that happened; `tick` handles things that
*stopped* happening, which is how an agent sitting on an unanswered permission dialog is noticed.

One deliberate deviation from plan 0077. The plan sent every Claude `idle_prompt` notification to
the park bench. That turned out to be wrong in practice: `idle_prompt` fires 60 seconds after a
turn ends and means "you haven't looked at this yet", not "the agent is stuck". Routing it to the
bench would have left the bench permanently full and the cafe empty, destroying exactly the
signal the bench exists to give. It instead marks the agent as needing attention in the cafe,
while a real `PermissionRequest` still parks it on the bench.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .models import (
    MAX_FILES_TOUCHED,
    MAX_RECENT_TOOLS,
    Agent,
    AgentState,
    EventKind,
    PrLink,
    Source,
    ToolCall,
    VillageEvent,
    World,
)


@dataclass(frozen=True, slots=True)
class ReducerConfig:
    #: A Cursor turn with no hook traffic for this long is probably sitting on an approval
    #: dialog. Cursor exposes no permission-prompt event, and every "before" hook it does expose
    #: is a permission hook we refuse to attach to, so silence is the only signal available.
    #: Deliberately generous: a false bench visit is worse than a late one.
    cursor_silence_waiting_s: float = 45.0
    #: A tool that has been open this long is either genuinely slow or waiting on approval.
    dangling_tool_s: float = 90.0
    #: BLOCKED is a momentary condition; relax it so one failed grep doesn't leave a permanent
    #: red avatar in the infirmary.
    blocked_decay_s: float = 120.0
    #: Move finished agents out of the cafe so it doesn't fill up with the morning's sessions.
    #: They go to bed rather than home: a day's work stays on the map, just out of the way.
    done_to_hotel_s: float = 3600.0
    #: Any agent silent this long is presumed dead even without a SessionEnd. This is also what
    #: finally empties the hotel, so what you can see is the last day's work and no more.
    stale_to_gone_s: float = 86400.0


DEFAULT_CONFIG = ReducerConfig()

#: Events that prove the agent is alive and therefore clear an *inferred* waiting guess.
_ACTIVITY_KINDS = frozenset(
    {
        EventKind.PROMPT_SUBMIT,
        EventKind.THOUGHT,
        EventKind.RESPONSE,
        EventKind.TOOL_START,
        EventKind.TOOL_END,
        EventKind.TOOL_FAIL,
        EventKind.FILE_EDIT,
        EventKind.COMPACT_START,
        EventKind.COMPACT_END,
    }
)


def _ensure(world: World, event: VillageEvent) -> Agent:
    """Fetch the agent, inventing one if the daemon started mid-session."""
    existing = world.agents.get(event.session_id)
    if existing is not None:
        return existing
    return Agent(
        session_id=event.session_id,
        source=event.source,
        state=AgentState.SPAWNING,
        state_since=event.ts,
        last_event_at=event.ts,
        spawned_at=event.ts,
        cwd=event.cwd,
        model=event.model,
        parent_session_id=event.parent_session_id,
        agent_type=event.agent_type,
        is_background=event.is_background,
        pid=event.pid,
    )


def _set_state(agent: Agent, state: AgentState, ts: datetime, **fields) -> Agent:
    if agent.state is not state:
        fields["state"] = state
        fields["state_since"] = ts
    return replace(agent, **fields)


def _merge_context(agent: Agent, event: VillageEvent) -> Agent:
    """Fold in the slow-moving descriptive fields that any event may carry.

    `pid` is the one field where an explicit zero means something. A hook that walked the
    process tree and found no agent reports 0, and that has to be able to clear a pid recorded
    earlier - otherwise a stale pid from a dead process sticks forever and the reaper keeps
    retiring a session that is alive. Events that carry no pid information at all (transcript
    enrichment) send `None` and leave the existing value alone.
    """
    return replace(
        agent,
        last_event_at=max(agent.last_event_at, event.ts),
        cwd=event.cwd or agent.cwd,
        git_branch=event.git_branch or agent.git_branch,
        model=event.model or agent.model,
        permission_mode=event.permission_mode or agent.permission_mode,
        pid=event.pid if event.pid is not None else agent.pid,
        agent_type=event.agent_type or agent.agent_type,
        parent_session_id=event.parent_session_id or agent.parent_session_id,
        title=event.title or agent.title,
    )


def _open_tool(agent: Agent, event: VillageEvent) -> Agent:
    call = ToolCall(
        name=event.tool_name or "tool",
        started_at=event.ts,
        detail=event.tool_detail,
        tool_id=event.tool_id,
    )
    return replace(
        agent,
        open_tools=agent.open_tools + (call,),
        current_tool=call.name,
        tool_count=agent.tool_count + 1,
    )


def _after_tool_state(agent: Agent) -> AgentState:
    """What a finished tool call implies the agent is doing now.

    Claude reports tool starts, so an empty tool stack really does mean the model is between
    calls and generating. Cursor never reports starts - every "before" hook there is a
    permission hook we refuse to register - so its stack is *always* empty, and reading that
    as "no tool running" parked the avatar in `thinking` for an entire turn of solid tool use.
    For that source a completion is the only evidence of work we ever get.
    """
    if agent.open_tools or agent.source is not Source.CLAUDE:
        return AgentState.WORKING
    return AgentState.THINKING


def _close_tool(agent: Agent, event: VillageEvent, ok: bool) -> Agent:
    """Close the matching open tool, falling back to the oldest when ids are absent."""
    match_idx: int | None = None
    if event.tool_id:
        for i, call in enumerate(agent.open_tools):
            if call.tool_id == event.tool_id:
                match_idx = i
                break
    if match_idx is None and event.tool_name:
        for i, call in enumerate(agent.open_tools):
            if call.name == event.tool_name:
                match_idx = i
                break
    if match_idx is None and agent.open_tools:
        match_idx = 0

    if match_idx is None:
        # A tool ended that we never saw start, which is normal for Cursor: it only gives us
        # "after" hooks. Record it as an instantaneous call so the timeline is still complete.
        finished = ToolCall(
            name=event.tool_name or "tool",
            started_at=event.ts,
            detail=event.tool_detail,
            tool_id=event.tool_id,
            ended_at=event.ts,
            ok=ok,
        )
        remaining = agent.open_tools
        tool_count = agent.tool_count + 1
    else:
        call = agent.open_tools[match_idx]
        finished = replace(call, ended_at=event.ts, ok=ok)
        remaining = agent.open_tools[:match_idx] + agent.open_tools[match_idx + 1 :]
        tool_count = agent.tool_count

    recent = (agent.recent_tools + (finished,))[-MAX_RECENT_TOOLS:]
    return replace(
        agent,
        open_tools=remaining,
        recent_tools=recent,
        current_tool=remaining[-1].name if remaining else None,
        tool_count=tool_count,
    )


def _clear_inferred_waiting(agent: Agent) -> Agent:
    if agent.waiting_inferred:
        return replace(agent, waiting_inferred=False, waiting_reason=None)
    return agent


def apply(world: World, event: VillageEvent, config: ReducerConfig = DEFAULT_CONFIG) -> World:
    """Fold one event into the world. Unknown kinds are a no-op, never an error."""
    agent = _merge_context(_ensure(world, event), event)

    if event.kind in _ACTIVITY_KINDS:
        agent = _clear_inferred_waiting(agent)

    ts = event.ts

    match event.kind:
        case EventKind.SESSION_START:
            existing = world.agents.get(event.session_id)
            if existing is not None:
                # A genuine restart under a reused id: keep the avatar, reset the turn.
                agent = _set_state(agent, AgentState.SPAWNING, ts, open_tools=(), current_tool=None)
            else:
                agent = _set_state(agent, AgentState.SPAWNING, ts)
            agent = replace(agent, is_background=event.is_background or agent.is_background)

        case EventKind.SESSION_RESUME:
            # Must not spawn a twin. The avatar simply walks back in.
            agent = _set_state(agent, AgentState.DONE, ts, open_tools=(), current_tool=None)

        case EventKind.SESSION_END:
            agent = _set_state(agent, AgentState.GONE, ts, waiting_reason=event.reason)

        case EventKind.PROMPT_SUBMIT:
            agent = _set_state(
                agent,
                AgentState.THINKING,
                ts,
                last_prompt=event.text or agent.last_prompt,
                turns=agent.turns + 1,
                needs_attention=False,
            )

        case EventKind.THOUGHT:
            agent = _set_state(
                agent,
                AgentState.THINKING,
                ts,
                last_thought=event.text or agent.last_thought,
                thinking_ms=agent.thinking_ms + event.thinking_ms,
            )

        case EventKind.RESPONSE:
            agent = _set_state(agent, AgentState.THINKING, ts, last_assistant=event.text or agent.last_assistant)

        case EventKind.TOOL_START:
            agent = _set_state(_open_tool(agent, event), AgentState.WORKING, ts)

        case EventKind.TOOL_END:
            agent = _close_tool(agent, event, ok=True)
            agent = _set_state(agent, _after_tool_state(agent), ts)

        case EventKind.TOOL_FAIL:
            agent = _close_tool(agent, event, ok=False)
            agent = _set_state(agent, AgentState.BLOCKED, ts, waiting_reason=event.reason)

        case EventKind.FILE_EDIT:
            agent = _close_tool(agent, event, ok=True)
            files = agent.files_touched
            if event.file_path and event.file_path not in files:
                files = (files + (event.file_path,))[-MAX_FILES_TOUCHED:]
            # An edit landing says nothing about the *other* calls in a parallel batch, so this
            # must not drop a Claude agent to `thinking` while its siblings are still running.
            agent = _set_state(agent, _after_tool_state(agent), ts, files_touched=files)

        case EventKind.NEEDS_INPUT:
            agent = _set_state(
                agent,
                AgentState.WAITING,
                ts,
                waiting_inferred=False,
                waiting_reason=event.reason,
                last_assistant=event.text or agent.last_assistant,
            )

        case EventKind.COMPLETED:
            agent = _set_state(agent, AgentState.DONE, ts, needs_attention=True, open_tools=(), current_tool=None)

        case EventKind.TURN_END:
            agent = _set_state(
                agent,
                AgentState.DONE,
                ts,
                last_assistant=event.text or agent.last_assistant,
                open_tools=(),
                current_tool=None,
            )

        case EventKind.TURN_FAIL:
            agent = _set_state(agent, AgentState.BLOCKED, ts, waiting_reason=event.reason, open_tools=())

        case EventKind.COMPACT_START:
            agent = _set_state(agent, AgentState.COMPACTING, ts)

        case EventKind.COMPACT_END:
            agent = _set_state(agent, AgentState.THINKING, ts)

        case EventKind.SUBAGENT_START:
            agent = _set_state(agent, AgentState.WORKING, ts)

        case EventKind.SUBAGENT_STOP:
            agent = _set_state(agent, AgentState.GONE, ts)

        case EventKind.PR_LINK:
            if event.pr_url and all(p.url != event.pr_url for p in agent.pr_links):
                agent = replace(
                    agent,
                    pr_links=agent.pr_links + (PrLink(url=event.pr_url, number=event.pr_number, repo=event.pr_repo),),
                )

        case EventKind.TITLE:
            agent = replace(agent, title=event.title or agent.title)

        case EventKind.PLAN:
            if event.file_path:
                counts = dict(agent.plan_mentions)
                counts[event.file_path] = counts.get(event.file_path, 0) + 1
                agent = replace(agent, plan_mentions=tuple(counts.items()))

        case EventKind.USAGE:
            agent = replace(
                agent,
                tokens_in=agent.tokens_in + event.tokens_in,
                tokens_out=agent.tokens_out + event.tokens_out,
            )

        case EventKind.CONTEXT:
            agent = replace(
                agent,
                last_prompt=event.text or agent.last_prompt,
                last_assistant=event.tool_detail or agent.last_assistant,
            )

        case EventKind.HEARTBEAT:
            pass

        case _:
            return world.with_agent(agent)

    return world.with_agent(agent)


def tick(world: World, now: datetime, config: ReducerConfig = DEFAULT_CONFIG) -> World:
    """Apply the time-based transitions.

    Idempotent, and identity-preserving when nothing changed: callers use `is` to decide whether
    a frame is worth broadcasting, so an idle village must cost nothing.
    """
    out = world
    changed = False
    for agent in list(world.agents.values()):
        updated = _tick_agent(agent, now, config)
        if updated is not agent:
            out = out.with_agent(updated)
            changed = True
    return replace(out, updated_at=now) if changed else world


def _tick_agent(agent: Agent, now: datetime, config: ReducerConfig) -> Agent:
    if agent.state is AgentState.GONE:
        return agent

    silent_s = (now - agent.last_event_at).total_seconds()
    state_age_s = (now - agent.state_since).total_seconds()

    if silent_s > config.stale_to_gone_s:
        return _set_state(agent, AgentState.GONE, now, waiting_reason="stale")

    if agent.state is AgentState.WORKING and agent.open_tools:
        oldest = min(call.started_at for call in agent.open_tools)
        if (now - oldest).total_seconds() > config.dangling_tool_s:
            return _set_state(
                agent,
                AgentState.WAITING,
                now,
                waiting_inferred=True,
                waiting_reason="tool_dangling",
            )

    # Cursor cannot tell us it is waiting, so mid-turn silence is the only clue. `WORKING`
    # counts as mid-turn here too: with no tool-start events a busy Cursor agent now sits in
    # WORKING, and keying this on THINKING alone would strand it there instead of on the bench.
    if (
        agent.source is Source.CURSOR
        and agent.state in (AgentState.THINKING, AgentState.WORKING)
        and silent_s > config.cursor_silence_waiting_s
    ):
        return _set_state(
            agent,
            AgentState.WAITING,
            now,
            waiting_inferred=True,
            waiting_reason="cursor_silence",
        )

    if agent.state is AgentState.BLOCKED and state_age_s > config.blocked_decay_s:
        return _set_state(agent, AgentState.DONE, now)

    # The cafe is the only way into the hotel. Nothing checks in straight from the workshop or
    # the bench: turning in is something you do after you have finished, not instead of it.
    if agent.state is AgentState.DONE and silent_s > config.done_to_hotel_s:
        return _set_state(agent, AgentState.SLEEPING, now, waiting_reason="turned_in")

    return agent
