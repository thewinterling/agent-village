#!/bin/bash
# Agent Village event emitter.
#
#   village-emit.sh <claude|cursor> <EventName>
#
# Reads a hook payload on stdin and drops one JSON file into the spool directory. Prints "{}"
# and always exits 0, because this script runs inside a live agent loop and must never be the
# reason a real session stalls or a tool call gets blocked.
#
# Why a spool *directory* rather than appending to one JSONL file: some payloads carry full
# terminal output or file contents and blow past PIPE_BUF, so concurrent appends from several
# agents could interleave mid-line. One file per event, published by an atomic rename, has no
# size limit and needs no locking.

set -u

SPOOL_DIR="${AGENT_VILLAGE_SPOOL:-$HOME/.agent-village/spool}"
MAX_BYTES="${AGENT_VILLAGE_MAX_BYTES:-262144}"
# Process names that count as "the agent this hook belongs to" when walking up the tree.
AGENT_COMMS="${AGENT_VILLAGE_AGENT_COMMS:-claude cursor cursor-agent}"

src="${1:-unknown}"
event="${2:-unknown}"

# Hand control back to the agent no matter what happens below. Clearing the trap first keeps a
# deliberate exit from re-entering this function and printing a second response.
#
# "continue": true is spelled out rather than left to the default. Both Claude and Cursor read
# "continue": false as "stop the agent", and beforeSubmitPrompt sits on the user's own prompt
# path - an observer has no business being ambiguous there.
emit_ok() {
	trap - EXIT
	echo '{"continue": true}'
	exit 0
}
trap emit_ok EXIT

payload=$(cat 2>/dev/null)
[ -n "$payload" ] || payload='{}'

# Hook JSON is single-line in practice, and any newline inside a JSON string is escaped as \n,
# so raw newlines are only formatting. Strip them to keep one event on one line.
payload=${payload//$'\n'/}
payload=${payload//$'\r'/}

# A runaway payload (an afterShellExecution carrying megabytes of build log) is not worth
# spooling. Keep the event, drop the body: the daemon only ever reads identity and metadata.
if [ "${#payload}" -gt "$MAX_BYTES" ]; then
	payload='{"_truncated":true}'
fi

# Cursor may not pass arguments through to the hook command, so fall back to the event name the
# payload carries. Pure bash regex, no jq fork.
if [ "$event" = "unknown" ] && [[ "$payload" =~ \"hook_event_name\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]]; then
	event="${BASH_REMATCH[1]}"
fi

# Walk up the process tree to find the agent process this hook was spawned from, so the reaper
# can tell a closed terminal from a finished session. Pure bash, capped depth, no forks.
#
# For a Claude CLI session this lands on that session's own `claude` process. Under Cursor it
# lands on the Cursor application, which is shared by every conversation in the window - still
# useful as a liveness signal, but not a per-conversation identity. The daemon treats it that
# way: see `reaper.ReaperConfig`.
agent_pid=0
_pid=$PPID
_depth=0
while [ "$_pid" -gt 1 ] && [ "$_depth" -lt 8 ]; do
	[ -r "/proc/$_pid/comm" ] || break
	read -r _comm <"/proc/$_pid/comm" 2>/dev/null || break
	for _want in $AGENT_COMMS; do
		if [ "$_comm" = "$_want" ]; then
			agent_pid=$_pid
			break 2
		fi
	done
	# Field 4 of /proc/<pid>/stat is the parent pid, but field 2 is the command name in
	# parentheses and may itself contain spaces, so cut everything up to the last ") ".
	read -r _stat <"/proc/$_pid/stat" 2>/dev/null || break
	_rest=${_stat##*) }
	# shellcheck disable=SC2086
	set -- $_rest
	_pid=${2:-0}
	_depth=$((_depth + 1))
done

mkdir -p "$SPOOL_DIR" 2>/dev/null || emit_ok

now=${EPOCHREALTIME:-$SECONDS}
now=${now/,/.} # some locales render the fraction with a comma

name="${now}-${RANDOM}"
tmp="$SPOOL_DIR/.wip-$$-$name"
printf '{"v":1,"src":"%s","ev":"%s","ts":%s,"pid":%s,"p":%s}\n' \
	"$src" "$event" "$now" "$agent_pid" "$payload" >"$tmp" 2>/dev/null || emit_ok

# Rename is atomic, so the daemon never sees a half-written event.
mv -f "$tmp" "$SPOOL_DIR/$name.json" 2>/dev/null || rm -f "$tmp" 2>/dev/null

emit_ok
