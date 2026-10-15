/**
 * Placement rules.
 *
 * Rendering is not tested here - three.js is somebody else's problem. What is tested is the
 * pure geometry, because "two avatars standing inside each other" and "the bench is off the
 * edge of the park" are the bugs that actually happen.
 */

import { describe, expect, it } from "vitest";

import type { Agent, AgentState } from "../src/types";
import {
  BENCH_SEATS,
  HOTEL_BEDS,
  STATE_COLOUR,
  WORKSHOP_DESKS,
  ZONES,
  type ZoneId,
  bedPosition,
  deskPosition,
  layout,
  signAnchor,
  slotPosition,
  statesIn,
  toScreen,
  villageBounds,
  zoneFor,
} from "../src/zones";

function agent(overrides: Partial<Agent> & { sessionId: string }): Agent {
  return {
    source: "claude",
    state: "working",
    stateSince: "2026-10-13T08:00:00Z",
    stateAgeS: 1,
    lastEventAt: "2026-10-13T08:00:00Z",
    spawnedAt: "2026-10-13T08:00:00Z",
    name: overrides.sessionId,
    subtitle: null,
    title: null,
    cwd: null,
    repo: null,
    gitBranch: null,
    model: null,
    lastPrompt: null,
    lastAssistant: null,
    lastThought: null,
    permissionMode: null,
    pid: null,
    parentSessionId: null,
    agentType: null,
    isSubagent: false,
    isBackground: false,
    currentTool: null,
    openTools: [],
    recentTools: [],
    filesTouched: [],
    prLinks: [],
    tokensIn: 0,
    tokensOut: 0,
    thinkingMs: 0,
    turns: 0,
    toolCount: 0,
    waitingInferred: false,
    waitingReason: null,
    needsAttention: false,
    appearance: {
      body: "#fff", hair: "#000", hat: "none", height: 1, girth: 1, gaitPhase: 0, accessory: "none",
      skin: "#eec4a2", hairStyle: "short", trousers: "#37405a",
    },
    ...overrides,
  };
}

describe("zoneFor", () => {
  const cases: [AgentState, string][] = [
    ["working", "workshop"],
    ["thinking", "workshop"],
    ["waiting", "park"],
    ["blocked", "infirmary"],
    ["compacting", "library"],
    ["done", "cafe"],
    ["spawning", "door"],
    ["gone", "door"],
  ];
  it.each(cases)("sends %s to the %s", (state, zone) => {
    expect(zoneFor({ state })).toBe(zone);
  });

  it("covers every state", () => {
    const states: AgentState[] = [
      "spawning", "thinking", "working", "waiting", "blocked", "compacting", "done", "gone",
    ];
    for (const state of states) expect(ZONES[zoneFor({ state })]).toBeDefined();
  });
});

describe("zone info", () => {
  it("explains every zone", () => {
    for (const zone of Object.values(ZONES)) {
      expect(zone.info.length, `${zone.id} needs an explanation`).toBeGreaterThan(30);
    }
  });

  it("lists the states that lead to each zone", () => {
    expect(statesIn("workshop").sort()).toEqual(["thinking", "working"]);
    expect(statesIn("park")).toEqual(["waiting"]);
    expect(statesIn("hotel")).toEqual(["sleeping"]);
    expect(statesIn("door").sort()).toEqual(["gone", "spawning"]);
  });

  it("accounts for every state exactly once across the zones", () => {
    const listed = Object.keys(ZONES).flatMap((id) => statesIn(id as ZoneId));
    expect(listed.sort()).toEqual(
      ["blocked", "compacting", "done", "gone", "sleeping", "spawning", "thinking", "waiting", "working"],
    );
  });
});

describe("the hotel", () => {
  /**
   * The hotel holds a working day rather than a moment, so it is the one zone that has to
   * cope with everything that finished since this morning being in it at once.
   */
  it("sleeps at least as many as the workshop can put to work", () => {
    expect(HOTEL_BEDS).toBeGreaterThanOrEqual(WORKSHOP_DESKS);
  });

  it("has a bed for a busy day's worth of finished sessions", () => {
    // Fifteen sleepers by mid-morning was a normal day, and that filled every bed.
    expect(HOTEL_BEDS).toBeGreaterThanOrEqual(24);
  });

  it("puts every bed inside the room with space to walk round it", () => {
    const zone = ZONES.hotel;
    for (let i = 0; i < HOTEL_BEDS; i++) {
      const bed = bedPosition(zone, i);
      // A bed is 1.2 across and 2.2 long, and the headboard goes against the back wall.
      expect(Math.abs(bed.x - zone.centre.x) + 0.6).toBeLessThan(zone.half.x);
      expect(bed.z - 1.2).toBeGreaterThan(zone.centre.z - zone.half.z);
      expect(bed.z + 1.2).toBeLessThan(zone.centre.z + zone.half.z);
    }
  });

  it("sits each sleeper on their own bed, not beside it", () => {
    for (let i = 0; i < HOTEL_BEDS; i++) {
      const bed = bedPosition(ZONES.hotel, i);
      const slot = slotPosition("hotel", i, HOTEL_BEDS);
      expect(slot.x).toBe(bed.x);
      expect(Math.abs(slot.z - bed.z), `sleeper ${i} is off the mattress`).toBeLessThan(0.9);
      expect(slot.seated).toBe(true);
    }
  });

  it("stands the overflow on clear floor rather than on the beds", () => {
    const beds = Array.from({ length: HOTEL_BEDS }, (_, i) => bedPosition(ZONES.hotel, i));
    for (let i = HOTEL_BEDS; i < HOTEL_BEDS + 8; i++) {
      const slot = slotPosition("hotel", i, HOTEL_BEDS + 8);
      const zone = ZONES.hotel;
      expect(Math.abs(slot.x - zone.centre.x), `overflow ${i} is outside the hotel`).toBeLessThan(zone.half.x);
      expect(Math.abs(slot.z - zone.centre.z), `overflow ${i} is outside the hotel`).toBeLessThan(zone.half.z);
      for (const bed of beds) {
        expect(Math.hypot(slot.x - bed.x, slot.z - bed.z), `overflow ${i} is standing on a bed`)
          .toBeGreaterThan(1.3);
      }
    }
  });

  it("keeps clear of the infirmary next door", () => {
    const gap = ZONES.hotel.centre.x - ZONES.hotel.half.x - (ZONES.infirmary.centre.x + ZONES.infirmary.half.x);
    expect(gap, "the hotel and the infirmary share a wall").toBeGreaterThan(1);
  });
});

describe("villageBounds", () => {
  it("covers every zone with grass to spare", () => {
    // The island used to be a hardcoded slab. Adding the hotel left its far corner hanging
    // over the edge of the world.
    const { centre, half } = villageBounds();
    for (const zone of Object.values(ZONES)) {
      for (const axis of ["x", "z"] as const) {
        const overhang = Math.abs(zone.centre[axis] - centre[axis]) + zone.half[axis];
        expect(overhang, `the ${zone.id} hangs off the island on ${axis}`).toBeLessThan(half[axis]);
      }
    }
  });

  it("leaves the margin it is asked for", () => {
    expect(villageBounds(10).half.x - villageBounds(0).half.x).toBe(10);
  });
});

describe("signAnchor", () => {
  /**
   * Where a 3D point lands on the isometric camera. Height matters as much as position here -
   * a sign sitting over its own floor plan can still *appear* over the zone behind it.
   */
  const project = (x: number, y: number, z: number) => ({
    u: (x - z) / Math.SQRT2,
    v: (-x + 2 * y - z) / Math.sqrt(6),
  });

  /** The zone's floor, as the quadrilateral it projects to. */
  const floorOnScreen = (zone: (typeof ZONES)[ZoneId]) =>
    [
      [-1, -1],
      [1, -1],
      [1, 1],
      [-1, 1],
    ].map(([sx, sz]) =>
      project(zone.centre.x + sx! * zone.half.x, 0, zone.centre.z + sz! * zone.half.z),
    );

  function inside(p: { u: number; v: number }, poly: { u: number; v: number }[]): boolean {
    let sign = 0;
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i]!;
      const b = poly[(i + 1) % poly.length]!;
      const cross = (b.u - a.u) * (p.v - a.v) - (b.v - a.v) * (p.u - a.u);
      if (cross === 0) continue;
      if (sign === 0) sign = Math.sign(cross);
      else if (Math.sign(cross) !== sign) return false;
    }
    return true;
  }

  it("shows every name over the zone it names, not over the one behind it", () => {
    for (const zone of Object.values(ZONES)) {
      const sign = signAnchor(zone);
      const at = project(sign.x, sign.y, sign.z);
      expect(inside(at, floorOnScreen(zone)), `${zone.label} appears off its own floor`).toBe(true);
    }
  });

  it("stands the sign up off the ground rather than lying it flat", () => {
    expect(signAnchor(ZONES.workshop).y).toBeGreaterThan(1);
  });

  it("caps the height on a shallow zone so the name cannot drift off the back", () => {
    // The front door is two units deep; at the full height its name lands in the park.
    expect(signAnchor(ZONES.door).y).toBeLessThan(signAnchor(ZONES.park).y);
  });
});

describe("the zones that matter are front and centre", () => {
  it("puts the park nearest the camera and horizontally centred", () => {
    expect(ZONES.park.centre.x).toBe(0);
    expect(ZONES.park.centre.z).toBeGreaterThan(ZONES.workshop.centre.z);
    expect(ZONES.park.centre.z).toBeGreaterThan(ZONES.cafe.centre.z);
  });

  it("does not hide the infirmary at the back", () => {
    expect(ZONES.infirmary.centre.z).toBeGreaterThan(ZONES.workshop.centre.z);
  });

  it("keeps the zones from overlapping", () => {
    const zones = Object.values(ZONES);
    for (let i = 0; i < zones.length; i++) {
      for (let j = i + 1; j < zones.length; j++) {
        const a = zones[i]!;
        const b = zones[j]!;
        const apart =
          Math.abs(a.centre.x - b.centre.x) >= a.half.x + b.half.x ||
          Math.abs(a.centre.z - b.centre.z) >= a.half.z + b.half.z;
        expect(apart, `${a.id} overlaps ${b.id}`).toBe(true);
      }
    }
  });
});

describe("slotPosition", () => {
  it("is deterministic, so avatars stand still instead of jittering", () => {
    expect(slotPosition("workshop", 2, 5)).toEqual(slotPosition("workshop", 2, 5));
  });

  it("keeps everyone inside their zone", () => {
    for (const zone of Object.values(ZONES)) {
      if (zone.arrangement === "point") continue;
      for (let i = 0; i < 12; i++) {
        const p = slotPosition(zone.id, i, 12);
        expect(Math.abs(p.x - zone.centre.x)).toBeLessThanOrEqual(zone.half.x + 2.2);
        expect(Math.abs(p.z - zone.centre.z)).toBeLessThanOrEqual(zone.half.z + 2.2);
      }
    }
  });

  it("gives every bench sitter their own seat", () => {
    const seats = Array.from({ length: BENCH_SEATS }, (_, i) => slotPosition("park", i, BENCH_SEATS));
    const xs = new Set(seats.map((s) => s.x.toFixed(3)));
    expect(xs.size).toBe(BENCH_SEATS);
  });

  it("starts a second row once the benches are full", () => {
    const first = slotPosition("park", 0, BENCH_SEATS + 1);
    const overflow = slotPosition("park", BENCH_SEATS, BENCH_SEATS + 1);
    expect(overflow.z).toBeGreaterThan(first.z);
  });

  /**
   * An avatar is about 0.8 units wide, so anything closer than that is a visible pile. Checked
   * at realistic occupancy, not just two or three: the cafe stacked badly at 12.
   */
  it.each([2, 4, 7, 12, 20])("keeps %i avatars in a zone apart in world space", (n) => {
    forEachSeatPair(n, (zone, i, j, a, b) => {
      const d = Math.hypot(a.x - b.x, a.z - b.z);
      expect(d, `${zone}: seats ${i} and ${j} are ${d.toFixed(2)} apart`).toBeGreaterThan(1.2);
    });
  });

  /**
   * The one that actually matters, and the one world-space distance misses entirely.
   *
   * Under an isometric camera a pair of avatars offset by (+d, +d) sits on the view axis and
   * projects to the same pixel however far apart they really are. The cafe's 45-degree seating
   * did exactly that: four distinct seats rendered as two.
   */
  it.each([2, 4, 7, 12, 20])("keeps %i avatars from overlapping on screen", (n) => {
    forEachSeatPair(n, (zone, i, j, a, b) => {
      const sa = toScreen(a);
      const sb = toScreen(b);
      const d = Math.hypot(sa.x - sb.x, sa.z - sb.z);
      expect(d, `${zone}: seats ${i} and ${j} overlap on screen (${d.toFixed(2)})`).toBeGreaterThan(0.85);
    });
  });
});

function forEachSeatPair(
  n: number,
  check: (zone: string, i: number, j: number, a: { x: number; z: number }, b: { x: number; z: number }) => void,
): void {
  for (const zone of Object.values(ZONES)) {
    if (zone.arrangement === "point") continue;
    const seats = Array.from({ length: n }, (_, i) => slotPosition(zone.id, i, n));
    for (let i = 0; i < seats.length; i++) {
      for (let j = i + 1; j < seats.length; j++) check(zone.id, i, j, seats[i]!, seats[j]!);
    }
  }
}

/**
 * Seating.
 *
 * An avatar that sits down has to sit on something and face the right way. These are the two
 * things that make the village read as furnished rather than as dots on a grid, and both are
 * easy to break by nudging a zone or a prop.
 */
describe("seating", () => {
  it("fills the workshop desks before anyone stands around", () => {
    const total = WORKSHOP_DESKS + 4;
    for (let i = 0; i < WORKSHOP_DESKS; i++) {
      const seat = slotPosition("workshop", i, total);
      expect(seat.seated, `desk ${i}`).toBe(true);
      expect(seat.facing).toBe(Math.PI); // turned towards the monitor
    }
  });

  it("seats everybody just in front of their own desk", () => {
    const zone = ZONES.workshop;
    for (let i = 0; i < WORKSHOP_DESKS; i++) {
      const seat = slotPosition("workshop", i, WORKSHOP_DESKS);
      const desk = deskPosition(zone, i);
      expect(seat.x, `desk ${i} is not in line with its chair`).toBeCloseTo(desk.x);
      // In front of the desk, between it and the camera, within arm's reach.
      expect(seat.z - desk.z).toBeGreaterThan(0.5);
      expect(seat.z - desk.z).toBeLessThan(1.5);
    }
  });

  it("gives every desk its own spot, in rows", () => {
    const spots = Array.from({ length: WORKSHOP_DESKS }, (_, i) =>
      slotPosition("workshop", i, WORKSHOP_DESKS),
    );
    const unique = new Set(spots.map((s) => `${s.x.toFixed(3)},${s.z.toFixed(3)}`));
    expect(unique.size).toBe(WORKSHOP_DESKS);
    expect(new Set(spots.map((s) => s.z.toFixed(3))).size, "should be more than one row").toBeGreaterThan(1);
  });

  it("keeps every desk and chair inside the workshop", () => {
    const zone = ZONES.workshop;
    const DESK_HALF_W = 1.15; // the desk prop is 2.3 wide
    for (let i = 0; i < WORKSHOP_DESKS; i++) {
      const desk = deskPosition(zone, i);
      const seat = slotPosition("workshop", i, WORKSHOP_DESKS);
      expect(Math.abs(desk.x - zone.centre.x) + DESK_HALF_W, `desk ${i} pokes out the side`).toBeLessThan(
        zone.half.x,
      );
      for (const z of [desk.z, seat.z]) {
        expect(Math.abs(z - zone.centre.z), `desk ${i} pokes out front or back`).toBeLessThan(zone.half.z);
      }
    }
  });

  it("keeps the overflow off the desks", () => {
    const total = WORKSHOP_DESKS + 9;
    const backmostFreeZ = Math.max(
      ...Array.from({ length: WORKSHOP_DESKS }, (_, i) => slotPosition("workshop", i, total).z),
    );
    for (let i = WORKSHOP_DESKS; i < total; i++) {
      const spot = slotPosition("workshop", i, total);
      expect(spot.seated).toBeUndefined();
      expect(spot.z, `standee ${i} is in among the desks`).toBeGreaterThan(backmostFreeZ);
      expect(Math.abs(spot.z - ZONES.workshop.centre.z)).toBeLessThan(ZONES.workshop.half.z);
    }
  });

  it("seats the two bench rows back to back", () => {
    expect(slotPosition("park", 0, 16).facing).toBe(0);
    expect(slotPosition("park", BENCH_SEATS, 16).facing).toBe(Math.PI);
  });

  it("turns cafe guests in towards their table", () => {
    for (let seat = 0; seat < 4; seat++) {
      const spot = slotPosition("cafe", seat, 4);
      expect(spot.seated).toBe(true);
      // Walking one step along the facing direction must move closer to the table centre.
      const table = { x: ZONES.cafe.centre.x - 6, z: ZONES.cafe.centre.z - 3.2 };
      const before = Math.hypot(spot.x - table.x, spot.z - table.z);
      const after = Math.hypot(
        spot.x + Math.sin(spot.facing!) * 0.5 - table.x,
        spot.z + Math.cos(spot.facing!) * 0.5 - table.z,
      );
      expect(after).toBeLessThan(before);
    }
  });
});

describe("layout", () => {
  it("gives every agent a position", () => {
    const agents = [agent({ sessionId: "a" }), agent({ sessionId: "b", state: "waiting" })];
    const positions = layout(agents);
    expect(positions.size).toBe(2);
  });

  it("does not stack two agents in the same zone on the same spot", () => {
    const agents = Array.from({ length: 6 }, (_, i) => agent({ sessionId: `s${i}` }));
    const positions = [...layout(agents).values()];
    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        const d = Math.hypot(positions[i]!.x - positions[j]!.x, positions[i]!.z - positions[j]!.z);
        expect(d).toBeGreaterThan(0.8);
      }
    }
  });

  it("is stable when an unrelated agent in another zone changes", () => {
    const base = [agent({ sessionId: "a" }), agent({ sessionId: "b" })];
    const before = layout(base).get("a");
    const after = layout([...base, agent({ sessionId: "z", state: "done" })]).get("a");
    expect(after).toEqual(before);
  });

  it("seats an arrival at the free seat nearest the door it comes in by", () => {
    const rooms = { workshop: WORKSHOP_DESKS, cafe: 24, hotel: HOTEL_BEDS, park: BENCH_SEATS * 2 };
    for (const [zone, cap] of Object.entries(rooms) as [ZoneId, number][]) {
      const door = { x: ZONES[zone].centre.x + 3, z: ZONES[zone].centre.z + ZONES[zone].half.z };
      const away = (p: { x: number; z: number }) => Math.hypot(p.x - door.x, p.z - door.z);
      const p = layout([agent({ sessionId: "a", state: statesIn(zone)[0]! })], new Map(), () => door).get("a")!;
      const nearest = Math.min(...Array.from({ length: cap }, (_, i) => away(slotPosition(zone, i, cap))));
      expect(away(p)).toBeCloseTo(nearest);
    }
  });

  it("keeps everyone in their seat when somebody else arrives or leaves", () => {
    const seats = new Map();
    const a = agent({ sessionId: "m" });
    const before = layout([a, agent({ sessionId: "z" })], seats).get("m");
    const joined = layout([agent({ sessionId: "b" }), a, agent({ sessionId: "z" })], seats).get("m");
    const left = layout([a], seats).get("m");
    expect(joined).toEqual(before);
    expect(left).toEqual(before);
  });

  it("gives a freed seat to the next arrival", () => {
    const seats = new Map();
    const door = () => ZONES.cafe.centre;
    layout([agent({ sessionId: "a", state: "done" })], seats, door);
    const first = seats.get("a");
    layout([], seats, door);
    layout([agent({ sessionId: "b", state: "done" })], seats, door);
    expect(seats.get("b")).toEqual(first);
  });

  it("parks a subagent next to its parent rather than in its own slot", () => {
    const agents = [
      agent({ sessionId: "parent" }),
      agent({ sessionId: "parent:child", parentSessionId: "parent", isSubagent: true }),
    ];
    const p = layout(agents);
    const d = Math.hypot(
      p.get("parent")!.x - p.get("parent:child")!.x,
      p.get("parent")!.z - p.get("parent:child")!.z,
    );
    expect(d).toBeLessThan(2);
  });

  it("handles an empty village", () => {
    expect(layout([]).size).toBe(0);
  });
});

describe("state colours", () => {
  it("defines a colour for every state", () => {
    const states: AgentState[] = [
      "spawning", "thinking", "working", "waiting", "blocked", "compacting", "done", "gone",
    ];
    for (const state of states) expect(STATE_COLOUR[state]).toBeTypeOf("number");
  });

  it("makes waiting and blocked visually distinct from everything calm", () => {
    expect(STATE_COLOUR.waiting).not.toBe(STATE_COLOUR.done);
    expect(STATE_COLOUR.blocked).not.toBe(STATE_COLOUR.working);
  });
});
