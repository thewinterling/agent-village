/**
 * The usage strip along the bottom.
 *
 * One bar per minute of token spend, oldest left, newest right. Two failure modes worth
 * guarding, both silent: too many bars for the width and the overflow clips the newest end,
 * so the strip quietly stops showing what just happened; and a single enormous spike on a
 * linear axis flattens every other bar to nothing, so the strip shows one spike and no shape.
 */

import { describe, expect, it } from "vitest";

import { ago, barHeights, hoverPosition, visibleBuckets } from "../src/hud";
import type { UsageBucket } from "../src/types";

/** As `/api/usage` returns them: oldest first, one per minute, quiet ones included. */
function series(count: number, tokens: (i: number) => number = () => 1000): UsageBucket[] {
  return Array.from({ length: count }, (_, i) => ({
    t: new Date(Date.UTC(2026, 9, 14, 9, i)).toISOString(),
    in: tokens(i),
    out: Math.round(tokens(i) / 10),
    sessions: tokens(i) ? 1 : 0,
    top: tokens(i) ? { sessionId: `sess-${i}`, name: `agent ${i}`, tokens: tokens(i) } : null,
  }));
}

describe("visibleBuckets", () => {
  it("draws oldest on the left and newest on the right", () => {
    expect(visibleBuckets(series(5), 800).map((b) => b.top!.sessionId)).toEqual([
      "sess-0",
      "sess-1",
      "sess-2",
      "sess-3",
      "sess-4",
    ]);
  });

  it("keeps the newest when there are more than fit", () => {
    const shown = visibleBuckets(series(400), 100); // 100px holds 14 bars
    expect(shown).toHaveLength(14);
    expect(shown.at(-1)!.top!.sessionId, "the most recent minute must survive").toBe("sess-399");
    expect(shown[0]!.top!.sessionId).toBe("sess-386");
  });

  it("scales with the available width", () => {
    expect(visibleBuckets(series(400), 1000).length).toBeGreaterThan(visibleBuckets(series(400), 300).length);
  });

  it("still shows something in a strip too narrow to measure", () => {
    // clientWidth is 0 until the panel has been laid out, and an empty bar looks broken.
    expect(visibleBuckets(series(400), 0)).toHaveLength(12);
  });

  it("copes with a village that has spent nothing yet", () => {
    expect(visibleBuckets([], 800)).toEqual([]);
  });

  it("does not disturb the caller's array", () => {
    const original = series(3);
    visibleBuckets(original, 800);
    expect(original.map((b) => b.top!.sessionId)).toEqual(["sess-0", "sess-1", "sess-2"]);
  });
});

describe("barHeights", () => {
  it("gives the busiest minute the full height", () => {
    expect(barHeights(series(3, (i) => (i === 1 ? 5000 : 100)), 26)[1]).toBe(26);
  });

  it("keeps a small spend visible next to a huge one", () => {
    // Linear scaling would put 1k against a 500k spike at 0.05px, which rounds to nothing.
    const [small] = barHeights(series(2, (i) => (i === 0 ? 1_000 : 500_000)), 26);
    expect(small).toBeGreaterThan(0);
    expect(small, "but it must still read as much smaller").toBeLessThan(13);
  });

  it("leaves an idle minute as a gap rather than a stub", () => {
    expect(barHeights(series(2, (i) => (i === 0 ? 0 : 9000)), 26)[0]).toBe(0);
  });

  it("draws nothing at all when the whole window is idle", () => {
    expect(barHeights(series(4, () => 0), 26)).toEqual([0, 0, 0, 0]);
  });

  it("survives an empty series", () => {
    expect(barHeights([], 26)).toEqual([]);
  });
});

describe("hoverPosition", () => {
  const view = { width: 1600, height: 900 };
  const card = { width: 330, height: 140 };

  it("sits below and right of the pointer when there is room", () => {
    expect(hoverPosition({ x: 400, y: 300 }, card, view)).toEqual({ left: 416, top: 316 });
  });

  it("flips above the pointer rather than over the strip it describes", () => {
    // The reported bug: hovering a bar on the bottom strip covered the bar.
    const { top } = hoverPosition({ x: 400, y: 870 }, card, view);
    expect(top + card.height, "the card must clear the pointer entirely").toBeLessThanOrEqual(870);
  });

  it("flips left of the pointer at the right-hand edge", () => {
    const { left } = hoverPosition({ x: 1560, y: 300 }, card, view);
    expect(left + card.width).toBeLessThanOrEqual(1560);
  });

  it("stays on screen in the corner where both flips happen", () => {
    const { left, top } = hoverPosition({ x: 1580, y: 880 }, card, view);
    expect(left).toBeGreaterThanOrEqual(12);
    expect(top).toBeGreaterThanOrEqual(12);
    expect(left + card.width).toBeLessThanOrEqual(view.width);
    expect(top + card.height).toBeLessThanOrEqual(view.height);
  });

  it("keeps a card too tall to flip on screen rather than off the top", () => {
    const tall = { width: 330, height: 880 };
    expect(hoverPosition({ x: 400, y: 860 }, tall, view).top).toBe(12);
  });
});

describe("ago", () => {
  const now = Date.UTC(2026, 9, 13, 12, 10, 0);

  it("reads as elapsed time, which is what you want from a history strip", () => {
    expect(ago(new Date(Date.UTC(2026, 9, 13, 12, 9, 30)).toISOString(), now)).toBe("30s ago");
    expect(ago(new Date(Date.UTC(2026, 9, 13, 12, 5, 0)).toISOString(), now)).toBe("5m 0s ago");
  });

  it("does not say '0s ago' for something happening right now", () => {
    expect(ago(new Date(now).toISOString(), now)).toBe("just now");
  });
});
