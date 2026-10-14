# Agent Village

A Sims-style overview of every Claude Code and Cursor agent running on this machine.

Each agent is an avatar in an isometric 3D village. Where it stands tells you what it is doing:

| Zone       | State                 | Meaning                                              |
| ---------- | --------------------- | ---------------------------------------------------- |
| Workshop   | `thinking`, `working` | Busy. Nothing needed from you.                       |
| Park bench | `waiting`             | **Blocked on you** - a permission prompt or question. |
| Infirmary  | `blocked`             | A tool call failed.                                  |
| Library    | `compacting`          | Compacting its context.                              |
| Cafe       | `done`                | Finished its turn.                                   |
| Hotel      | `sleeping`            | Finished over an hour ago. The day's work, parked.   |
| Front door | `spawning`, `gone`    | Arriving or leaving.                                 |

Hover an avatar for a summary card; click for the full drawer (prompt, recent tools, token
spend, and a `claude --resume` command you can copy).

## Running it

```bash
uv sync                       # or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'
cd web && npm install && npm run build && cd ..
.venv/bin/villaged            # http://127.0.0.1:8847
```

`villaged` binds to loopback and should stay that way: the event stream carries prompt text and
file paths out of your work repos.

On first start it backfills recent sessions from the Claude and Cursor transcript files, so the
village is populated before any hook has fired. `--backfill-hours 0` turns that off.

### Keeping it up

A village that dies with the shell you started it from is not much of a village. Run it under
systemd so it comes back after a crash or a reboot:

```bash
mkdir -p ~/.config/systemd/user
cp scripts/agent-village.service ~/.config/systemd/user/
systemctl --user enable --now agent-village
systemctl --user status agent-village
```

Events are never lost while it is down: `village-emit.sh` writes to the spool directory whether
or not anything is listening, and the daemon drains whatever accumulated when it next starts.

## Wiring up the hooks

Live state comes from agent hooks, not from tailing transcripts. A transcript cannot tell you
whether a session is finished or waiting for you - both look like a file that stopped growing.

```bash
.venv/bin/village-install-hooks --dry-run   # print the merged config, change nothing
.venv/bin/village-install-hooks             # merge in, after backing both files up
.venv/bin/village-install-hooks --uninstall # remove just the village entries
```

The installer merges into `~/.claude/settings.json` and `~/.cursor/hooks.json`, keeping whatever
is already there, and copies both files to `~/.agent-village/config-backups/` first.

**It registers on observational events only.** Cursor's `beforeShellExecution`,
`beforeMCPExecution`, `beforeReadFile`, `beforeTabFileRead`, `subagentStart` and `preToolUse` are
*permission* hooks: a hook on those can block a tool call, or silently auto-approve one. The
village registers on none of them, and `hookinstall.py` raises if anyone adds one to the list.

Hooks apply to sessions started after the install. Restart Cursor and any running `claude` to
pick them up.

## Stopping and dismissing agents

The drawer has two buttons, and they do very different things.

**Stop** sends the agent's process a `SIGINT` - the same Ctrl-C you would type yourself, so the
transcript is flushed and the session stays resumable with the `claude --resume` line shown
just above the button. **Claude CLI only.** A Cursor agent's recorded pid is the shared editor
process, so the button is greyed out there with the reason spelled out. Subagents are refused
too: they share their parent's process.

Immediately before signalling, the daemon reads `/proc/<pid>/comm` and refuses unless it says
`claude`. Pids get recycled, and a stale one could be anything by then.

**Dismiss** retires the avatar and touches nothing outside the village. It is the safe one, and
the only action available on a Cursor agent. Note that it does not make an agent stop: a
still-running agent reappears on its next hook event. Dismiss is for clearing out sessions that
have already finished.

Both need two clicks - the first arms the button, the second acts.

### Why these endpoints are locked down

Binding to loopback keeps the daemon off the network. It does nothing about the browser already
running on this machine: any page you visit can POST to `127.0.0.1:8847` without you noticing.
A loopback server that signals processes needs its own front door, so the two write endpoints
sit behind three independent gates.

| Gate            | Stops                                                                      |
| --------------- | --------------------------------------------------------------------------- |
| Control token   | Everything. A cross-origin script can call `/api/session` but cannot read the reply. |
| `Host` check    | DNS rebinding, where an attacker's name resolves to 127.0.0.1 but the browser still sends their hostname. |
| JSON-only       | Simple requests. Requiring `application/json` forces a CORS preflight, which this app answers for nobody. |

The token is minted fresh at daemon start and never written to disk.

## How it fits together

```
agent  ->  hooks/village-emit.sh  ->  ~/.agent-village/spool/*.json
                                            |
                                     villaged (tail + reduce)
                                            |
                              WebSocket  ->  three.js village
```

`village-emit.sh` runs inside the live agent loop, so it is deliberately dull: it always exits 0,
always prints `{"continue": true}`, writes one file per event under a temp name and `mv`s it into
place so the daemon never reads a half-written event. If the disk is full or the spool is
unwritable it drops the event rather than disturbing the agent.

The daemon's core is a pure reducer, `(World, VillageEvent) -> World`, plus a `tick(World, now)`
for time-based transitions. Both are side-effect free, which is why the state machine can be
tested without a running agent, a socket, or a clock.

### Known gap: Cursor cannot report "waiting"

Cursor has no permission-prompt event, and every "before" hook that would reveal one is a
permission hook we refuse to touch. So a Cursor agent's `waiting` state is **inferred** from a
mid-turn silence of 45 seconds. The UI shows that honestly: a reported wait gets a `!` bubble, an
inferred one gets `?`.

## Layout

| Path                            | What                                                       |
| ------------------------------- | ---------------------------------------------------------- |
| `hooks/village-emit.sh`         | The only code that runs inside an agent loop.              |
| `src/agent_village/adapters.py` | Claude and Cursor hook payloads -> one normalised event.    |
| `src/agent_village/reducer.py`  | The state machine. Pure.                                   |
| `src/agent_village/transcript.py` | Backfill and enrichment (titles, token counts, MR links). |
| `src/agent_village/daemon.py`   | Spool tailer, reaper, snapshot.                            |
| `src/agent_village/control.py`  | Stop and dismiss, plus the request guard on the write endpoints. |
| `src/agent_village/server.py`   | FastAPI, WebSocket fan-out, `/table` debug view.           |
| `web/src/zones.ts`              | Zone geometry and slot assignment. No three.js, so testable. |

## Tests

```bash
.venv/bin/pytest          # 149 tests
cd web && npx vitest run  # 38 tests
```

Zone layout is tested twice: once for world-space distance and once for *screen*-space distance.
Under an isometric camera two avatars offset by `(+d, +d)` sit on the camera's view axis and
project to the same pixel however far apart they really are, so world-space distance alone will
happily pass a layout that renders as one avatar.
