"""Live titles: a few words saying what each session is doing now.

Claude Code titles a session once, from how the conversation opened, and a long session moves
on from that within the hour. A third of sessions never get a title at all. So the daemon asks
a small model for a fresh one from the latest prompts whenever a new prompt comes in.

The model is reached through the `claude` CLI in print mode, which runs on the same login as
every other session and needs no API key. It is started with hooks off, no tools, no settings
files and nothing saved: a title call must not show up in the village as an agent of its own,
or leave a transcript behind for the next backfill to find.

Calls run on a small thread pool so a slow model never holds up the poll loop. A session is
titled at most once per `min_interval`, and only while it is doing something - backfill wakes
a hotel full of sleepers, and none of them is worth a call.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import RECENT_PROMPTS, Agent, AgentState, clip

log = logging.getLogger(__name__)

__all__ = ["RECENT_PROMPTS", "Titler", "TitlerConfig", "clean_title", "titling_prompt"]

#: States in which a session is doing nothing that a new title could describe.
_IDLE_STATES = frozenset({AgentState.SLEEPING, AgentState.GONE})

#: How much of each prompt the model sees. A title needs the gist, not a pasted log.
_PROMPT_CLIP = 280

#: Longer than this and the answer is a sentence, not a title.
_MAX_TITLE_WORDS = 10
_MAX_TITLE_CHARS = 60

#: A label the model sometimes puts in front despite being told not to.
_LABEL = re.compile(r"^\s*(?:session\s+)?title\s*:\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TitlerConfig:
    #: Off unless the CLI turns it on, so tests and library use never spawn a model call.
    enabled: bool = False
    model: str = "haiku"
    min_interval: timedelta = timedelta(minutes=3)
    timeout_s: float = 60.0
    workers: int = 2
    command: tuple[str, ...] = field(default=("claude",))


def titling_prompt(agent: Agent) -> str:
    """What the model is asked. Newest prompt last, so the current job is what it reads last."""
    where = agent.repo or "an unknown repo"
    if agent.git_branch:
        where += f", branch {agent.git_branch}"
    lines = [f"Recent requests from one coding-agent session in {where}, oldest first:"]
    lines += [f"- {clip(' '.join(p.split()), _PROMPT_CLIP)}" for p in agent.recent_prompts]
    if agent.title:
        lines.append(f"(The session opened as: {agent.title})")
    lines += [
        "",
        (
            "Reply with only a 3-6 word title for what this session is working on now, "
            "weighted towards the latest request. No quotes, no trailing punctuation."
        ),
    ]
    return "\n".join(lines)


def clean_title(answer: str | None) -> str | None:
    """The title out of the model's answer, or None when there is no usable one."""
    if not answer:
        return None
    first = next((line for line in answer.strip().splitlines() if line.strip()), "")
    title = _LABEL.sub("", first).strip().strip("\"'`*").rstrip(".!:;,").strip()
    if not title or len(title.split()) > _MAX_TITLE_WORDS:
        return None
    return clip(title, _MAX_TITLE_CHARS)


def claude_cli(config: TitlerConfig) -> Callable[[str], str | None]:
    """A title call through the `claude` CLI, isolated from everything a normal session does."""
    executable = shutil.which(config.command[0])
    settings = json.dumps({"disableAllHooks": True})

    def generate(prompt: str) -> str | None:
        if executable is None:
            return None
        argv = [
            executable,
            *config.command[1:],
            "--print",
            "--model",
            config.model,
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--tools",
            "",
            "--settings",
            settings,
        ]
        try:
            done = subprocess.run(
                argv, input=prompt, capture_output=True, text=True, timeout=config.timeout_s, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("live title call failed: %s", exc)
            return None
        if done.returncode != 0:
            log.warning("live title call exited %d: %s", done.returncode, done.stderr.strip()[:200])
            return None
        return done.stdout

    return generate


class Titler:
    """Decides which sessions need a new title, asks for it, and hands back what came in."""

    def __init__(
        self,
        config: TitlerConfig | None = None,
        generate: Callable[[str], str | None] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config = config or TitlerConfig()
        self._generate = generate or claude_cli(self.config)
        self._executor = executor
        #: The prompts each session was last titled from, and when.
        self._asked: dict[str, tuple[tuple[str, ...], datetime]] = {}
        self._in_flight: dict[str, Future] = {}

    def start_due(self, agents: Iterable[Agent], now: datetime) -> None:
        """Ask for a title for every session whose prompts moved on since it last had one."""
        if not self.config.enabled:
            return
        for agent in agents:
            if not self._due(agent, now):
                continue
            self._asked[agent.session_id] = (agent.recent_prompts, now)
            prompt = titling_prompt(agent)
            self._in_flight[agent.session_id] = self._pool().submit(self._generate, prompt)

    def collect(self, now: datetime) -> list[tuple[str, str]]:
        """Titles that have come back since the last call, as `(session_id, title)`."""
        out: list[tuple[str, str]] = []
        for session_id, future in list(self._in_flight.items()):
            if not future.done():
                continue
            del self._in_flight[session_id]
            try:
                title = clean_title(future.result())
            except Exception:
                log.exception("live title call raised")
                continue
            if title:
                out.append((session_id, title))
        return out

    def forget(self, session_id: str) -> None:
        self._asked.pop(session_id, None)

    def _due(self, agent: Agent, now: datetime) -> bool:
        if agent.state in _IDLE_STATES or agent.is_subagent or not agent.recent_prompts:
            return False
        if agent.session_id in self._in_flight:
            return False
        asked = self._asked.get(agent.session_id)
        if asked is None:
            return True
        prompts, when = asked
        return prompts != agent.recent_prompts and (now - when) >= self.config.min_interval

    def _pool(self) -> Executor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.config.workers, thread_name_prefix="titler")
        return self._executor
