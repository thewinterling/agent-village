/**
 * Where every avatar belongs, as pure geometry.
 *
 * Kept free of three.js so the placement rules - the part that is actually easy to get wrong -
 * can be unit tested without a renderer.
 *
 * Layout rule from plan 0077: the park (waiting) and the infirmary (blocked) sit front and
 * centre in the default camera frame. Noticing those two states without hunting is the entire
 * point of the thing.
 *
 * Second layout rule, learned the hard way: nothing walled may sit within its own wall height
 * of the road. See `backWall`. The library and the infirmary used to border the spine and
 * swallowed the whole eastern half of it, so an avatar crossing the village disappeared.
 */

import type { Agent, AgentState } from "./types";

export type ZoneId = "workshop" | "park" | "cafe" | "infirmary" | "library" | "hotel" | "door";

export interface Vec2 {
  x: number;
  z: number;
}

/**
 * A place to stand, plus the way to face once you get there.
 *
 * Facing only matters where furniture dictates it - a chair, a bench. Elsewhere it is left
 * undefined and the avatar keeps whatever direction it walked in on.
 */
export interface Slot extends Vec2 {
  facing?: number;
  /** There is a chair, bench or desk here, so the avatar should sit rather than stand. */
  seated?: boolean;
}

export interface Zone {
  id: ZoneId;
  label: string;
  /** Centre of the zone floor. */
  centre: Vec2;
  /** Half-extent on each axis. */
  half: Vec2;
  colour: number;
  /** How avatars arrange themselves once they arrive. */
  arrangement: "grid" | "bench" | "table" | "point" | "desks" | "beds";
  /**
   * Height of the solid wall along the zone's north edge, where it has one.
   *
   * Lives here rather than in `world.ts` because it decides what the camera can see behind
   * the zone, which is a layout question: in this projection the ground at `(x, z)` is hidden
   * by anything at `(x + d, z + d)` taller than `d`, so a wall casts its height in screen
   * depth northwards. Roads have to be kept that far clear of it.
   */
  backWall?: number;
  /** What standing here means, shown behind the (i) on the zone sign. */
  info: string;
}

export const ZONES: Record<ZoneId, Zone> = {
  workshop: {
    id: "workshop",
    label: "Workshop",
    centre: { x: -15.6, z: -6 },
    half: { x: 9.6, z: 8.4 },
    colour: 0x4c5a7a,
    arrangement: "desks",
    backWall: 3.2,
    info:
      "Busy, and nothing is needed from you. At a desk means a tool call is running; " +
      "standing means the model is generating between calls.",
  },
  park: {
    id: "park",
    label: "Park",
    centre: { x: 0, z: 10.8 },
    half: { x: 9.6, z: 4.8 },
    colour: 0x4e7c58,
    arrangement: "bench",
    info:
      "The one zone that means you. The agent hit a permission prompt or asked a question " +
      "and is waiting. Cursor cannot report this, so for its agents it is inferred from 45s " +
      "of silence and marked with ? instead of !.",
  },
  cafe: {
    id: "cafe",
    label: "Cafe",
    centre: { x: 16.8, z: -6 },
    half: { x: 11.4, z: 9 },
    colour: 0x8a7050,
    arrangement: "table",
    info: "Finished its turn and idle. Sessions are retired from here after an hour of silence.",
  },
  infirmary: {
    id: "infirmary",
    label: "Infirmary",
    centre: { x: 16.8, z: 13.8 },
    half: { x: 6, z: 4.2 },
    colour: 0x834850,
    arrangement: "grid",
    backWall: 3.0,
    info:
      "A tool call or a turn failed. Usually momentary - the agent recovers by itself, and " +
      "the state clears after 2 minutes so one failed grep does not leave a red avatar here.",
  },
  library: {
    id: "library",
    label: "Library",
    centre: { x: -16.8, z: 13.8 },
    half: { x: 6, z: 4.2 },
    colour: 0x615186,
    arrangement: "grid",
    backWall: 3.4,
    info:
      "Compacting: the conversation outgrew the context window and the agent is summarising " +
      "itself. Briefly unresponsive, and it loses some detail on the way out.",
  },
  hotel: {
    id: "hotel",
    label: "Hotel",
    centre: { x: 33.6, z: 18 },
    half: { x: 9, z: 9 },
    colour: 0x3d5d6b,
    arrangement: "beds",
    backWall: 3.0,
    info:
      "Turned in. Finished over an hour ago, so it is out of the cafe but still on the map - " +
      "this is the day's work, in one place. Prompt it and it gets up; after a day it leaves.",
  },
  door: {
    id: "door",
    label: "Front door",
    centre: { x: 0, z: 19.2 },
    half: { x: 2.4, z: 1.2 },
    colour: 0x363d4d,
    arrangement: "point",
    info: "Arriving or leaving. New sessions walk in here, and ended ones walk out.",
  },
};

const STATE_ZONE: Record<AgentState, ZoneId> = {
  spawning: "door",
  thinking: "workshop",
  working: "workshop",
  waiting: "park",
  blocked: "infirmary",
  compacting: "library",
  done: "cafe",
  sleeping: "hotel",
  gone: "door",
};

export function zoneFor(agent: Pick<Agent, "state">): ZoneId {
  return STATE_ZONE[agent.state];
}

/** Which states send an avatar to this zone. Derived, so the legend cannot drift from the map. */
export function statesIn(zone: ZoneId): AgentState[] {
  return (Object.keys(STATE_ZONE) as AgentState[]).filter((state) => STATE_ZONE[state] === zone);
}

/**
 * Where the zone's name stands.
 *
 * Over the zone's own floor, not out past the wall behind it: a name hanging in the sky
 * beyond the back wall reads as belonging to the grass as much as to the room.
 *
 * Height has to be paid for in depth. Lifting a sign moves it up the screen, and on this
 * camera one unit of height climbs twice as fast as one unit of z, so the sign is pushed
 * towards the viewer to compensate and capped on shallow zones - at a fixed height the front
 * door's name would float off its pad and land in the park behind it.
 */
const SIGN_HEIGHT = 1.6;
const SIGN_NEAR_EDGE = 0.45;

export function signAnchor(zone: Zone): { x: number; y: number; z: number } {
  return {
    x: zone.centre.x,
    y: Math.min(SIGN_HEIGHT, zone.half.z * 0.4),
    z: zone.centre.z + zone.half.z * SIGN_NEAR_EDGE,
  };
}

/**
 * The ground every zone stands on, with a margin of grass around the edge.
 *
 * Measured rather than written down. A hardcoded slab is fine right up until somebody adds a
 * zone, and then half a room hangs over the edge of the world into the sky.
 */
export function villageBounds(margin = 6): { centre: Vec2; half: Vec2 } {
  const edges = Object.values(ZONES);
  const lo = (axis: "x" | "z") => Math.min(...edges.map((z) => z.centre[axis] - z.half[axis]));
  const hi = (axis: "x" | "z") => Math.max(...edges.map((z) => z.centre[axis] + z.half[axis]));
  return {
    centre: { x: (lo("x") + hi("x")) / 2, z: (lo("z") + hi("z")) / 2 },
    half: { x: (hi("x") - lo("x")) / 2 + margin, z: (hi("z") - lo("z")) / 2 + margin },
  };
}

/** Number of bench seats in the park. Beyond this, avatars stand and queue behind them. */
export const BENCH_SEATS = 8;
/**
 * Half-width of the walkway running north-south through the park.
 *
 * The park is a thoroughfare as well as a zone - the route in from the front door goes through
 * it - so the benches are split either side of this gap rather than spanning the full width.
 */
export const PARK_PATH_HALF_W = 1.2;
/** Clear ground between the walkway edge and the first bench seat. */
const BENCH_INSET = 0.2;
const BENCH_PITCH = 1.5;

/**
 * The workshop's desk grid.
 *
 * It used to be a single row of four against the back wall, which left most of a 16x14 room
 * as bare floor and had everyone past the fourth agent standing about. Laid out as a proper
 * open-plan office instead, with the back rows filling first.
 */
const DESK_COLS = 5;
const DESK_ROWS = 3;
export const WORKSHOP_DESKS = DESK_COLS * DESK_ROWS;
const DESK_PITCH_X = 2.9;
/** Enough that the row behind can walk past somebody already sitting down. */
const DESK_PITCH_Z = 3.4;
const DESK_WALL_GAP = 0.6;
/** How far in front of its desk the chair stands. */
const DESK_SEAT_OFFSET = 0.95;

/**
 * Where a desk stands.
 *
 * Shared with `world.ts`, which builds the furniture from it - the seat and the desk it
 * belongs to drifting apart is exactly the bug that leaves an avatar sitting on thin air.
 */
export function deskPosition(zone: Zone, index: number): Vec2 {
  const col = index % DESK_COLS;
  const row = Math.floor(index / DESK_COLS);
  return {
    x: zone.centre.x + (col - (DESK_COLS - 1) / 2) * DESK_PITCH_X,
    z: zone.centre.z - zone.half.z + DESK_WALL_GAP + row * DESK_PITCH_Z,
  };
}

/**
 * The hotel's dormitory.
 *
 * Sized for a working day rather than a moment: everything that finished since this morning
 * ends up here, which is fifteen or more sessions on a normal day and far more than any other
 * zone has to hold at once.
 */
const BED_COLS = 6;
const BED_ROWS = 4;
export const HOTEL_BEDS = BED_COLS * BED_ROWS;
const BED_PITCH_X = 2.7;
/** A bed is two units long, so this leaves room to walk between the foot of one and the next. */
const BED_PITCH_Z = 3.0;
const BED_WALL_GAP = 1.5;
/** Where on the bed somebody sits: up by the pillow, not perched on the end. */
const BED_SIT_OFFSET = -0.35;

/** Where a bed stands. Shared with `world.ts` so the sleeper and the bed cannot drift apart. */
export function bedPosition(zone: Zone, index: number): Vec2 {
  const col = index % BED_COLS;
  const row = Math.floor(index / BED_COLS);
  return {
    x: zone.centre.x + (col - (BED_COLS - 1) / 2) * BED_PITCH_X,
    z: zone.centre.z - zone.half.z + BED_WALL_GAP + row * BED_PITCH_Z,
  };
}

/**
 * Deterministic slot inside a zone.
 *
 * Determinism matters more than prettiness: if the same agent were given a different slot on
 * every frame it would jitter across the floor instead of standing still.
 */
export function slotPosition(zoneId: ZoneId, index: number, total: number): Slot {
  const zone = ZONES[zoneId];
  const n = Math.max(1, total);

  switch (zone.arrangement) {
    case "point":
      return spread(zone.centre, index, n, 1.4);

    case "bench": {
      // Two rows of benches, each row split either side of the walkway so nobody sits in the
      // middle of the path everyone else walks down.
      const seat = index % BENCH_SEATS;
      const row = Math.floor(index / BENCH_SEATS);
      const side = seat < BENCH_SEATS / 2 ? -1 : 1;
      const place = seat % (BENCH_SEATS / 2);
      const x =
        zone.centre.x + side * (PARK_PATH_HALF_W + BENCH_INSET + (place + 0.5) * BENCH_PITCH);
      const z = zone.centre.z - zone.half.z * 0.35 + row * 2.6;
      // The two bench rows sit back to back, so their occupants look opposite ways.
      return { x, z, facing: row === 0 ? 0 : Math.PI, seated: true };
    }

    case "table": {
      // Four-seat tables; one table fills before the next opens. The seat radius has to clear
      // an avatar's own width or the cafe turns into a pile of overlapping capsules.
      const perTable = 4;
      const table = Math.floor(index / perTable);
      const seat = index % perTable;
      const cols = 3;
      const tx = zone.centre.x + ((table % cols) - (cols - 1) / 2) * 6.0;
      const tz = zone.centre.z + (Math.floor(table / cols) - 0.5) * 6.4;
      // Axis-aligned seats on purpose. At 45 degrees the opposite pair differs by (+d, +d),
      // which is exactly the isometric camera's view axis, so the two avatars land on the same
      // screen pixel and look like one. See the screen-separation test.
      const angle = (seat / perTable) * Math.PI * 2;
      return {
        x: tx + Math.cos(angle) * 1.85,
        z: tz + Math.sin(angle) * 1.85,
        facing: Math.atan2(-Math.cos(angle), -Math.sin(angle)), // turned in towards the table
        seated: true,
      };
    }

    case "desks": {
      // The desks get taken first. A furnished office with everyone standing in the middle of
      // it looks wrong, and a seated agent reads as "working" far faster than a standing one.
      if (index < WORKSHOP_DESKS) {
        const desk = deskPosition(zone, index);
        return {
          x: desk.x,
          z: desk.z + DESK_SEAT_OFFSET,
          facing: Math.PI, // at the monitor, which means back to the camera
          seated: true,
        };
      }
      // Everyone else works standing, in the clear strip between the last row of desks and
      // the door, which is the only floor the grid still has to itself.
      const spare = index - WORKSHOP_DESKS;
      return grid(zone, spare, n - WORKSHOP_DESKS, { offsetZ: 4.4, insetZ: 4.2 });
    }

    case "beds": {
      // Propped up against the pillow, facing the room. Nobody is under the covers: a sleeper
      // you cannot recognise is no use, and the whole point of the hotel is still being able
      // to see who was working today.
      if (index < HOTEL_BEDS) {
        const bed = bedPosition(zone, index);
        return { x: bed.x, z: bed.z + BED_SIT_OFFSET, facing: 0, seated: true };
      }
      // A busy day overflows. The spare floor is the strip in front of the last row of beds.
      return grid(zone, index - HOTEL_BEDS, n - HOTEL_BEDS, { offsetZ: 6.4, insetZ: 7.0 });
    }

    case "grid":
    default:
      return grid(zone, index, n, {});
  }
}

function grid(zone: Zone, index: number, total: number, opts: { offsetZ?: number; insetZ?: number }): Vec2 {
  const n = Math.max(1, total);
  const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
  const rows = Math.ceil(n / cols);
  const col = index % cols;
  const row = Math.floor(index / cols);
  const halfZ = zone.half.z - (opts.insetZ ?? 0);
  return {
    x: zone.centre.x + ((col + 0.5) / cols - 0.5) * zone.half.x * 2 * 0.85,
    z: zone.centre.z + (opts.offsetZ ?? 0) + ((row + 0.5) / rows - 0.5) * halfZ * 2 * 0.85,
  };
}

function spread(centre: Vec2, index: number, total: number, gap: number): Vec2 {
  const offset = (index - (total - 1) / 2) * gap;
  return { x: centre.x + offset, z: centre.z };
}

/**
 * Where a ground position lands on screen, for the isometric camera in `world.ts`.
 *
 * Two avatars can be metres apart in world space and still overlap on screen if their offset
 * runs along the camera's view axis. Only this projection can tell you that.
 */
export function toScreen(p: Vec2): Vec2 {
  return { x: (p.x - p.z) * Math.SQRT1_2, z: (p.x + p.z) / Math.sqrt(6) };
}

/** Which seat an agent has taken, remembered between snapshots by the caller. */
export interface Seat {
  zone: ZoneId;
  index: number;
}

/**
 * Fixed seats per arrangement. Agents keep their seat while they stay in the zone, so these
 * must not depend on how many others are in the room. A doorway is somewhere to pass through,
 * so it has none and keeps the old spread-by-count behaviour.
 */
const SEATS: Partial<Record<Zone["arrangement"], number>> = {
  desks: WORKSHOP_DESKS,
  beds: HOTEL_BEDS,
  table: 24, // six tables of four, as furnished in world.ts
  bench: BENCH_SEATS * 2,
  grid: 16,
};

/**
 * Assign every agent a position.
 *
 * An agent arriving in a zone takes the free seat nearest to `entry(agent)` - the door it
 * walks in through - and keeps it until it leaves. `seats` is that memory, updated in place.
 */
export function layout(
  agents: Agent[],
  seats = new Map<string, Seat>(),
  entry: (agent: Agent) => Vec2 = (agent) => ZONES[zoneFor(agent)].centre,
): Map<string, Slot> {
  const byZone = new Map<ZoneId, Agent[]>();
  const subagents: Agent[] = [];
  for (const agent of stableSort(agents)) {
    // Subagents stand just behind their parent rather than taking a slot of their own.
    if (agent.parentSessionId && agents.some((a) => a.sessionId === agent.parentSessionId)) {
      subagents.push(agent);
      continue;
    }
    const zone = zoneFor(agent);
    const list = byZone.get(zone) ?? [];
    list.push(agent);
    byZone.set(zone, list);
  }

  const live = new Set(agents.map((a) => a.sessionId));
  for (const id of seats.keys()) if (!live.has(id)) seats.delete(id);

  const out = new Map<string, Slot>();
  for (const [zoneId, list] of byZone) {
    const cap = SEATS[ZONES[zoneId].arrangement];
    if (cap === undefined) {
      list.forEach((agent, i) => out.set(agent.sessionId, slotPosition(zoneId, i, list.length)));
      continue;
    }

    const taken = new Set<number>();
    const newcomers: Agent[] = [];
    for (const agent of list) {
      const seat = seats.get(agent.sessionId);
      if (seat?.zone === zoneId && seat.index < cap) taken.add(seat.index);
      else newcomers.push(agent);
    }

    const overflow: Agent[] = [];
    for (const agent of newcomers) {
      const from = entry(agent);
      let best = -1;
      let bestD = Infinity;
      for (let i = 0; i < cap; i++) {
        if (taken.has(i)) continue;
        const p = slotPosition(zoneId, i, cap);
        const d = Math.hypot(p.x - from.x, p.z - from.z);
        if (d < bestD) [best, bestD] = [i, d];
      }
      if (best < 0) {
        overflow.push(agent);
        seats.delete(agent.sessionId);
        continue;
      }
      taken.add(best);
      seats.set(agent.sessionId, { zone: zoneId, index: best });
    }

    for (const agent of list) {
      const seat = seats.get(agent.sessionId);
      if (seat) out.set(agent.sessionId, slotPosition(zoneId, seat.index, cap));
    }
    // A full room: the rest stand in the overflow, which does shift as people come and go.
    overflow.forEach((agent, i) =>
      out.set(agent.sessionId, slotPosition(zoneId, cap + i, cap + overflow.length)),
    );
  }

  for (const agent of subagents) {
    seats.delete(agent.sessionId);
    const parent = out.get(agent.parentSessionId!) ?? slotPosition(zoneFor(agent), 0, 1);
    out.set(agent.sessionId, { x: parent.x + 1.1, z: parent.z + 1.1 });
  }
  return out;
}

function stableSort(agents: Agent[]): Agent[] {
  return [...agents].sort((a, b) => a.sessionId.localeCompare(b.sessionId));
}

/** Colour per state, shared by the avatar tint, the HUD pills and the table. */
export const STATE_COLOUR: Record<AgentState, number> = {
  spawning: 0xc3cad8,
  thinking: 0xb0b4ff,
  working: 0x8fc4ff,
  waiting: 0xffd089,
  blocked: 0xff9aa2,
  compacting: 0xd2a8ff,
  done: 0x87e6b0,
  // Deliberately the dimmest of the live states. Nothing here wants your attention.
  sleeping: 0x7e86b5,
  gone: 0x6a7183,
};
