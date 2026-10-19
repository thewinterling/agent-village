"""`agent-top`: the village as a btop-style terminal view.

A client of the running daemon, not a second daemon: it reads the same `/api/world` and
`/api/agents/<id>` the browser does and acts through the same control endpoints, so names,
the "what happened" summary, prompts, renames, stop and dismiss all behave exactly as they do
in the web UI. Standard library only - `curses` for the screen, `urllib` for the API.
"""

from __future__ import annotations

import argparse
import curses
import json
import textwrap
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

#: Most urgent first. Kept in step with `STATE_ORDER` in web/src/hud.ts.
STATE_ORDER = ("waiting", "blocked", "working", "thinking", "compacting", "done", "sleeping")

#: Kept in step with `STATE_COLOUR` in web/src/zones.ts.
STATE_COLOUR = {
    "spawning": 0xC3CAD8,
    "thinking": 0xB0B4FF,
    "working": 0x8FC4FF,
    "waiting": 0xFFD089,
    "blocked": 0xFF9AA2,
    "compacting": 0xD2A8FF,
    "done": 0x87E6B0,
    "sleeping": 0x7E86B5,
    "gone": 0x6A7183,
}

KEYS = "↑↓ select  enter details  p prompt  r rename  s stop  d dismiss  q quit"
POLL_S = 1.0
DETAIL_POLL_S = 3.0


# -- talking to the daemon ------------------------------------------------------------------


class Daemon:
    """The daemon's HTTP API. Every failure comes back as `RuntimeError` with a readable reason."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self._token: str | None = None

    def _call(self, request: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                reason = json.load(error).get("error")
            except (ValueError, AttributeError):
                reason = None
            raise RuntimeError(reason or f"request failed ({error.code})") from error
        except (urllib.error.URLError, OSError) as error:
            raise RuntimeError(f"daemon unreachable at {self.url}") from error

    def get(self, path: str) -> Any:
        return self._call(urllib.request.Request(self.url + path))

    def post(self, path: str, payload: dict | None = None) -> Any:
        if self._token is None:
            self._token = self.get("/api/session")["token"]
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload or {}).encode(),
            method="POST",
            # No Origin header: the daemon reads that as a non-browser client, which this is.
            headers={"content-type": "application/json", "x-village-token": self._token},
        )
        return self._call(request)

    def act(self, session_id: str, action: str, payload: dict | None = None) -> Any:
        return self.post(f"/api/agents/{urllib.request.quote(session_id, safe='')}/{action}", payload)

    def detail(self, session_id: str) -> dict:
        return self.get(f"/api/agents/{urllib.request.quote(session_id, safe='')}")


# -- what goes on screen, kept free of curses so it can be tested ---------------------------


def order(agents: list[dict]) -> list[dict]:
    """Most urgent state first, then whoever has been in it longest. Same as the web list."""

    def rank(agent: dict) -> int:
        state = agent.get("state")
        return STATE_ORDER.index(state) if state in STATE_ORDER else len(STATE_ORDER)

    return sorted(agents, key=lambda a: (rank(a), -float(a.get("stateAgeS") or 0)))


def dur(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def tokens(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def ago(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return ""
    seconds = ((now or datetime.now(UTC)) - datetime.fromisoformat(iso)).total_seconds()
    return "just now" if seconds < 1 else f"{dur(seconds)} ago"


def fit(text: str, width: int) -> str:
    """Pad or cut to exactly `width` columns.

    shortcut: counts code points, not terminal cells, so a wide emoji in a name shifts the row
    by a column; upgrade to `wcwidth` if that ever shows up in practice.
    """
    if width <= 0:
        return ""
    return text.ljust(width) if len(text) <= width else text[: width - 1] + "…"


#: (header, width) per column. Width 0 means "whatever is left", and only the name gets that.
COLUMNS = (("STATE", 11), ("AGE", 7), ("NAME", 0), ("REPO", 22), ("TOOL", 10), ("MODEL", 16), ("TOK", 7), ("SEEN", 12))


def column_widths(total: int) -> list[int]:
    fixed = sum(w for _, w in COLUMNS) + len(COLUMNS) - 1
    return [w or max(12, total - fixed) for _, w in COLUMNS]


def row_cells(agent: dict, now: datetime | None = None) -> list[str]:
    tool = agent.get("currentTool") or next((t.get("name") for t in agent.get("openTools") or []), "") or ""
    state = agent.get("state", "") + ("?" if agent.get("waitingInferred") else "")
    name = ("↳ " if agent.get("isSubagent") else "") + (agent.get("name") or agent.get("sessionId", ""))
    repo = agent.get("repo") or ""
    if agent.get("gitBranch"):
        repo = f"{repo}:{agent['gitBranch']}" if repo else agent["gitBranch"]
    return [
        state,
        dur(agent.get("stateAgeS") or 0),
        name,
        repo,
        tool,
        agent.get("model") or "",
        tokens(int(agent.get("tokensIn") or 0) + int(agent.get("tokensOut") or 0)),
        ago(agent.get("lastEventAt"), now),
    ]


def format_row(cells: list[str], widths: list[int]) -> str:
    return " ".join(fit(cell, width) for cell, width in zip(cells, widths, strict=True))


def detail_lines(d: dict, width: int) -> list[tuple[str, str]]:
    """The drawer as `(style, text)` lines: `title`, `dim`, `head`, `state` or `` for plain."""
    width = max(20, width)
    out: list[tuple[str, str]] = []

    def para(text: str, style: str = "", indent: str = "") -> None:
        for line in textwrap.wrap(text, width, initial_indent=indent, subsequent_indent=indent) or [""]:
            out.append((style, line))

    para(d.get("name") or d.get("sessionId", ""), "title")
    if d.get("subtitle"):
        para(d["subtitle"], "dim")
    chips = [f"{d.get('state')}{' (inferred)' if d.get('waitingInferred') else ''} · {dur(d.get('stateAgeS') or 0)}"]
    chips += [c for c in (d.get("source"), d.get("model")) if c]
    if d.get("isSubagent"):
        chips.append(f"subagent: {d['agentType']}" if d.get("agentType") else "subagent")
    out.append(("state", "  ".join(chips)))

    if d.get("prLinks"):
        out += [("", ""), ("head", f"MERGE REQUESTS ({len(d['prLinks'])})")]
        for p in d["prLinks"]:
            out.append(("", f"  {'!' + str(p['number']) if p.get('number') else 'MR'}  {p.get('url')}"))

    narrative = d.get("narrative") or []
    if narrative:
        out += [("", ""), ("head", f"WHAT HAPPENED ({len(narrative)} turn{'s' if len(narrative) != 1 else ''})")]
        for turn in narrative:
            para(turn.get("ask") or "", "", "▸ ")
            para(turn.get("outcome") or "still working on it…", "dim" if not turn.get("outcome") else "", "  ")
            meta = [
                turn["at"][11:16] if turn.get("at") else "",
                f"{turn['toolCalls']} tool calls" if turn.get("toolCalls") else "",
            ]
            if any(meta):
                out.append(("dim", "  " + " · ".join(m for m in meta if m)))
    elif d.get("lastPrompt"):
        out += [("", ""), ("head", "LAST PROMPT")]
        para(d["lastPrompt"], "", "  ")

    if d.get("resumeCommand"):
        out += [("", ""), ("head", "RESUME"), ("", "  " + d["resumeCommand"])]
    if not d.get("canPrompt") and d.get("promptBlockedReason"):
        out += [("", "")]
        para("prompt: " + d["promptBlockedReason"], "dim")
    if not d.get("canStop") and d.get("stopBlockedReason"):
        para("stop: " + d["stopBlockedReason"], "dim")
    out += [("", ""), ("dim", d.get("sessionId", ""))]
    return out


def xterm256(rgb: int) -> int:
    """Nearest colour in the xterm 6x6x6 cube, for terminals that cannot redefine colours."""
    levels = (0, 95, 135, 175, 215, 255)

    def nearest(v: int) -> int:
        return min(range(6), key=lambda i: abs(levels[i] - v))

    r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return 16 + 36 * nearest(r) + 6 * nearest(g) + nearest(b)


# -- the screen -----------------------------------------------------------------------------


class Top:
    def __init__(self, screen: curses.window, daemon: Daemon) -> None:
        self.screen = screen
        self.daemon = daemon
        self.agents: list[dict] = []
        self.counts: dict[str, int] = {}
        self.selected: str | None = None
        self.detail: dict | None = None
        self.detail_open = False
        self.detail_scroll = 0
        self.status = "connecting…"
        self.note = ""
        self._next_poll = 0.0
        self._next_detail = 0.0
        self._init_colours()

    def _init_colours(self) -> None:
        self.colour: dict[str, int] = {}
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        many = curses.COLORS >= 256
        for i, (state, rgb) in enumerate(STATE_COLOUR.items(), start=1):
            curses.init_pair(i, xterm256(rgb) if many else curses.COLOR_CYAN, -1)
            self.colour[state] = curses.color_pair(i)
        curses.init_pair(20, 244 if many else curses.COLOR_WHITE, -1)
        self.colour["dim"] = curses.color_pair(20)
        curses.init_pair(21, 75 if many else curses.COLOR_BLUE, -1)
        self.colour["frame"] = curses.color_pair(21)

    # -- data --

    def poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now >= self._next_poll:
            self._next_poll = now + POLL_S
            try:
                world = self.daemon.get("/api/world")
                self.agents = order(world.get("agents") or [])
                self.counts = world.get("counts") or {}
                self.status = "live"
            except RuntimeError as error:
                self.status = str(error)
            ids = [a["sessionId"] for a in self.agents]
            if self.selected not in ids:
                self.selected = ids[0] if ids else None
        if self.detail_open and self.selected and (force or now >= self._next_detail):
            self._next_detail = now + DETAIL_POLL_S
            try:
                self.detail = self.daemon.detail(self.selected)
            except RuntimeError as error:
                self.detail = {"sessionId": self.selected, "name": "(unavailable)", "subtitle": str(error)}

    def current(self) -> dict | None:
        return next((a for a in self.agents if a["sessionId"] == self.selected), None)

    # -- drawing --

    def draw(self) -> None:
        s = self.screen
        s.erase()
        height, width = s.getmaxyx()
        if height < 8 or width < 40:
            s.addstr(0, 0, fit("window too small", width - 1))
            s.refresh()
            return

        self._header(width)
        list_h = height - 3
        if self.detail_open:
            list_h = max(5, (height - 3) * 2 // 5)
        self._box(1, 0, list_h, width, f" agents ({len(self.agents)}) ")
        self._table(2, 1, list_h - 2, width - 2)
        if self.detail_open:
            self._box(1 + list_h, 0, height - 2 - list_h, width, " details ")
            self._detail(2 + list_h, 2, height - 4 - list_h, width - 4)
        s.addstr(height - 1, 0, fit(self.note or KEYS, width - 1), self.colour.get("dim", 0))
        s.refresh()

    def _header(self, width: int) -> None:
        s = self.screen
        s.addstr(0, 0, " Agent Village ", curses.A_BOLD | curses.A_REVERSE)
        x = 16
        for state in STATE_ORDER:
            if self.counts.get(state) and x < width - 20:
                label = f" ● {state} {self.counts[state]} "
                s.addstr(0, x, label, self.colour.get(state, 0))
                x += len(label)
        status = f" {self.status} "
        live = self.status == "live"
        s.addstr(
            0,
            max(x, width - len(status) - 1),
            fit(status, width - x - 1),
            self.colour["done" if live else "blocked"] if self.colour else 0,
        )

    def _box(self, y: int, x: int, h: int, w: int, title: str) -> None:
        # Drawn by hand: `border()` takes single-byte characters only, so no rounded corners.
        frame = self.colour.get("frame", 0)
        s = self.screen
        s.addstr(y, x, "╭" + "─" * (w - 2) + "╮", frame)
        for row in range(y + 1, y + h - 1):
            s.addstr(row, x, "│", frame)
            s.addstr(row, x + w - 1, "│", frame)
        s.addstr(y + h - 1, x, "╰" + "─" * (w - 2) + "╯", frame)
        s.addstr(y, x + 2, title, frame | curses.A_BOLD)

    def _table(self, y: int, x: int, h: int, w: int) -> None:
        s = self.screen
        widths = column_widths(w)
        s.addstr(y, x, format_row([c for c, _ in COLUMNS], widths), curses.A_BOLD | self.colour.get("dim", 0))
        rows = h - 1
        if not self.agents:
            s.addstr(y + 1, x, fit("nobody in the village.", w), self.colour.get("dim", 0))
            return
        ids = [a["sessionId"] for a in self.agents]
        at = ids.index(self.selected) if self.selected in ids else 0
        top = max(0, min(at - rows // 2, len(ids) - rows))
        now = datetime.now(UTC)
        for i, agent in enumerate(self.agents[top : top + rows]):
            line = format_row(row_cells(agent, now), widths)
            chosen = agent["sessionId"] == self.selected
            attr = curses.A_REVERSE if chosen else 0
            s.addstr(y + 1 + i, x, line, attr)
            # The state cell in its colour, the way the web list draws a coloured dot.
            s.addstr(
                y + 1 + i, x, fit(row_cells(agent, now)[0], widths[0]), attr | self.colour.get(agent.get("state"), 0)
            )

    def _detail(self, y: int, x: int, h: int, w: int) -> None:
        if not self.detail:
            self.screen.addstr(y, x, "loading…", self.colour.get("dim", 0))
            return
        lines = detail_lines(self.detail, w)
        self.detail_scroll = max(0, min(self.detail_scroll, len(lines) - h))
        styles = {
            "title": curses.A_BOLD,
            "dim": self.colour.get("dim", 0),
            "head": curses.A_BOLD | self.colour.get("dim", 0),
            "state": self.colour.get(self.detail.get("state"), 0),
        }
        for i, (style, text) in enumerate(lines[self.detail_scroll : self.detail_scroll + h]):
            self.screen.addstr(y + i, x, fit(text, w), styles.get(style, 0))

    # -- input --

    def ask(self, label: str, initial: str = "") -> str | None:
        """A one-line editor on the bottom row. Enter returns the text, Esc returns None."""
        curses.curs_set(1)
        self.screen.timeout(-1)
        text = list(initial)
        try:
            while True:
                height, width = self.screen.getmaxyx()
                shown = label + "".join(text)
                self.screen.move(height - 1, 0)
                self.screen.clrtoeol()
                self.screen.addstr(height - 1, 0, shown[-(width - 1) :])
                self.screen.refresh()
                key = self.screen.get_wch()
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(text)
                if key == "\x1b":
                    return None
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if text:
                        text.pop()
                elif key == "\x15":  # Ctrl-U, as in a shell
                    text.clear()
                elif isinstance(key, str) and key.isprintable():
                    text.append(key)
        finally:
            curses.curs_set(0)
            self.screen.timeout(int(POLL_S * 1000 / 4))

    def act(self, action: str, payload: dict | None = None, done: str = "") -> None:
        agent = self.current()
        if not agent:
            return
        try:
            self.daemon.act(agent["sessionId"], action, payload)
            self.note = done
        except RuntimeError as error:
            self.note = f"{action}: {error}"
        self.poll(force=True)

    def handle(self, key: int | str) -> bool:
        """One keypress. False means quit."""
        ids = [a["sessionId"] for a in self.agents]
        at = ids.index(self.selected) if self.selected in ids else 0
        agent = self.current()
        self.note = ""
        match key:
            case "q" | "Q":
                return False
            case curses.KEY_UP | "k":
                self._select(ids, at - 1)
            case curses.KEY_DOWN | "j":
                self._select(ids, at + 1)
            case curses.KEY_HOME | "g":
                self._select(ids, 0)
            case curses.KEY_END | "G":
                self._select(ids, len(ids) - 1)
            case "\n" | "\r" | curses.KEY_ENTER | curses.KEY_RIGHT | "l":
                self.detail_open = not self.detail_open if key in ("\n", "\r", curses.KEY_ENTER) else True
                self.detail, self.detail_scroll = None, 0
                self.poll(force=True)
            case "\x1b" | curses.KEY_LEFT | "h":
                self.detail_open = False
            case curses.KEY_NPAGE | " ":
                self.detail_scroll += 10
            case curses.KEY_PPAGE | "b":
                self.detail_scroll = max(0, self.detail_scroll - 10)
            case "p" if agent:
                text = self.ask(f"prompt {fit(agent['name'], 30).rstrip()} > ")
                if text and text.strip():
                    self.act(
                        "prompt", {"text": text.strip()}, "sent. the agent picks it up when it next reads its input."
                    )
            case "r" if agent:
                name = self.ask("rename (empty for the generated name) > ", agent.get("customTitle") or agent["name"])
                if name is not None:
                    self.act("rename", {"title": name}, "renamed.")
                    self._next_detail = 0
            case "s" if agent:
                if self.ask(f"stop {fit(agent['name'], 40).rstrip()}? Ctrl-C to the process [y/N] ") in ("y", "Y"):
                    self.act("stop", done="signal sent. resume with: claude --resume " + agent["sessionId"])
            case "d" if agent:
                if self.ask(f"dismiss {fit(agent['name'], 40).rstrip()} from view? [y/N] ") in ("y", "Y"):
                    self.act("dismiss", done="dismissed.")
        return True

    def _select(self, ids: list[str], index: int) -> None:
        if ids:
            self.selected = ids[max(0, min(index, len(ids) - 1))]
            self.detail, self.detail_scroll, self._next_detail = None, 0, 0

    def run(self) -> None:
        curses.curs_set(0)
        self.screen.keypad(True)
        self.screen.timeout(int(POLL_S * 1000 / 4))
        while True:
            self.poll()
            self.draw()
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue  # timed out, nothing pressed
            if key == curses.KEY_RESIZE:
                continue
            if not self.handle(key):
                return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch and steer the village from the terminal.")
    parser.add_argument("--url", default="http://127.0.0.1:8847", help="where villaged is listening")
    args = parser.parse_args(argv)
    # Esc should close a pane now, not after curses' default one-second wait for an escape sequence.
    curses.set_escdelay(25)
    curses.wrapper(lambda screen: Top(screen, Daemon(args.url)).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
