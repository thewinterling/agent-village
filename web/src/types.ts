export type AgentState =
  | "spawning"
  | "thinking"
  | "working"
  | "waiting"
  | "blocked"
  | "compacting"
  | "done"
  | "sleeping"
  | "gone";

/**
 * One row of `/api/history`: an agent changing state.
 *
 * Snake case because this endpoint hands back SQLite rows verbatim, unlike the agent payloads
 * which are built by hand in camel case.
 */
export interface Transition {
  ts: string;
  session_id: string;
  from_state: AgentState | null;
  to_state: AgentState;
  name: string | null;
  repo: string | null;
}

/** One slice of the usage strip: everything spent in `bucketS` seconds from `t`. */
export interface UsageBucket {
  t: string;
  in: number;
  out: number;
  sessions: number;
  top: { sessionId: string; name: string; tokens: number } | null;
}

export interface UsageSeries {
  bucketS: number;
  buckets: UsageBucket[];
}

export interface ToolCall {
  name: string;
  detail: string | null;
  startedAt: string;
  endedAt: string | null;
  ok: boolean | null;
  durationMs: number | null;
}

export interface Appearance {
  body: string;
  hair: string;
  hat: string;
  height: number;
  girth: number;
  gaitPhase: number;
  accessory: string;
}

export interface Agent {
  sessionId: string;
  source: "claude" | "cursor";
  state: AgentState;
  stateSince: string;
  stateAgeS: number;
  lastEventAt: string;
  spawnedAt: string;
  name: string;
  /** What this session is about, under the name. The AI title, or the gist of the prompt. */
  subtitle: string | null;
  title: string | null;
  cwd: string | null;
  repo: string | null;
  gitBranch: string | null;
  model: string | null;
  lastPrompt: string | null;
  lastAssistant: string | null;
  lastThought: string | null;
  permissionMode: string | null;
  pid: number | null;
  parentSessionId: string | null;
  agentType: string | null;
  isSubagent: boolean;
  isBackground: boolean;
  currentTool: string | null;
  openTools: ToolCall[];
  recentTools: ToolCall[];
  filesTouched: string[];
  prLinks: { url: string; number: number | null; repo: string | null }[];
  tokensIn: number;
  tokensOut: number;
  thinkingMs: number;
  turns: number;
  toolCount: number;
  waitingInferred: boolean;
  waitingReason: string | null;
  needsAttention: boolean;
  appearance: Appearance;
}

export interface WorldSnapshot {
  type: "world";
  updatedAt: string;
  now: string;
  agents: Agent[];
  counts: Record<string, number>;
  stats?: {
    sessions: number;
    toolCalls: number;
    prompts: number;
    turns: number;
    benchVisits: number;
    failures: number;
  };
}

/** One round trip in a session: what was asked, and how the agent said it went. */
export interface Turn {
  at: string | null;
  ask: string;
  /** The agent's closing line for that turn. Null while the turn is still running. */
  outcome: string | null;
  toolCalls: number;
}

export interface AgentDetail extends Agent {
  resumeCommand: string | null;
  /** False for Cursor agents, subagents, and any pid that no longer belongs to an agent. */
  canStop: boolean;
  stopBlockedReason: string | null;
  /** False for anything without a terminal to type into: Cursor agents, subagents, dead pids. */
  canPrompt: boolean;
  promptBlockedReason: string | null;
  narrative: Turn[];
  timeline: { ts: string; kind: string; tool_name: string | null; detail: string | null }[];
}
