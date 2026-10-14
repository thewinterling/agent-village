#!/usr/bin/env python3
"""Populate the spool with a believable village.

Useful for working on the world without waiting for real agents to wander into the right
states, and for checking that every zone renders with something in it.

    python scripts/seed-demo.py            # one of each state
    python scripts/seed-demo.py --live     # keep nudging them around
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

SPOOL = Path(os.environ.get("AGENT_VILLAGE_SPOOL", Path.home() / ".agent-village" / "spool"))

REPOS = ["acme-platform", "dotfiles", "infra", "acme-search-index", "agent-village"]
TOOLS = ["Bash", "Read", "Edit", "Grep", "WebFetch", "Write", "Task"]
TITLES = [
    "Plan 0068 long-term storage",
    "Flaky queue-worker test",
    "Dataset export to Parquet",
    "Lint check and push",
    "GraphQL schema migration",
    "Rolling upload safety",
    "PR 2752 review comments",
    "Schema contract check",
]


def emit(src: str, event: str, payload: dict) -> None:
    SPOOL.mkdir(parents=True, exist_ok=True)
    now = time.time()
    name = f"{now:.6f}-{random.randint(1000, 9999)}"
    tmp = SPOOL / f".wip-{name}"
    # pid 0 means "no owning process". Stamping this script's pid would make the reaper
    # correctly retire every demo agent the moment the seeder exits.
    tmp.write_text(json.dumps({"v": 1, "src": src, "ev": event, "ts": now, "pid": 0, "p": payload}))
    tmp.rename(SPOOL / f"{name}.json")


def claude(session: str, event: str, **payload) -> None:
    emit(
        "claude",
        event,
        {
            "session_id": session,
            "cwd": f"/home/u/workspace/{payload.pop('repo', REPOS[0])}",
            "hook_event_name": event,
            **payload,
        },
    )


def cursor(session: str, event: str, **payload) -> None:
    emit("cursor", event, {"conversation_id": session, "hook_event_name": event, **payload})


def seed() -> None:
    """One agent per zone, so every part of the world has something in it."""
    # Workshop: mid tool call.
    for i in range(3):
        s = f"demo-work-{i}"
        claude(s, "SessionStart", source="startup", model="claude-opus-5-5", repo=REPOS[i % len(REPOS)])
        claude(s, "UserPromptSubmit", prompt=random.choice(TITLES), repo=REPOS[i % len(REPOS)])
        claude(
            s,
            "PreToolUse",
            tool_name=random.choice(TOOLS),
            tool_use_id=f"t{i}",
            tool_input={"command": "pytest -q tests/"},
            repo=REPOS[i % len(REPOS)],
        )

    # Workshop: thinking.
    claude("demo-think", "UserPromptSubmit", prompt="why is the retarget contract rejecting s8g09?")

    # Park: a real permission prompt, and an inferred one.
    claude("demo-perm", "PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build/ && make clean"})
    cursor(
        "demo-cursor-wait",
        "beforeSubmitPrompt",
        prompt="refactor the uploader",
        workspace_roots=["/home/u/workspace/dotfiles"],
    )

    # Infirmary.
    claude("demo-blocked", "PostToolUseFailure", tool_name="Bash", error="pytest exited 1: 3 failed, 211 passed")

    # Library.
    claude("demo-compact", "PreCompact", trigger="auto")

    # Cafe, one with a merge request to show off.
    claude("demo-done", "UserPromptSubmit", prompt="open the MR")
    claude("demo-done", "Stop", last_assistant_message="Opened draft MR !2752 with the topic gate changes.")
    claude("demo-done", "Notification", notification_type="idle_prompt")

    # A subagent trailing its parent.
    claude(
        "demo-work-0",
        "SubagentStart",
        agent_id="ag-explore",
        agent_type="explore",
        prompt="find every caller of RecordingService.finish",
    )
    claude(
        "demo-work-0",
        "PreToolUse",
        agent_id="ag-explore",
        agent_type="explore",
        tool_name="Grep",
        tool_use_id="sg1",
        tool_input={"pattern": "finish\\("},
    )

    print(f"seeded 10 agents into {SPOOL}")


def live(rounds: int) -> None:
    """Keep the demo village moving so animations and transitions can be eyeballed."""
    sessions = [f"demo-work-{i}" for i in range(3)]
    for n in range(rounds):
        s = random.choice(sessions)
        roll = random.random()
        if roll < 0.45:
            claude(s, "PostToolUse", tool_name=random.choice(TOOLS), tool_use_id=f"t{n}")
            claude(
                s,
                "PreToolUse",
                tool_name=random.choice(TOOLS),
                tool_use_id=f"t{n + 1}",
                tool_input={"command": "yarn nx run-many -t test"},
            )
        elif roll < 0.6:
            claude(s, "PermissionRequest", tool_name="Bash", tool_input={"command": "git push --force-with-lease"})
        elif roll < 0.75:
            claude(s, "Stop", last_assistant_message="Done. 42 tests pass.")
        elif roll < 0.9:
            claude(s, "UserPromptSubmit", prompt=random.choice(TITLES))
        else:
            claude(s, "PostToolUseFailure", tool_name="Bash", error="exit 1")
        time.sleep(1.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="keep the village moving")
    parser.add_argument("--rounds", type=int, default=200)
    args = parser.parse_args()
    seed()
    if args.live:
        live(args.rounds)
