"""Acting on agents, rather than only watching them.

This is the only part of the village that writes to anything outside its own process, so the
rules are deliberately narrow: one signal, one kind of agent, and a positive identity check on
the target process before anything is sent.

Two distinct actions, often confused:

* **stop** terminates the agent's process. Claude CLI only, and recoverable - the drawer hands
  you the `claude --resume` line for the session you just stopped.
* **dismiss** is purely cosmetic. It retires the avatar and touches nothing outside the
  village. A still-running agent reappears on its next hook event, which is the honest
  outcome: dismissing something does not make it stop.
* **prompt** types into the agent's terminal. Claude CLI running under tmux only - see
  `send_prompt` for why that is the only channel there is.
"""

from __future__ import annotations

import hmac
import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .models import Agent, Source

#: Process names we are willing to signal. Checked against /proc/<pid>/comm immediately before
#: sending, because pids are recycled and a stale one could by then be anything at all. The
#: Claude CLI reports exactly "claude"; nothing else is on the list, deliberately.
AGENT_COMMS = frozenset({"claude"})

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


@dataclass(frozen=True, slots=True)
class ControlConfig:
    proc_root: Path = Path("/proc")
    allowed_comms: frozenset[str] = field(default_factory=lambda: AGENT_COMMS)
    #: SIGINT, not SIGTERM: it is the Ctrl-C an agent already knows how to handle, so the
    #: transcript is flushed and the session stays resumable.
    stop_signal: int = signal.SIGINT
    tmux: str = "tmux"
    #: Long enough for a real instruction, short enough that a runaway paste cannot be typed
    #: into somebody's terminal one keystroke at a time.
    max_prompt_chars: int = 2000


DEFAULT_CONTROL = ControlConfig()


class Refused(Exception):
    """The village declined to act, with a reason fit to show a human."""

    def __init__(self, reason: str, status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def process_comm(pid: int, config: ControlConfig = DEFAULT_CONTROL) -> str | None:
    """The command name behind a pid, or None if there is no such process."""
    try:
        return (config.proc_root / str(pid) / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def why_not_stoppable(agent: Agent, config: ControlConfig = DEFAULT_CONTROL) -> str | None:
    """None when the agent can be stopped, otherwise a sentence saying why not."""
    if agent.source is not Source.CLAUDE:
        return (
            "Only Claude CLI agents can be stopped from here. A Cursor agent's recorded pid is "
            "the shared editor process, so signalling it would take the whole IDE down."
        )
    if agent.is_subagent:
        return "This is a subagent and shares its parent's process. Stop the parent instead."
    if not agent.pid:
        return "No process was ever recorded for this agent."

    comm = process_comm(agent.pid, config)
    if comm is None:
        return "That process has already exited."
    if comm not in config.allowed_comms:
        return f"pid {agent.pid} is now {comm!r}, not an agent. Refusing to signal it."
    return None


def stop(agent: Agent, config: ControlConfig = DEFAULT_CONTROL, *, kill=os.kill) -> int:
    """Send the stop signal to an agent's process. Returns the pid signalled."""
    reason = why_not_stoppable(agent, config)
    if reason:
        raise Refused(reason)

    assert agent.pid is not None  # why_not_stoppable rejects a missing pid
    try:
        kill(agent.pid, config.stop_signal)
    except ProcessLookupError as exc:
        raise Refused("That process has already exited.") from exc
    except PermissionError as exc:
        raise Refused(f"Not allowed to signal pid {agent.pid}.", status=403) from exc
    return agent.pid


# -- talking back -----------------------------------------------------------------------
#
# There is no API to hand a running CLI a new instruction. The one channel that exists is its
# terminal, and the agents worth prompting are all sitting in a tmux pane, so the village types
# into that pane exactly as you would. Claude queues what it receives mid-turn, which means a
# prompt sent to a busy agent lands after the current turn instead of being lost.


def pane_for_pid(pid: int, config: ControlConfig = DEFAULT_CONTROL, *, run=subprocess.run) -> str | None:
    """The tmux pane a process is attached to, found through its controlling terminal.

    `/proc/<pid>/fd/0` is the pane's pty, and tmux will list the pty of every pane it owns, so
    the two can simply be matched up. Nothing is assumed about how the pane is named or which
    session it belongs to.
    """
    try:
        tty = os.readlink(str(config.proc_root / str(pid) / "fd" / "0"))
    except OSError:
        return None
    if not tty.startswith("/dev/pts/"):
        return None

    try:
        listed = run(
            [config.tmux, "list-panes", "-a", "-F", "#{pane_id} #{pane_tty}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None

    for line in listed.stdout.splitlines():
        pane, _, pane_tty = line.partition(" ")
        if pane_tty.strip() == tty:
            return pane
    return None


def why_not_promptable(agent: Agent, config: ControlConfig = DEFAULT_CONTROL, *, run=subprocess.run) -> str | None:
    """None when the agent can be typed at, otherwise a sentence saying why not."""
    if agent.source is not Source.CLAUDE:
        return (
            "Cursor agents live inside the editor and have no terminal to type into. Only "
            "Claude CLI sessions can be prompted from here."
        )
    if agent.is_subagent:
        return "A subagent takes its instructions from its parent. Prompt the parent instead."
    if not agent.pid:
        return "No process was ever recorded for this agent."
    if process_comm(agent.pid, config) not in config.allowed_comms:
        return "That process is gone, or is no longer an agent."
    if pane_for_pid(agent.pid, config, run=run) is None:
        return "This agent is not running in a tmux pane, so there is nowhere to type."
    return None


def send_prompt(agent: Agent, text: str, config: ControlConfig = DEFAULT_CONTROL, *, run=subprocess.run) -> str:
    """Type a prompt into the agent's terminal and press enter. Returns the pane used."""
    prompt = flatten_prompt(text)
    if not prompt:
        raise Refused("Nothing to send.", status=400)
    if len(prompt) > config.max_prompt_chars:
        raise Refused(f"Prompts are limited to {config.max_prompt_chars} characters.", status=400)

    reason = why_not_promptable(agent, config, run=run)
    if reason:
        raise Refused(reason)
    assert agent.pid is not None  # why_not_promptable rejects a missing pid
    pane = pane_for_pid(agent.pid, config, run=run)
    if pane is None:
        raise Refused("That agent's pane has gone.")

    # `-l` is load-bearing: without it tmux reads the text as key names, so a prompt
    # containing the word "Enter" - or worse, "C-c" - would be executed rather than typed.
    # The text is passed as an argv element, so no shell ever sees it either.
    for keys in ([config.tmux, "send-keys", "-t", pane, "-l", prompt], [config.tmux, "send-keys", "-t", pane, "Enter"]):
        try:
            done = run(keys, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Refused(f"Could not reach tmux: {exc}") from exc
        if done.returncode != 0:
            raise Refused(f"tmux refused the keystrokes: {done.stderr.strip() or done.returncode}")
    return pane


def flatten_prompt(text: str) -> str:
    """Collapse a prompt to the single line a terminal can accept.

    A newline typed into the TUI submits, so a multi-line prompt would arrive as several
    half-finished ones. Control characters go too: this text is being typed into somebody's
    terminal, and an escape sequence there is not a prompt.
    """
    cleaned = "".join(" " if c in "\r\n\t" else "" if ord(c) < 0x20 or ord(c) == 0x7F else c for c in text)
    return " ".join(cleaned.split())


# -- request guard ----------------------------------------------------------------------
#
# The daemon binds to loopback, which keeps it off the network but does nothing about the
# browser already running on this machine. Any page you visit can POST to 127.0.0.1 without
# you noticing, so a loopback server that signals processes needs its own front door.


def _hostname(value: str) -> str:
    """Strip the port from a Host header value, IPv6 brackets included."""
    if value.startswith("["):
        return value[: value.index("]") + 1] if "]" in value else value
    return value.rsplit(":", 1)[0] if ":" in value else value


def host_is_loopback(host: str | None) -> bool:
    return bool(host) and _hostname(host) in LOOPBACK_HOSTS


def origin_is_loopback(origin: str | None) -> bool:
    """A missing Origin is allowed: that is curl, not a browser. A wrong one never is."""
    if origin is None or origin == "null":
        return origin is None
    return (urlsplit(origin).hostname or "") in LOOPBACK_HOSTS


def check_request(
    *,
    token: str,
    supplied_token: str | None,
    host: str | None,
    origin: str | None,
    content_type: str | None,
) -> None:
    """Raise `Refused` unless this request may change something.

    Three independent gates, because each covers a hole the others leave:

    * the **token** is the real lock, and a cross-origin page cannot read it off `/api/session`
    * the **Host** check stops DNS rebinding, where an attacker's name resolves to 127.0.0.1
      but the browser still sends their hostname
    * requiring **JSON** forces a CORS preflight, which this app answers for nobody, so the
      browser refuses the request before it is ever dispatched
    """
    if not host_is_loopback(host):
        raise Refused("Requests must be addressed to the loopback interface.", status=403)
    if not origin_is_loopback(origin):
        raise Refused("Cross-origin requests are not accepted.", status=403)
    if (content_type or "").split(";")[0].strip() != "application/json":
        raise Refused("Content-Type must be application/json.", status=415)
    if not supplied_token or not hmac.compare_digest(supplied_token, token):
        raise Refused("Missing or invalid control token.", status=403)
