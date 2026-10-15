/** The list view's order: what needs you at the top, the quiet ones at the bottom. */

import { describe, expect, it } from "vitest";

import { listOrder } from "../src/list";
import type { Agent, AgentState } from "../src/types";

const agent = (sessionId: string, state: AgentState, stateAgeS = 1): Agent =>
  ({ sessionId, state, stateAgeS }) as Agent;

describe("listOrder", () => {
  it("puts the states that need attention first", () => {
    const ids = listOrder([
      agent("s", "sleeping"),
      agent("w", "working"),
      agent("b", "blocked"),
      agent("q", "waiting"),
    ]).map((a) => a.sessionId);
    expect(ids).toEqual(["q", "b", "w", "s"]);
  });

  it("sorts states the counters do not list to the end", () => {
    const ids = listOrder([agent("g", "gone"), agent("d", "done")]).map((a) => a.sessionId);
    expect(ids).toEqual(["d", "g"]);
  });

  it("puts whoever has waited longest first within a state", () => {
    const ids = listOrder([agent("new", "waiting", 5), agent("old", "waiting", 500)]).map((a) => a.sessionId);
    expect(ids).toEqual(["old", "new"]);
  });
});
