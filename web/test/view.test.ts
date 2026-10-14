/**
 * Framing the village: the whole of it in view, and none of it behind the bar along the bottom.
 */

import { describe, expect, it } from "vitest";

import { fitFrustum } from "../src/view";

const bounds = { halfW: 40, halfH: 25 };

describe("fitFrustum", () => {
  it("fits the village into the canvas when nothing covers it", () => {
    const f = fitFrustum(bounds, 1600, 1000, 0, 1);
    expect(f.top).toBe(-f.bottom);
    expect(f.right).toBe(-f.left);
    expect(f.right).toBeGreaterThanOrEqual(bounds.halfW);
    expect(f.top).toBeGreaterThanOrEqual(bounds.halfH);
  });

  it("keeps the whole village above the bar along the bottom", () => {
    const reserved = 120;
    const f = fitFrustum(bounds, 1600, 1000, reserved, 1);
    const worldPerPx = (f.top - f.bottom) / 1000;
    const visibleBottom = f.bottom + reserved * worldPerPx;
    expect(visibleBottom).toBeLessThanOrEqual(-bounds.halfH + 1e-9);
    expect(f.top).toBeGreaterThanOrEqual(bounds.halfH);
    expect(f.right).toBeGreaterThanOrEqual(bounds.halfW);
  });

  it("keeps pixels square, so the isometric view is not squashed", () => {
    const f = fitFrustum(bounds, 1600, 1000, 120, 1);
    expect((f.right - f.left) / 1600).toBeCloseTo((f.top - f.bottom) / 1000, 9);
  });

  it("zooms by scaling the whole frustum", () => {
    const near = fitFrustum(bounds, 1600, 1000, 120, 0.5);
    const far = fitFrustum(bounds, 1600, 1000, 120, 1);
    expect(near.top - near.bottom).toBeCloseTo((far.top - far.bottom) / 2, 9);
  });

  it("survives a canvas no taller than the bar", () => {
    const f = fitFrustum(bounds, 800, 100, 400, 1);
    expect(Number.isFinite(f.top) && Number.isFinite(f.bottom)).toBe(true);
    expect(f.top).toBeGreaterThan(f.bottom);
  });
});
