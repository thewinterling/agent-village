/**
 * The road network: the only places an avatar may cross the village.
 *
 * Every zone opens onto one spine through a gate in its fence. The paving in `world.ts` is
 * generated from this same graph, so what you see on the ground is exactly where they walk -
 * previously avatars took the straight line to their new slot and strolled through the cafe
 * railing on the way.
 *
 * Why a spine rather than one road per state transition: almost every state can follow almost
 * every other. A turn can end, fail, need you, or start compacting from wherever the agent
 * currently is, and the reaper can retire it from anywhere at all. Drawn edge by edge that is
 * a nearly complete graph over six zones and reads as a scribble. A spine every zone gates
 * onto carries all of those transitions and stays legible.
 *
 * The one exception is the alley between the workshop and the cafe. See `ALLEY_Z`.
 */

import { PARK_PATH_HALF_W, ZONES, type Vec2, type ZoneId } from "./zones";

/** The main road, threaded through the gap between workshop/cafe and library/infirmary. */
export const SPINE_Z = 4;
/** Width of the paving. Also the width of the gap cut in a fence where a gate meets it. */
export const PATH_W = 1.4;
export const GATE_W = 2.4;

const north = (id: ZoneId) => ZONES[id].centre.z - ZONES[id].half.z;
const south = (id: ZoneId) => ZONES[id].centre.z + ZONES[id].half.z;

/**
 * Where each zone opens onto the network, on its own boundary.
 *
 * Derived from the zone bounds rather than written out, so moving a zone moves its gate with
 * it instead of leaving a doorway hanging in the grass.
 */
export const GATES: Record<ZoneId, Vec2> = {
  workshop: { x: ZONES.workshop.centre.x, z: south("workshop") },
  cafe: { x: ZONES.cafe.centre.x, z: south("cafe") },
  library: { x: ZONES.library.centre.x, z: north("library") },
  infirmary: { x: ZONES.infirmary.centre.x, z: north("infirmary") },
  hotel: { x: ZONES.hotel.centre.x, z: north("hotel") },
  park: { x: ZONES.park.centre.x, z: north("park") },
  door: { x: ZONES.door.centre.x, z: north("door") },
};

/**
 * The alley between the workshop and the cafe, and the doors at either end of it.
 *
 * Those two rooms face each other across eleven units of grass, and working -> done is by far
 * the most common move an agent makes. Sent round the spine it is most of the length of the
 * village for a trip you can see across, so each room gets a second door onto the gap.
 *
 * A side door is only ever a shortcut. Every zone still meets the spine through its own gate,
 * which is the way in from everywhere else.
 */
export const ALLEY_Z = -4.8;

const east = (id: ZoneId) => ZONES[id].centre.x + ZONES[id].half.x;
const west = (id: ZoneId) => ZONES[id].centre.x - ZONES[id].half.x;

export const SIDE_GATES: Partial<Record<ZoneId, Vec2>> = {
  workshop: { x: east("workshop"), z: ALLEY_Z },
  cafe: { x: west("cafe"), z: ALLEY_Z },
};

const gate = (id: ZoneId) => `gate:${id}`;
const side = (id: ZoneId) => `side:${id}`;

/** Every opening in a zone's boundary. The gate onto the spine comes first. */
export function doorsOf(id: ZoneId): string[] {
  return SIDE_GATES[id] ? [gate(id), side(id)] : [gate(id)];
}

/**
 * Where a zone's gate stub meets the spine.
 *
 * Straight south of the gate for most of them, because nothing stands between the doorway and
 * the road. The library and the infirmary are the exception: their doorway is a gap cut into a
 * solid back wall that faces the road. In this projection the ground at `(x, z)` is hidden by
 * anything at `(x + d, z + d)` taller than `d`, so a stub running straight at such a doorway
 * spends its whole length in the shadow of the wall *beside* the gap. Angled back along
 * `(-1, -1)` instead, it stays in the shadow of the doorway itself - which is a hole - and the
 * approach stays in view all the way to the door.
 */
function junction(id: ZoneId): Vec2 {
  const point = GATES[id];
  const behindAWall = ZONES[id].backWall !== undefined && point.z === north(id);
  return { x: point.x - (behindAWall ? point.z - SPINE_Z : 0), z: SPINE_Z };
}

export const NODES: Record<string, Vec2> = {
  "gate:workshop": GATES.workshop,
  "gate:cafe": GATES.cafe,
  "gate:library": GATES.library,
  "gate:infirmary": GATES.infirmary,
  "gate:hotel": GATES.hotel,
  "gate:park": GATES.park,
  "gate:door": GATES.door,

  "spine:library": junction("library"),
  "spine:workshop": junction("workshop"),
  "spine:centre": junction("park"),
  "spine:infirmary": junction("infirmary"),
  "spine:cafe": junction("cafe"),
  "spine:hotel": junction("hotel"),

  "side:workshop": SIDE_GATES.workshop!,
  "side:cafe": SIDE_GATES.cafe!,
  // Where the alley meets the spur down to the spine. Without that spur the alley would be an
  // island, and an avatar whose state changed halfway along it would have nowhere to route to.
  "alley:centre": { x: (east("workshop") + west("cafe")) / 2, z: ALLEY_Z },

  // The park doubles as the thoroughfare from the front door, so its walkway is part of the
  // network and the benches are arranged either side of it.
  "park:south": { x: ZONES.park.centre.x, z: south("park") },
};

export interface Link {
  from: string;
  to: string;
  width?: number;
  /**
   * The run crosses a zone floor, which stands proud of the grass, so its paving has to be
   * laid on top of that floor instead of on the ground or it disappears underneath it.
   */
  onFloor?: boolean;
}

/** Undirected. Each one is drawn as a slab of paving. */
export const LINKS: Link[] = [
  { from: "spine:library", to: "spine:workshop" },
  { from: "spine:workshop", to: "spine:centre" },
  { from: "spine:centre", to: "spine:infirmary" },
  { from: "spine:infirmary", to: "spine:cafe" },
  { from: "spine:cafe", to: "spine:hotel" },

  { from: "gate:library", to: "spine:library" },
  { from: "gate:workshop", to: "spine:workshop" },
  { from: "gate:infirmary", to: "spine:infirmary" },
  { from: "gate:cafe", to: "spine:cafe" },
  { from: "gate:hotel", to: "spine:hotel" },
  { from: "gate:park", to: "spine:centre" },

  { from: "side:workshop", to: "alley:centre" },
  { from: "alley:centre", to: "side:cafe" },
  { from: "alley:centre", to: "spine:centre" },

  { from: "park:south", to: "gate:park", width: PARK_PATH_HALF_W * 2, onFloor: true },
  { from: "gate:door", to: "park:south", width: PARK_PATH_HALF_W * 2 },
];

const dist = (a: Vec2, b: Vec2) => Math.hypot(a.x - b.x, a.z - b.z);

const ADJACENCY: Record<string, string[]> = (() => {
  const out: Record<string, string[]> = {};
  for (const id of Object.keys(NODES)) out[id] = [];
  for (const { from, to } of LINKS) {
    out[from]!.push(to);
    out[to]!.push(from);
  }
  return out;
})();

/**
 * Which zone a point is standing in, if any.
 *
 * Inset slightly so points sitting exactly on a boundary - every gate, and both ends of the
 * park walkway - count as being on the network rather than inside the zone.
 */
export function zoneAt(p: Vec2): ZoneId | null {
  for (const zone of Object.values(ZONES)) {
    if (
      Math.abs(p.x - zone.centre.x) < zone.half.x - 0.15 &&
      Math.abs(p.z - zone.centre.z) < zone.half.z - 0.15
    ) {
      return zone.id;
    }
  }
  return null;
}

/**
 * Nodes reachable in a straight line from inside a zone, without crossing its fence.
 *
 * Normally just the zone's own gate. The park is the exception: its walkway runs right through
 * it, so somebody in the park can join the network at either end.
 */
function openNodes(zone: ZoneId): string[] {
  return zone === "park" ? ["gate:park", "park:south"] : doorsOf(zone);
}

/** The point on segment a-b closest to p. */
function closestOnSegment(p: Vec2, a: Vec2, b: Vec2): Vec2 {
  const vx = b.x - a.x;
  const vz = b.z - a.z;
  const len2 = vx * vx + vz * vz;
  if (len2 === 0) return a;
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * vx + (p.z - a.z) * vz) / len2));
  return { x: a.x + vx * t, z: a.z + vz * t };
}

/**
 * Where an avatar standing outside every zone joins the network.
 *
 * The ends of whichever run it is closest to - never just "the nearest node anywhere", which
 * would happily send it diagonally across the whole village and straight through a railing to
 * reach a gate on the far side. Offering both ends lets the caller pick whichever actually
 * leads towards the destination, so somebody already striding along the spine carries on
 * rather than turning round.
 */
function networkEntries(from: Vec2): string[] {
  let best: { d: number; ids: string[] } | null = null;
  for (const link of LINKS) {
    const q = closestOnSegment(from, NODES[link.from]!, NODES[link.to]!);
    const d = dist(from, q);
    if (!best || d < best.d) best = { d, ids: [link.from, link.to] };
  }
  return best ? best.ids : Object.keys(NODES);
}

function shortestPath(from: string, to: string): { ids: string[]; cost: number } | null {
  if (from === to) return { ids: [from], cost: 0 };

  const best: Record<string, number> = { [from]: 0 };
  const prev: Record<string, string> = {};
  const queue = new Set(Object.keys(NODES));

  while (queue.size > 0) {
    let at: string | null = null;
    for (const id of queue) {
      if (best[id] !== undefined && (at === null || best[id]! < best[at]!)) at = id;
    }
    if (at === null || best[at] === undefined) break;
    queue.delete(at);
    if (at === to) break;

    for (const next of ADJACENCY[at] ?? []) {
      if (!queue.has(next)) continue;
      const cost = best[at]! + dist(NODES[at]!, NODES[next]!);
      if (best[next] === undefined || cost < best[next]!) {
        best[next] = cost;
        prev[next] = at;
      }
    }
  }

  if (best[to] === undefined) return null;
  const ids: string[] = [to];
  while (ids[0] !== from) ids.unshift(prev[ids[0]!]!);
  return { ids, cost: best[to]! };
}

/**
 * Waypoints from a position to a zone's gate, along the network.
 *
 * Empty when the avatar is already in the target zone - there are no fences inside a zone, so
 * it just walks to its slot. The caller appends the slot itself.
 *
 * Every candidate entry point is costed end to end rather than picking the nearest one, so an
 * agent halfway down the park walkway when its state changes carries on in the direction it
 * was already going instead of doubling back to the gate it came in by.
 */
export function routeTo(from: Vec2, to: ZoneId): Vec2[] {
  const here = zoneAt(from);
  if (here === to) return [];

  const entries = here ? openNodes(here) : networkEntries(from);
  let best: { cost: number; ids: string[] } | null = null;

  for (const id of entries) {
    for (const door of doorsOf(to)) {
      const path = shortestPath(id, door);
      if (!path) continue;
      // The walk from the door to the slot counts as well, roughed in as the distance to the
      // middle of the room. Without it a zone with two doors would send everyone through
      // whichever one the road happens to reach first and let them trek across the floor.
      const cost = dist(from, NODES[id]!) + path.cost + dist(NODES[door]!, ZONES[to].centre);
      if (!best || cost < best.cost) best = { cost, ids: path.ids };
    }
  }

  return best ? best.ids.map((id) => NODES[id]!) : [];
}

/**
 * How close counts as having reached a waypoint.
 *
 * Looser than the arrival radius for a slot: a gate is a place to pass through, not a place to
 * stand on exactly, and a tight radius makes avatars visibly stall homing in on each corner.
 */
export const WAYPOINT_ARRIVE = 0.4;

/**
 * Drop the waypoints a walker has already reached.
 *
 * Checks the whole prefix rather than just the first: at walking speed one frame can carry an
 * avatar past two closely spaced junctions, and a stale waypoint left in the list would send
 * it back to collect it.
 */
export function advanceRoute(pos: Vec2, route: Vec2[]): Vec2[] {
  let reached = 0;
  while (reached < route.length && dist(pos, route[reached]!) <= WAYPOINT_ARRIVE) reached++;
  return reached === 0 ? route : route.slice(reached);
}

/** Half-width of the park walkway, re-exported so `world.ts` can pave it. */
export { PARK_PATH_HALF_W };
