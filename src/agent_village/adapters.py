"""Translate raw hook payloads into `VillageEvent`s.

Two vocabularies arrive here and only one leaves. Everything in this module is a pure function
of a spool line, which is what makes the whole pipeline testable from recorded fixtures.

Field names were taken from the Claude Code and Cursor hook references. Both are external
schemas outside our control, so every lookup is defensive: an unknown event yields `[]` rather
than an exception, and a missing field yields `None` rather than a `KeyError`. A hook payload
that changes shape upstream must degrade the village, never crash the daemon.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .models import EventKind, Source, VillageEvent, clip

SPOOL_VERSION = 1

#: Claude notification types that mean "the agent is stuck until a human acts".
CLAUDE_WAITING_NOTIFICATIONS = frozenset(
    {
        "permission_prompt",
        "agent_needs_input",
        "elicitation_dialog",
        "elicitation_url_dialog",
        "quota_auto_resume_stale",
        "quota_auto_resume_disabled",
    }
)

#: Types that mean "finished, and you have not looked yet". These go to the cafe with an
#: attention marker rather than to the bench - see the deviation note in `reducer`.
CLAUDE_COMPLETED_NOTIFICATIONS = frozenset({"idle_prompt", "agent_completed"})

#: Notification types that carry no state meaning for the village.
CLAUDE_IGNORED_NOTIFICATIONS = frozenset(
    {
        "auth_success",
        "elicitation_complete",
        "elicitation_response",
        "quota_auto_resume_fired",
    }
)

#: Cursor tool events whose payload names the shell command rather than a tool.
_CURSOR_SHELL_EVENTS = frozenset({"afterShellExecution", "beforeShellExecution"})


def parse_ts(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    return datetime.now(UTC)


def parse_spool_line(line: str) -> list[VillageEvent]:
    """Turn one spool line into zero or more normalised events."""
    line = line.strip()
    if not line:
        return []
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(record, dict):
        return []
    return parse_record(record)


def parse_record(record: dict[str, Any]) -> list[VillageEvent]:
    src = record.get("src") or record.get("source")
    payload = record.get("p") or record.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    event_name = record.get("ev") or record.get("event") or payload.get("hook_event_name") or ""
    ts = parse_ts(record.get("ts"))
    pid = record.get("pid")
    pid = int(pid) if isinstance(pid, (int, str)) and str(pid).isdigit() else None

    if src == Source.CLAUDE:
        return _from_claude(event_name, payload, ts, pid)
    if src == Source.CURSOR:
        return _from_cursor(event_name, payload, ts, pid)
    return []


# --------------------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------------------


def _claude_identity(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return `(avatar_id, parent_id)`.

    Tool events inside a subagent carry the *parent* session id plus an `agent_id`. The subagent
    deserves its own avatar, so it gets a composite id and remembers who spawned it.
    """
    session_id = str(payload.get("session_id") or "")
    agent_id = payload.get("agent_id")
    if agent_id:
        return f"{session_id}:{agent_id}", session_id
    return session_id, None


def _tool_detail(tool_name: str | None, tool_input: Any) -> str | None:
    """A one-line human summary of what a tool call is actually doing."""
    if not isinstance(tool_input, dict):
        return clip(str(tool_input), 160) if tool_input else None
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return clip(value, 160)
    return None


def _from_claude(event: str, p: dict[str, Any], ts: datetime, pid: int | None) -> list[VillageEvent]:
    session_id, parent_id = _claude_identity(p)
    if not session_id:
        return []

    base: dict[str, Any] = {
        "source": Source.CLAUDE,
        "session_id": session_id,
        "ts": ts,
        "cwd": p.get("cwd"),
        "permission_mode": p.get("permission_mode"),
        "parent_session_id": parent_id,
        "agent_type": p.get("agent_type") or None,
        "agent_id": p.get("agent_id") or None,
        "pid": pid,
        "raw_event_name": event,
    }
    tool_name = p.get("tool_name")
    detail = _tool_detail(tool_name, p.get("tool_input"))
    tool_id = p.get("tool_use_id")

    match event:
        case "SessionStart":
            source_kind = p.get("source") or "startup"
            kind = EventKind.SESSION_RESUME if source_kind in {"resume", "fork", "compact"} else EventKind.SESSION_START
            events = [
                VillageEvent(
                    kind=kind,
                    model=p.get("model"),
                    reason=source_kind,
                    title=clip(p.get("session_title"), 80),
                    **base,
                )
            ]
            return events
        case "UserPromptSubmit":
            return [VillageEvent(kind=EventKind.PROMPT_SUBMIT, text=clip(p.get("prompt")), **base)]
        case "PreToolUse":
            return [
                VillageEvent(
                    kind=EventKind.TOOL_START, tool_name=tool_name, tool_detail=detail, tool_id=tool_id, **base
                )
            ]
        case "PostToolUse":
            return [
                VillageEvent(kind=EventKind.TOOL_END, tool_name=tool_name, tool_detail=detail, tool_id=tool_id, **base)
            ]
        case "PostToolUseFailure":
            return [
                VillageEvent(
                    kind=EventKind.TOOL_FAIL,
                    tool_name=tool_name,
                    tool_detail=detail,
                    tool_id=tool_id,
                    reason=clip(p.get("error") or p.get("reason"), 300),
                    **base,
                )
            ]
        case "PermissionRequest":
            # Fires the moment Claude asks. The Notification/permission_prompt type only arrives
            # after the prompt has sat unanswered for ~6 seconds, so this is the live signal and
            # that one is the "user is actually away" escalation.
            return [
                VillageEvent(
                    kind=EventKind.NEEDS_INPUT,
                    tool_name=tool_name,
                    tool_detail=detail,
                    reason="permission_request",
                    text=clip(f"Permission to run {tool_name}" + (f": {detail}" if detail else ""), 300),
                    **base,
                )
            ]
        case "PermissionDenied":
            return [
                VillageEvent(
                    kind=EventKind.TOOL_END,
                    tool_name=tool_name,
                    tool_detail=detail,
                    tool_id=tool_id,
                    reason="permission_denied",
                    **base,
                )
            ]
        case "Notification":
            ntype = p.get("notification_type") or ""
            if ntype in CLAUDE_IGNORED_NOTIFICATIONS:
                return []
            if ntype in CLAUDE_COMPLETED_NOTIFICATIONS:
                return [VillageEvent(kind=EventKind.COMPLETED, reason=ntype, **base)]
            if ntype in CLAUDE_WAITING_NOTIFICATIONS:
                return [
                    VillageEvent(kind=EventKind.NEEDS_INPUT, reason=ntype, text=clip(p.get("message"), 300), **base)
                ]
            return []
        case "Stop":
            return [VillageEvent(kind=EventKind.TURN_END, text=clip(p.get("last_assistant_message")), **base)]
        case "StopFailure":
            return [VillageEvent(kind=EventKind.TURN_FAIL, reason=clip(p.get("reason"), 300), **base)]
        case "SubagentStart":
            return [VillageEvent(kind=EventKind.SUBAGENT_START, text=clip(p.get("prompt") or p.get("task")), **base)]
        case "SubagentStop":
            return [VillageEvent(kind=EventKind.SUBAGENT_STOP, text=clip(p.get("last_assistant_message")), **base)]
        case "PreCompact":
            return [VillageEvent(kind=EventKind.COMPACT_START, reason=p.get("trigger"), **base)]
        case "PostCompact":
            return [VillageEvent(kind=EventKind.COMPACT_END, reason=p.get("trigger"), **base)]
        case "SessionEnd":
            return [VillageEvent(kind=EventKind.SESSION_END, reason=p.get("reason"), **base)]
        case _:
            return []


# --------------------------------------------------------------------------------------
# Cursor
# --------------------------------------------------------------------------------------


def _from_cursor(event: str, p: dict[str, Any], ts: datetime, pid: int | None) -> list[VillageEvent]:
    session_id = str(p.get("conversation_id") or p.get("session_id") or "")
    if not session_id:
        return []

    roots = p.get("workspace_roots")
    cwd = p.get("cwd") or (roots[0] if isinstance(roots, list) and roots else None)

    base: dict[str, Any] = {
        "source": Source.CURSOR,
        "session_id": session_id,
        "ts": ts,
        "cwd": cwd,
        "model": p.get("model"),
        "pid": pid,
        "raw_event_name": event,
    }

    tool_name = p.get("tool_name")
    if event in _CURSOR_SHELL_EVENTS:
        tool_name = "Shell"
    detail = _tool_detail(tool_name, p.get("tool_input") or p.get("command"))
    tool_id = p.get("tool_use_id")

    match event:
        case "sessionStart":
            return [
                VillageEvent(
                    kind=EventKind.SESSION_START,
                    is_background=bool(p.get("is_background_agent")),
                    reason=p.get("composer_mode"),
                    **base,
                )
            ]
        case "beforeSubmitPrompt":
            return [VillageEvent(kind=EventKind.PROMPT_SUBMIT, text=clip(p.get("prompt")), **base)]
        case "afterAgentThought":
            ms = p.get("duration_ms")
            return [
                VillageEvent(
                    kind=EventKind.THOUGHT,
                    text=clip(p.get("text"), 600),
                    thinking_ms=int(ms) if isinstance(ms, (int, float)) else 0,
                    **base,
                )
            ]
        case "afterAgentResponse":
            return [VillageEvent(kind=EventKind.RESPONSE, text=clip(p.get("text")), **base)]
        case "postToolUse":
            return [
                VillageEvent(kind=EventKind.TOOL_END, tool_name=tool_name, tool_detail=detail, tool_id=tool_id, **base)
            ]
        case "postToolUseFailure":
            # `permission_denied` here is a *past-tense* denial, not a live prompt, so it must not
            # park the avatar on the bench.
            failure = p.get("failure_type") or "error"
            kind = EventKind.TOOL_END if failure == "permission_denied" else EventKind.TOOL_FAIL
            return [
                VillageEvent(
                    kind=kind,
                    tool_name=tool_name,
                    tool_detail=detail,
                    tool_id=tool_id,
                    reason=clip(p.get("error_message") or failure, 300),
                    **base,
                )
            ]
        case "afterShellExecution":
            return [
                VillageEvent(
                    kind=EventKind.TOOL_END,
                    tool_name="Shell",
                    tool_detail=clip(p.get("command"), 160),
                    **base,
                )
            ]
        case "afterMCPExecution":
            return [
                VillageEvent(
                    kind=EventKind.TOOL_END,
                    tool_name=f"mcp:{p.get('mcp_server_name') or '?'}/{p.get('tool_name') or '?'}",
                    **base,
                )
            ]
        case "afterFileEdit":
            return [
                VillageEvent(
                    kind=EventKind.FILE_EDIT,
                    file_path=p.get("file_path"),
                    tool_name="Edit",
                    tool_detail=clip(p.get("file_path"), 160),
                    **base,
                )
            ]
        case "preCompact":
            return [VillageEvent(kind=EventKind.COMPACT_START, **base)]
        case "stop":
            status = p.get("status") or "completed"
            kind = EventKind.TURN_END if status == "completed" else EventKind.TURN_FAIL
            return [VillageEvent(kind=kind, reason=status, **base)]
        case "subagentStop":
            return [
                VillageEvent(
                    kind=EventKind.SUBAGENT_STOP,
                    agent_type=p.get("subagent_type"),
                    text=clip(p.get("summary") or p.get("task")),
                    reason=p.get("status"),
                    **base,
                )
            ]
        case "sessionEnd":
            return [VillageEvent(kind=EventKind.SESSION_END, reason=p.get("reason"), **base)]
        case _:
            return []
