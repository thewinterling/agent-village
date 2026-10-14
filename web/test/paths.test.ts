/**
 * The road network.
 *
 * The bug this exists to prevent: an avatar took the straight line to its new slot and walked
 * through the cafe railing on its way to the workshop. So the tests that matter are "every
 * state change has a route" and "no leg of a route crosses a fence".
 */

import { describe, expect, it } from "vitest";

import {
  advanceRoute,
  ALLEY_Z,
  doorsOf,
  GATE_W,
  GATES,
  LINKS,
  NODES,
  PATH_W,
  routeTo,
  SPINE_Z,
  zoneAt,
} from "../src/paths";
import { BENCH_SEATS, PARK_PATH_HALF_W, slotPosition, ZONES, type Vec2, type ZoneId } from "../src/zones";

const ZONE_IDS = Object.keys(ZONES) as ZoneId[];

/** The edges an avatar is not allowed to cross, as segments in the ground plane. */
interface Fence {
  zone: ZoneId;
  axis: "x" | "z";
  /** Position on `axis`. */
  at: number;
  /** Span on the other axis. */
  from: number;
  to: number;
}

/**
 * Every wall and railing in `world.ts`, and the gap cut in it where a gate passes through.
 *
 * Duplicated from the scene builder on purpose: if the two drift apart that is exactly the
 * bug, and a test that imports the same constant cannot see it.
 */
const FENCES: Fence[] = [
  // Workshop: walls on the far and left edges.
  { zone: "workshop", axis: "z", at: -14.4, from: -25.2, to: -6 },
  { zone: "workshop", axis: "x", at: -25.2, from: -14.4, to: 2.4 },
  // Cafe: railings on the far and left edges. The left one is what agents used to walk over.
  { zone: "cafe", axis: "z", at: -15, from: 5.4, to: 28.2 },
  { zone: "cafe", axis: "x", at: 5.4, from: -15, to: 3 },
  // Library and infirmary: left wall solid, back wall has the doorway in it.
  { zone: "library", axis: "x", at: -22.8, from: 9.6, to: 18 },
  { zone: "library", axis: "z", at: 9.6, from: -22.8, to: -10.8 },
  { zone: "infirmary", axis: "x", at: 10.8, from: 9.6, to: 18 },
  { zone: "infirmary", axis: "z", at: 9.6, from: 10.8, to: 22.8 },
  { zone: "hotel", axis: "x", at: 24.6, from: 9, to: 27 },
  { zone: "hotel", axis: "z", at: 9, from: 24.6, to: 42.6 },
];

/** Where a fence is broken by one of the zone's doorways, so a crossing there is legal. */
function isGateway(fence: Fence, x: number, z: number): boolean {
  return doorsOf(fence.zone).some((id) => {
    const door = NODES[id]!;
    const along = fence.axis === "x" ? z : x;
    const doorAlong = fence.axis === "x" ? door.z : door.x;
    const onDoorEdge = Math.abs((fence.axis === "x" ? x : z) - (fence.axis === "x" ? door.x : door.z)) < 1e-6;
    return onDoorEdge && Math.abs(along - doorAlong) < GATE_W / 2;
  });
}

/** Does the segment a->b cross any fence other than through its gateway? */
function crossesAFence(a: Vec2, b: Vec2): Fence | null {
  for (const fence of FENCES) {
    const [pa, pb] = fence.axis === "x" ? [a.x, b.x] : [a.z, b.z];
    const [qa, qb] = fence.axis === "x" ? [a.z, b.z] : [a.x, b.x];
    if ((pa - fence.at) * (pb - fence.at) >= 0) continue; // never reaches the fence line

    const t = (fence.at - pa) / (pb - pa);
    const q = qa + (qb - qa) * t;
    if (q < fence.from || q > fence.to) continue; // passes beyond the end of it

    const hit = fence.axis === "x" ? { x: fence.at, z: q } : { x: q, z: fence.at };
    if (!isGateway(fence, hit.x, hit.z)) return fence;
  }
  return null;
}

/** A full walk: start, every waypoint, then the slot. */
function legs(from: Vec2, to: ZoneId): [Vec2, Vec2][] {
  const points = [from, ...routeTo(from, to), slotPosition(to, 0, 1)];
  return points.slice(0, -1).map((p, i) => [p, points[i + 1]!]);
}

describe("the network reaches everywhere", () => {
  it("connects every zone to every other", () => {
    for (const from of ZONE_IDS) {
      for (const to of ZONE_IDS) {
        if (from === to) continue;
        const route = routeTo(GATES[from], to);
        expect(route.length, `no route from ${from} to ${to}`).toBeGreaterThan(0);
        const doors = doorsOf(to).map((id) => NODES[id]!);
        expect(doors, `${from} -> ${to} ends somewhere that is not a door`).toContainEqual(route.at(-1));
      }
    }
  });

  it("walks from the cafe to the workshop without climbing the railing", () => {
    // The reported bug, verbatim.
    const seat = slotPosition("cafe", 0, 4);
    for (const [a, b] of legs(seat, "workshop")) {
      expect(crossesAFence(a, b), `leg ${JSON.stringify(a)} -> ${JSON.stringify(b)}`).toBeNull();
    }
  });

  it("never crosses a fence on any state change", () => {
    for (const from of ZONE_IDS) {
      for (const to of ZONE_IDS) {
        if (from === to) continue;
        const start = slotPosition(from, 0, 1);
        for (const [a, b] of legs(start, to)) {
          expect(crossesAFence(a, b), `${from} -> ${to}: leg crosses a fence`).toBeNull();
        }
      }
    }
  });

  it("walks a new arrival in from outside the village", () => {
    const route = routeTo({ x: 0, z: 19 }, "workshop");
    expect(route[0]).toEqual(GATES.door);
    expect(doorsOf("workshop").map((id) => NODES[id]!)).toContainEqual(route.at(-1));
  });

  it("stays put when the agent is already in the target zone", () => {
    expect(routeTo(ZONES.cafe.centre, "cafe")).toEqual([]);
  });
});

describe("routes are sensible, not just legal", () => {
  it("does not double back when a park walker is sent out of the village", () => {
    // Halfway down the park walkway, heading for the door. Entering the network at the north
    // gate would mean walking away from the exit first.
    const route = routeTo({ x: 0, z: 11 }, "door");
    expect(route[0]).toEqual(NODES["park:south"]);
  });

  it("takes the short way round the spine", () => {
    const toLibrary = routeTo(GATES.workshop, "library");
    const toCafe = routeTo(GATES.workshop, "cafe");
    expect(toLibrary.length).toBeLessThan(toCafe.length);
  });
});

describe("the alley between the workshop and the cafe", () => {
  /** Ground covered by a walk through these points, which is what a shortcut has to cut. */
  const lengthOf = (points: Vec2[]): number =>
    points.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - points[i]!.x, p.z - points[i]!.z), 0);

  it("sends an agent finishing its turn straight across the gap", () => {
    // Working -> done, the commonest move there is. Round the spine it is nearly the whole
    // length of the village for a trip you can see across.
    const desk = slotPosition("workshop", 0, 1);
    expect(routeTo(desk, "cafe")).toEqual([
      NODES["side:workshop"],
      NODES["alley:centre"],
      NODES["side:cafe"],
    ]);
  });

  it("takes about 40% off the walk it replaces", () => {
    const desk = slotPosition("workshop", 0, 1);
    const seat = slotPosition("cafe", 0, 1);
    const viaAlley = lengthOf([desk, ...routeTo(desk, "cafe"), seat]);
    const viaSpine = lengthOf([
      desk,
      GATES.workshop,
      NODES["spine:workshop"]!,
      NODES["spine:centre"]!,
      NODES["spine:infirmary"]!,
      NODES["spine:cafe"]!,
      GATES.cafe,
      seat,
    ]);

    expect(viaAlley).toBeLessThan(viaSpine * 0.65);
  });

  it("still routes the long way round when the alley does not help", () => {
    // A done agent that gets asked a question goes to the park, which the spine serves.
    const seat = slotPosition("cafe", 0, 1);
    expect(routeTo(seat, "park").at(-1)).toEqual(GATES.park);
  });

  it("opens both doors onto the strip of grass the alley runs down", () => {
    expect(NODES["side:workshop"]).toEqual({ x: ZONES.workshop.centre.x + ZONES.workshop.half.x, z: ALLEY_Z });
    expect(NODES["side:cafe"]).toEqual({ x: ZONES.cafe.centre.x - ZONES.cafe.half.x, z: ALLEY_Z });
  });

  it("keeps the alley clear of the desks and the cafe tables", () => {
    // It threads between two furnished rooms, so a doorway at the wrong depth opens onto a
    // chair. Checked against the real furniture rather than a remembered number.
    for (let i = 0; i < 15; i++) {
      const desk = slotPosition("workshop", i, 15);
      expect(Math.hypot(desk.x - NODES["side:workshop"]!.x, desk.z - NODES["side:workshop"]!.z)).toBeGreaterThan(1.4);
    }
    for (let i = 0; i < 24; i++) {
      const chair = slotPosition("cafe", i, 24);
      expect(Math.hypot(chair.x - NODES["side:cafe"]!.x, chair.z - NODES["side:cafe"]!.z)).toBeGreaterThan(1.4);
    }
  });

  it("is reachable from the spine, so a walker caught halfway along it has somewhere to go", () => {
    // An island would leave routeTo with nothing to return, and the avatar would abandon the
    // network and strike out across the grass - through the infirmary wall, as it happens.
    const midAlley = NODES["alley:centre"]!;
    for (const to of ZONE_IDS) {
      expect(routeTo(midAlley, to).length, `stranded in the alley on the way to ${to}`).toBeGreaterThan(0);
    }
  });
});

/**
 * A wall along a zone's north edge, which is the only kind that can stand between the camera
 * and the road.
 *
 * The camera looks down the (-1, -1) diagonal, so a wall hides the ground to the north of it
 * and nothing else. The side walls all run north-south along a zone's west edge and shadow
 * only the grass further west, which no road reaches.
 */
interface BackWall {
  zone: ZoneId;
  z: number;
  from: number;
  to: number;
  height: number;
  /** The doorway cut through it, which you can see straight through. */
  gap?: [number, number];
}

const BACK_WALLS: BackWall[] = Object.values(ZONES)
  .filter((zone) => zone.backWall !== undefined)
  .map((zone) => {
    const north = zone.centre.z - zone.half.z;
    const gated = GATES[zone.id].z === north;
    return {
      zone: zone.id,
      z: north,
      from: zone.centre.x - zone.half.x,
      to: zone.centre.x + zone.half.x,
      height: zone.backWall!,
      ...(gated ? { gap: [GATES[zone.id].x - GATE_W / 2, GATES[zone.id].x + GATE_W / 2] as [number, number] } : {}),
    };
  });

/**
 * Which wall, if any, this patch of ground is behind.
 *
 * Isometric at 45 degrees from each axis: the ground at `(x, z)` is covered by whatever stands
 * at `(x + d, z + d)` and is taller than `d`. One unit of height climbs the screen exactly as
 * fast as one unit along that diagonal, which is why a 3-unit wall swallows everything within
 * 3 units behind it.
 */
function hiddenBy(p: Vec2): BackWall | null {
  for (const wall of BACK_WALLS) {
    const d = wall.z - p.z;
    if (d <= 0 || d >= wall.height) continue; // in front of it, or low enough to see over
    const x = p.x + d; // the bit of wall directly in the way
    if (x < wall.from || x > wall.to) continue;
    if (wall.gap && x > wall.gap[0] && x < wall.gap[1]) continue; // seen through the doorway
    return wall;
  }
  return null;
}

describe("the camera can see the whole road", () => {
  /** Centre line and both kerbs, since an edge goes behind a wall before the middle does. */
  function* surface(link: (typeof LINKS)[number]): Generator<Vec2> {
    const a = NODES[link.from]!;
    const b = NODES[link.to]!;
    const len = Math.hypot(b.x - a.x, b.z - a.z);
    const half = (link.width ?? PATH_W) / 2;
    const nx = -(b.z - a.z) / len;
    const nz = (b.x - a.x) / len;
    for (let step = 0; step <= 40; step++) {
      const t = step / 40;
      for (const off of [-half, 0, half]) {
        yield { x: a.x + (b.x - a.x) * t + nx * off, z: a.z + (b.z - a.z) * t + nz * off };
      }
    }
  }

  it("leaves no stretch of paving behind a wall", () => {
    // The reported bug: the workshop-to-cafe road vanished behind the infirmary's back wall,
    // so an avatar crossing the village dropped out of sight halfway.
    for (const link of LINKS) {
      for (const p of surface(link)) {
        const wall = hiddenBy(p);
        expect(wall?.zone ?? null, `${link.from} -> ${link.to} is behind the ${wall?.zone} wall`).toBeNull();
      }
    }
  });

  it("lines the walled zones' stubs up with their own doorway", () => {
    // They approach across the wall that hides them, so the only way in is straight through
    // the gap. A stub that is merely near the door would be swallowed by the wall beside it.
    for (const id of ["library", "infirmary", "hotel"] as const) {
      const stub = NODES[`spine:${id}`]!;
      const door = GATES[id];
      expect(Math.abs(door.x - stub.x), `${id} stub is not on the camera diagonal`).toBeCloseTo(door.z - stub.z);
    }
  });
});

describe("the paving matches the ground it is laid on", () => {
  it("runs the spine clear of every zone floor", () => {
    const half = PATH_W / 2;
    for (const zone of Object.values(ZONES)) {
      if (zone.id === "park" || zone.id === "door") continue; // the walkway crosses these
      const north = zone.centre.z - zone.half.z;
      const south = zone.centre.z + zone.half.z;
      const overlaps = SPINE_Z + half > north && SPINE_Z - half < south;
      expect(overlaps, `spine runs through the ${zone.id} floor`).toBe(false);
    }
  });

  it("only links nodes that exist", () => {
    for (const { from, to } of LINKS) {
      expect(NODES[from], `unknown node ${from}`).toBeDefined();
      expect(NODES[to], `unknown node ${to}`).toBeDefined();
    }
  });

  it("puts every gate on its own zone boundary", () => {
    for (const id of ZONE_IDS) {
      const zone = ZONES[id];
      const gate = GATES[id];
      const onEdge =
        Math.abs(Math.abs(gate.z - zone.centre.z) - zone.half.z) < 1e-6 ||
        Math.abs(Math.abs(gate.x - zone.centre.x) - zone.half.x) < 1e-6;
      expect(onEdge, `${id} gate is not on its boundary`).toBe(true);
    }
  });
});

describe("the park benches leave the walkway clear", () => {
  it("seats nobody on the path", () => {
    for (let i = 0; i < BENCH_SEATS * 2; i++) {
      const seat = slotPosition("park", i, BENCH_SEATS * 2);
      expect(Math.abs(seat.x), `seat ${i} is on the walkway`).toBeGreaterThan(PARK_PATH_HALF_W);
    }
  });

  it("keeps every seat inside the park", () => {
    for (let i = 0; i < BENCH_SEATS * 2; i++) {
      const seat = slotPosition("park", i, BENCH_SEATS * 2);
      expect(Math.abs(seat.x - ZONES.park.centre.x)).toBeLessThan(ZONES.park.half.x);
      expect(Math.abs(seat.z - ZONES.park.centre.z)).toBeLessThan(ZONES.park.half.z);
    }
  });
});

describe("following a route", () => {
  // A gate and its junction really are this close together - see `gate:library`.
  const route = () => [
    { x: 0, z: 0 },
    { x: 0, z: 0.5 },
    { x: 0, z: 10 },
  ];

  it("keeps waypoints still ahead", () => {
    expect(advanceRoute({ x: 0, z: -5 }, route())).toHaveLength(3);
  });

  it("drops one that has been reached", () => {
    expect(advanceRoute({ x: 0, z: -0.2 }, route())).toEqual([
      { x: 0, z: 0.5 },
      { x: 0, z: 10 },
    ]);
  });

  it("drops two at once when a single frame carries the walker past both", () => {
    expect(advanceRoute({ x: 0, z: 0.3 }, route())).toEqual([{ x: 0, z: 10 }]);
  });

  it("walks a whole route to its end without turning back", () => {
    // The route the reported bug took: a cafe seat to a workshop desk.
    const start = slotPosition("cafe", 0, 4);
    let pos: Vec2 = { x: start.x, z: start.z };
    let remaining = routeTo(pos, "workshop");
    const target = slotPosition("workshop", 0, 1);
    const walked: Vec2[] = [pos];

    for (let frame = 0; frame < 2000; frame++) {
      remaining = advanceRoute(pos, remaining);
      const goal = remaining[0] ?? target;
      const d = Math.hypot(goal.x - pos.x, goal.z - pos.z);
      if (remaining.length === 0 && d < 0.12) break;
      const step = Math.min(d, 6.5 / 60);
      pos = { x: pos.x + ((goal.x - pos.x) / d) * step, z: pos.z + ((goal.z - pos.z) / d) * step };
      walked.push(pos);
    }

    expect(remaining, "never finished the route").toHaveLength(0);
    expect(Math.hypot(pos.x - target.x, pos.z - target.z)).toBeLessThan(0.2);

    // The actual complaint: the walk must not clip a railing at any point along the way.
    for (let i = 1; i < walked.length; i++) {
      expect(crossesAFence(walked[i - 1]!, walked[i]!), `frame ${i} crossed a fence`).toBeNull();
    }
  });
});

describe("zoneAt", () => {
  it("places a point inside a zone", () => {
    expect(zoneAt(ZONES.cafe.centre)).toBe("cafe");
  });

  it("treats gates and the spine as outside, so they count as network", () => {
    expect(zoneAt(GATES.library)).toBeNull();
    expect(zoneAt({ x: 0, z: SPINE_Z })).toBeNull();
  });
});
