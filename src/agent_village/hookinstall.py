"""Register (and unregister) the village emitter in the live Claude Code and Cursor hook config.

Three rules this module exists to enforce:

1. **Merge, never overwrite.** Both config files already contain hooks the user depends on.
2. **Idempotent.** Running the installer twice leaves exactly one village entry per event.
3. **Observational events only.** Cursor treats `beforeShellExecution`, `beforeMCPExecution`,
   `beforeReadFile`, `beforeTabFileRead`, `subagentStart` and `preToolUse` as *permission*
   hooks: on those, a handler that exits 0 with JSON which does not match the permission schema
   **blocks the action**, and one that returns `{"permission": "allow"}` would silently
   auto-approve commands the user would otherwise be asked about. A read-only observatory has
   no business anywhere near that decision, so it stays off those events entirely.

The planning functions are pure so the merge behaviour can be tested without touching the
user's real configuration.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

MARKER = "village-emit.sh"

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CURSOR_HOOKS = Path.home() / ".cursor" / "hooks.json"

#: Claude Code events we listen to. All are safe: every entry is registered with `async: true`,
#: so Claude Code neither waits for the hook nor reads its output as a decision.
CLAUDE_EVENTS = [
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "Notification",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
]

#: Cursor events we listen to. Deliberately excludes every permission hook; see module docstring.
CURSOR_EVENTS = [
    "sessionStart",
    "sessionEnd",
    "beforeSubmitPrompt",
    "afterShellExecution",
    "afterMCPExecution",
    "afterFileEdit",
    "postToolUse",
    "postToolUseFailure",
    "afterAgentResponse",
    "afterAgentThought",
    "preCompact",
    "stop",
    "subagentStop",
]

#: Never register on these, whatever else changes. Guard rail against a careless edit above.
CURSOR_FORBIDDEN = frozenset(
    {
        "beforeShellExecution",
        "beforeMCPExecution",
        "beforeReadFile",
        "beforeTabFileRead",
        "subagentStart",
        "preToolUse",
    }
)


def _is_village(entry: Any) -> bool:
    return isinstance(entry, dict) and MARKER in str(entry.get("command", ""))


def plan_claude(settings: dict[str, Any], script: Path, *, enable: bool = True) -> dict[str, Any]:
    """Return a new settings dict with the village hooks merged in (or stripped out)."""
    out = json.loads(json.dumps(settings))  # deep copy, config is small and plain JSON
    hooks: dict[str, Any] = out.setdefault("hooks", {})

    for event in CLAUDE_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = [] if groups is None else [groups]

        # Drop any previous village group, then re-add. That is what makes this idempotent.
        cleaned = []
        for group in groups:
            inner = group.get("hooks", []) if isinstance(group, dict) else []
            kept = [h for h in inner if not _is_village(h)]
            if isinstance(group, dict) and not kept and inner:
                continue  # the whole group was ours
            if isinstance(group, dict):
                group = {**group, "hooks": kept} if inner else group
            cleaned.append(group)

        if enable:
            cleaned.append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{script} claude {event}",
                            "async": True,
                            "timeout": 5,
                        }
                    ]
                }
            )

        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event, None)

    if not hooks:
        out.pop("hooks", None)
    return out


def plan_cursor(config: dict[str, Any], script: Path, *, enable: bool = True) -> dict[str, Any]:
    """Return a new hooks.json dict with the village hooks merged in (or stripped out)."""
    out = json.loads(json.dumps(config))
    out.setdefault("version", 1)
    hooks: dict[str, Any] = out.setdefault("hooks", {})

    # Strip stale village entries everywhere, including from events we no longer register on.
    for event, entries in list(hooks.items()):
        if isinstance(entries, list):
            kept = [e for e in entries if not _is_village(e)]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event)

    if enable:
        for event in CURSOR_EVENTS:
            if event in CURSOR_FORBIDDEN:
                raise ValueError(f"refusing to register on Cursor permission hook {event!r}")
            hooks.setdefault(event, []).append({"command": f"{script} cursor {event}"})

    return out


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _backup(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, target)
    return target


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".village-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the Agent Village hooks.")
    parser.add_argument("--uninstall", action="store_true", help="remove the village hooks again")
    parser.add_argument("--dry-run", action="store_true", help="print the merged config, change nothing")
    parser.add_argument("--claude-settings", type=Path, default=CLAUDE_SETTINGS)
    parser.add_argument("--cursor-hooks", type=Path, default=CURSOR_HOOKS)
    parser.add_argument("--skip-claude", action="store_true")
    parser.add_argument("--skip-cursor", action="store_true")
    parser.add_argument(
        "--script",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "hooks" / "village-emit.sh",
        help="path to village-emit.sh",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path.home() / ".agent-village" / "config-backups",
    )
    args = parser.parse_args(argv)

    script = args.script.resolve()
    if not args.uninstall and not script.exists():
        print(f"error: hook script not found: {script}", file=sys.stderr)
        return 2
    enable = not args.uninstall

    targets = []
    if not args.skip_claude:
        targets.append(("Claude Code", args.claude_settings, plan_claude))
    if not args.skip_cursor:
        targets.append(("Cursor", args.cursor_hooks, plan_cursor))

    for label, path, planner in targets:
        try:
            current = _load(path)
        except json.JSONDecodeError as exc:
            print(f"error: {path} is not valid JSON ({exc}); refusing to touch it", file=sys.stderr)
            return 2
        merged = planner(current, script, enable=enable)
        if merged == current:
            print(f"{label}: already up to date ({path})")
            continue
        if args.dry_run:
            print(f"--- {label}: {path} (dry run)")
            print(json.dumps(merged, indent=2))
            continue
        backup = _backup(path, args.backup_dir)
        _write(path, merged)
        verb = "removed from" if args.uninstall else "installed into"
        print(f"{label}: {verb} {path}" + (f" (backup: {backup})" if backup else " (new file)"))

    if enable and not args.dry_run:
        print("\nHooks are observational only; no permission hook was touched.")
        print("Undo at any time with:  village-install-hooks --uninstall")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
