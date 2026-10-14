"""Read the session transcripts both tools already write.

Hooks are the authority on *state*, but they never mention a session's AI-generated title, its
token spend, or the merge requests it opened. Only the transcript has those, so the daemon reads
both and lets the reducer merge them.

Transcript events are deliberately restricted to enrichment kinds plus, at startup, enough to
place an avatar. They must never fight the hook stream for control of the state machine.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import EventKind, Source, VillageEvent, clip

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CURSOR_PROJECTS = Path.home() / ".cursor" / "projects"


def _opt_ts(value: Any) -> datetime | None:
    """A record's own timestamp, where it has one. Narrative entries may go undated."""
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _ts(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return fallback
    return fallback


def _assistant_text(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    joined = "\n".join(p for p in parts if p).strip()
    return joined or None


def _user_text(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content if not content.lstrip().startswith("<") else None
    if not isinstance(content, list):
        return None
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            text = c.get("text", "")
            if text and not text.lstrip().startswith("<"):
                return text
    return None


def claude_enrichment(path: Path, *, fallback_ts: datetime | None = None) -> Iterator[VillageEvent]:
    """Yield enrichment events from a whole Claude Code transcript."""
    events, _ = read_claude_enrichment(path, 0, fallback_ts=fallback_ts)
    yield from events


def read_claude_enrichment(
    path: Path,
    offset: int,
    *,
    fallback_ts: datetime | None = None,
) -> tuple[list[VillageEvent], int]:
    """Enrichment from the bytes appended to a transcript since `offset`.

    Returns the events and the offset to resume from next time. Two details matter:

    * parsing stops at the last newline, because a live transcript's tail is routinely a
      half-written record that would otherwise be dropped as corrupt
    * the offset only ever moves forward, because `USAGE` events accumulate - replaying a
      stretch of transcript would silently double an agent's token count
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    # Truncated or replaced underneath us: start again rather than seek past the end.
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset

    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except OSError:
        return [], offset

    cut = chunk.rfind(b"\n")
    if cut == -1:
        return [], offset  # no complete record yet
    text = chunk[: cut + 1].decode("utf-8", errors="replace")
    fallback = fallback_ts or datetime.now(UTC)
    return list(_enrichment_events(text.splitlines(), path.stem, fallback)), offset + cut + 1


#: A plan file, wherever it is referred to - a tool reading it, the agent quoting it, you asking
#: for it. The leading path is thrown away so that `~/w/dotfiles/plans/0067-x.md` and a bare
#: `plans/0067-x.md` are recognised as the same plan.
_PLAN_REFERENCE = re.compile(r"plans/([\w.-]+)\.md")


def _said_or_done(rec: dict) -> Iterator[str]:
    """The parts of a transcript record the session itself produced: prompts, replies, tool calls.

    Tool *output* is left out. A file the agent read or a search it ran names whatever plans
    happen to be in it, and none of those says what this session is working on.
    """
    message = rec.get("message")
    if rec.get("type") not in ("user", "assistant") or not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield content
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            yield block["text"]
        elif block.get("type") == "tool_use":
            yield json.dumps(block.get("input"))


def _enrichment_events(lines: Iterable[str], session_id: str, fallback: datetime) -> Iterator[VillageEvent]:
    """The record types hooks cannot supply: `ai-title`, `pr-link`, per-message usage, plans."""
    cwd: str | None = None
    branch: str | None = None
    model: str | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue

        cwd = rec.get("cwd") or cwd
        branch = rec.get("gitBranch") or branch
        sid = rec.get("sessionId") or session_id
        ts = _ts(rec.get("timestamp"), fallback)
        base = {
            "source": Source.CLAUDE,
            "session_id": sid,
            "ts": ts,
            "cwd": cwd,
            "git_branch": branch,
            "raw_event_name": f"transcript:{rec.get('type')}",
        }

        for text in _said_or_done(rec):
            for plan in _PLAN_REFERENCE.findall(text):
                yield VillageEvent(kind=EventKind.PLAN, file_path=plan, **base)

        match rec.get("type"):
            case "ai-title":
                title = rec.get("aiTitle")
                if title:
                    yield VillageEvent(kind=EventKind.TITLE, title=clip(title, 80), **base)
            case "pr-link":
                url = rec.get("prUrl")
                if url:
                    yield VillageEvent(
                        kind=EventKind.PR_LINK,
                        pr_url=url,
                        pr_number=rec.get("prNumber"),
                        pr_repo=rec.get("prRepository"),
                        **base,
                    )
            case "assistant":
                message = rec.get("message") or {}
                model = message.get("model") or model
                usage = message.get("usage") or {}
                tokens_in = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
                tokens_out = int(usage.get("output_tokens") or 0)
                if tokens_in or tokens_out:
                    yield VillageEvent(
                        kind=EventKind.USAGE,
                        model=model,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        **base,
                    )


@dataclass(frozen=True, slots=True)
class TranscriptSummary:
    """Just enough to place an avatar that existed before the daemon started."""

    session_id: str
    source: Source
    cwd: str | None
    git_branch: str | None
    model: str | None
    title: str | None
    last_prompt: str | None
    last_assistant: str | None
    last_ts: datetime


def summarise_claude(path: Path) -> TranscriptSummary | None:
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    cwd = branch = model = title = last_prompt = last_assistant = None
    session_id = path.stem
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for line in handle:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            cwd = rec.get("cwd") or cwd
            branch = rec.get("gitBranch") or branch
            session_id = rec.get("sessionId") or session_id
            match rec.get("type"):
                case "ai-title":
                    title = rec.get("aiTitle") or title
                case "last-prompt":
                    last_prompt = rec.get("lastPrompt") or last_prompt
                case "assistant":
                    message = rec.get("message") or {}
                    model = message.get("model") or model
                    last_assistant = _assistant_text(message) or last_assistant
                case "user":
                    text = _user_text(rec.get("message") or {})
                    if text:
                        last_prompt = text
    return TranscriptSummary(
        session_id=session_id,
        source=Source.CLAUDE,
        cwd=cwd,
        git_branch=branch,
        model=model,
        title=clip(title, 80),
        last_prompt=clip(last_prompt, 500),
        last_assistant=clip(last_assistant, 500),
        last_ts=mtime,
    )


def summarise_cursor(path: Path) -> TranscriptSummary | None:
    """Cursor transcripts are `<uuid>/<uuid>.jsonl` with plain role/message lines."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    last_prompt = last_assistant = None
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for line in handle:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            message = rec.get("message") or {}
            if rec.get("role") == "user":
                last_prompt = _cursor_prompt(message) or last_prompt
            elif rec.get("role") == "assistant":
                last_assistant = _assistant_text(message) or last_assistant
    return TranscriptSummary(
        session_id=path.stem,
        source=Source.CURSOR,
        cwd=_cursor_cwd(path),
        git_branch=None,
        model=None,
        title=None,
        last_prompt=clip(last_prompt, 500),
        last_assistant=clip(last_assistant, 500),
        last_ts=mtime,
    )


def _cursor_prompt(message: Any) -> str | None:
    """What the human typed, out of a Cursor `user` record.

    Cursor hands the model far more than the prompt - a timestamp, attached skills, file
    contents - all as text blocks in the same record. Only what sits inside `<user_query>`
    came from the person, so a block without those tags is context and is passed over.
    """
    content = message.get("content") if isinstance(message, dict) else message
    blocks = [content] if isinstance(content, str) else content if isinstance(content, list) else []
    for block in blocks:
        text = block if isinstance(block, str) else block.get("text", "") if isinstance(block, dict) else ""
        if "<user_query>" in text:
            return _strip_cursor_wrapper(text) or None
    return None


def _strip_cursor_wrapper(text: str) -> str:
    """Cursor wraps the real prompt in `<user_query>` tags with a timestamp preamble."""
    start = text.find("<user_query>")
    if start == -1:
        return text
    end = text.find("</user_query>", start)
    return text[start + len("<user_query>") : end if end != -1 else None].strip()


def _cursor_cwd(path: Path) -> str | None:
    """Recover the workspace from the slugified project directory name.

    `home-jane-doe-workspace-acme-platform` is ambiguous (hyphens in the slug could be
    either separators or part of a name), so resolve it against directories that actually exist.
    """
    try:
        slug = path.parents[2].name
    except IndexError:
        return None
    parts = slug.split("-")
    candidate = Path("/")
    i = 0
    while i < len(parts):
        for take in range(len(parts) - i, 0, -1):
            nxt = candidate / "-".join(parts[i : i + take])
            if nxt.exists():
                candidate = nxt
                i += take
                break
        else:
            return str(candidate) if candidate != Path("/") else None
    return str(candidate)


# -- what happened in a session ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Turn:
    """One round trip: what the agent was asked, and what it said when it finished."""

    at: datetime | None
    ask: str
    #: The agent's own closing words for this turn. None while the turn is still running.
    outcome: str | None
    tool_calls: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat() if self.at else None,
            "ask": self.ask,
            "outcome": self.outcome,
            "toolCalls": self.tool_calls,
        }


def session_narrative(session_id: str, source: Source, limit: int = 40) -> list[Turn]:
    """A readable account of a session, taken from the agent's own words.

    No model is asked for this. The closing text of every turn is already the agent reporting
    what it just did - these transcripts are full of lines like "All 1319 tests pass now, I'll
    fold the fix into the feature commit" - so pairing each prompt with the text that ends the
    turn it caused gives a summary for free.

    Empty when there is no transcript, which is the honest answer for a session the village
    only ever saw through hooks.
    """
    path = transcript_for(session_id, source)
    if path is None:
        return []
    reader = _claude_steps if source is Source.CLAUDE else _cursor_steps
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            turns = _fold_turns(reader(handle))
    except OSError:
        return []
    return turns[-limit:]


def transcript_for(session_id: str, source: Source) -> Path | None:
    """The transcript file for a session, or None.

    The id arrives from a URL path, so anything that is not a plain session id is refused
    rather than pasted into a glob.
    """
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return None
    if source is Source.CLAUDE:
        return next(iter(CLAUDE_PROJECTS.glob(f"*/{session_id}.jsonl")), None)
    return next(iter(CURSOR_PROJECTS.glob(f"*/agent-transcripts/*/{session_id}.jsonl")), None)


#: What a transcript line contributes to the narrative. `ask` opens a turn, the other two
#: belong to whichever turn is open.
Step = tuple[str, Any]


def _fold_turns(steps: Iterable[Step]) -> list[Turn]:
    """Fold a flat stream of steps into turns.

    Each `said` replaces the previous one, so a turn keeps the *last* thing the agent said.
    Mid-turn narration ("I'll check the rebuilt file next") is a running commentary; the line
    that matters is the one it signed off with.
    """
    turns: list[Turn] = []
    for kind, value in steps:
        if kind == "ask":
            at, text = value
            turns.append(Turn(at=at, ask=clip(text, 240) or "", outcome=None, tool_calls=0))
        elif not turns:
            continue  # the agent spoke before anything was asked of it: nothing to attach to
        elif kind == "said":
            turns[-1] = replace(turns[-1], outcome=clip(value, 400))
        else:
            turns[-1] = replace(turns[-1], tool_calls=turns[-1].tool_calls + 1)
    return turns


def _claude_steps(lines: Iterable[str]) -> Iterator[Step]:
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Subagent traffic runs on its own thread and would interleave nonsensically here.
        if not isinstance(rec, dict) or rec.get("isSidechain") or rec.get("isMeta"):
            continue
        message = rec.get("message") or {}
        if rec.get("type") == "user":
            text = _user_text(message)
            if text:
                yield "ask", (_opt_ts(rec.get("timestamp")), text)
        elif rec.get("type") == "assistant":
            yield from _blocks(message)


def _cursor_steps(lines: Iterable[str]) -> Iterator[Step]:
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        message = rec.get("message") or {}
        if rec.get("role") == "user":
            text = _cursor_prompt(message)
            if text:
                yield "ask", (_opt_ts(rec.get("timestamp")), text)
        elif rec.get("role") == "assistant":
            yield from _blocks(message)


def _blocks(message: dict[str, Any]) -> Iterator[Step]:
    text = _assistant_text(message)
    if text:
        yield "said", text
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield "tool", None


def recent_claude_transcripts(within: timedelta, root: Path | None = None) -> list[Path]:
    root = root or CLAUDE_PROJECTS
    return _recent(root.glob("*/*.jsonl"), within)


def recent_cursor_transcripts(within: timedelta, root: Path | None = None) -> list[Path]:
    root = root or CURSOR_PROJECTS
    return _recent(root.glob("*/agent-transcripts/*/*.jsonl"), within)


def _recent(paths: Iterator[Path], within: timedelta) -> list[Path]:
    cutoff = datetime.now(UTC) - within
    out = []
    for path in paths:
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) >= cutoff:
                out.append(path)
        except OSError:
            continue
    return sorted(out, key=lambda p: os.stat(p).st_mtime)


def summary_to_events(summary: TranscriptSummary) -> list[VillageEvent]:
    """Turn a summary into events the normal reducer can eat.

    Ordered so the state-setting event lands last: the avatar ends up in the cafe, which is the
    honest representation of "this session existed before the daemon did and is not mid-turn".
    """
    base = {
        "source": summary.source,
        "session_id": summary.session_id,
        "ts": summary.last_ts,
        "cwd": summary.cwd,
        "git_branch": summary.git_branch,
        "model": summary.model,
        "raw_event_name": "transcript:backfill",
    }
    events = [
        VillageEvent(
            kind=EventKind.CONTEXT,
            text=summary.last_prompt,
            tool_detail=summary.last_assistant,
            **base,
        )
    ]
    if summary.title:
        events.append(VillageEvent(kind=EventKind.TITLE, title=summary.title, **base))
    events.append(VillageEvent(kind=EventKind.SESSION_RESUME, reason="backfill", **base))
    return events
