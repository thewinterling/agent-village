/** The scene: ground, zones, props, lighting, camera. Everything that does not move. */

import * as THREE from "three";

import {
  archway,
  bed,
  bench,
  bookshelf,
  cafeTable,
  chair,
  crossSign,
  desk,
  flowerBed,
  hedge,
  hospitalBed,
  ivStand,
  lampPost,
  mat,
  nightstand,
  potPlant,
  railing,
  readingLamp,
  tree,
  wall,
  whiteboard,
} from "./props";
import { floorTexture, type FloorKind } from "./textures";
import { ALLEY_Z, GATE_W, LINKS, NODES, PATH_W } from "./paths";
import { fitFrustum } from "./view";
import {
  bedPosition,
  BENCH_SEATS,
  deskPosition,
  HOTEL_BEDS,
  PARK_PATH_HALF_W,
  signAnchor,
  villageBounds,
  WORKSHOP_DESKS,
  ZONES,
  type Vec2,
  type Zone,
} from "./zones";

export interface Stage {
  scene: THREE.Scene;
  camera: THREE.OrthographicCamera;
  renderer: THREE.WebGLRenderer;
  resize: () => void;
  setTimeOfDay: (hour: number) => void;
  /** True if the gesture that just ended was a pan rather than a click. Resets on read. */
  consumeDrag: () => boolean;
  /** Back to the default framing: whole village, no pan. */
  resetView: () => void;
}

const SKY = 0x0b0e14;

/** Below this much pointer travel a drag is just a sloppy click, not a pan. */
const DRAG_SLOP_PX = 5;

// Avatars are placed inside the middle 85% of a zone (see `slotPosition` in zones.ts), so
// furniture has to live in the outer frame or somebody ends up standing in a bookcase.

const FLOOR_KIND: Record<string, FloorKind> = {
  workshop: "tile",
  park: "grass",
  cafe: "plank",
  infirmary: "lino",
  library: "carpet",
  hotel: "carpet",
  door: "dirt",
};

export function createStage(canvas: HTMLCanvasElement): Stage {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY);
  scene.fog = new THREE.Fog(SKY, 60, 125);

  // Orthographic, looking down the classic isometric diagonal.
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 400);
  camera.position.set(42, 42, 42);
  camera.lookAt(0, 0, 2.5);

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.45;

  const ambient = new THREE.AmbientLight(0xffffff, 0.5);
  scene.add(ambient);

  // Sky/ground bounce. Without it the shadowed sides of everything go flat black and the
  // village reads as cardboard.
  const hemi = new THREE.HemisphereLight(0x9fc0ff, 0x2a2416, 0.7);
  scene.add(hemi);

  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(30, 50, 20);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0008;
  sun.shadow.normalBias = 0.02;
  const d = 45;
  Object.assign(sun.shadow.camera, { left: -d, right: d, top: d, bottom: -d, near: 1, far: 160 });
  scene.add(sun);

  // A cool fill from the opposite side so silhouettes stay legible against the dark ground.
  const fill = new THREE.DirectionalLight(0x8fb4ff, 0.35);
  fill.position.set(-28, 22, -18);
  scene.add(fill);

  scene.add(buildGround());
  scene.add(buildPaths());
  for (const zone of Object.values(ZONES)) scene.add(buildZone(zone));

  // Collected once: the day/night cycle is the only writer of emissiveIntensity.
  const glowing: THREE.MeshStandardMaterial[] = [];
  scene.traverse((o) => {
    const material = (o as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
    if (material?.userData?.glow && !glowing.includes(material)) glowing.push(material);
  });

  const bounds = contentBounds();
  const home = new THREE.Vector3(42, 42, 42);
  camera.position.copy(home);
  camera.lookAt(bounds.target.x, 0, bounds.target.z);

  // The camera orientation is now fixed forever. Panning moves the camera *along its own
  // screen axes* and never re-aims it: this is an isometric view, and letting it swing would
  // break the fixed zone layout that the whole thing is navigated by.
  const screenRight = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  const screenUp = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
  const pan = new THREE.Vector3();

  let zoomScale = 1;
  /** Half the vertical world extent on screen. One pixel is this over half the canvas height. */
  let viewHalfH = bounds.halfH;

  const resize = () => {
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    // Fit the whole village, whatever the window shape, above the bar along the bottom.
    // Hardcoding a zoom only ever frames one viewport correctly, and the village then hangs
    // off the edge on every other.
    const frustum = fitFrustum(bounds, w, h, reservedBelow(), zoomScale);
    Object.assign(camera, frustum);
    viewHalfH = (frustum.top - frustum.bottom) / 2;
    camera.updateProjectionMatrix();
    camera.position.copy(home).add(pan);
    renderer.setSize(w, h, false);
  };

  // The bar changes height with the window width as its contents wrap.
  const bottomBar = document.getElementById("bottom");
  if (bottomBar) new ResizeObserver(() => resize()).observe(bottomBar);

  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      zoomScale = Math.min(2.6, Math.max(0.25, zoomScale * (1 + Math.sign(e.deltaY) * 0.1)));
      resize();
    },
    { passive: false },
  );

  // -- drag to pan ---------------------------------------------------------------------
  // Tracked in CSS pixels and converted with the live frustum scale, so a grabbed point stays
  // under the cursor at any zoom.
  let dragFrom: { x: number; y: number } | null = null;
  let dragPx = 0;

  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    dragFrom = { x: e.clientX, y: e.clientY };
    dragPx = 0;
    canvas.setPointerCapture(e.pointerId);
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!dragFrom) return;
    const dx = e.clientX - dragFrom.x;
    const dy = e.clientY - dragFrom.y;
    dragFrom = { x: e.clientX, y: e.clientY };
    dragPx += Math.abs(dx) + Math.abs(dy);

    const worldPerPx = (2 * viewHalfH) / (canvas.clientHeight || window.innerHeight);
    pan.addScaledVector(screenRight, -dx * worldPerPx);
    pan.addScaledVector(screenUp, dy * worldPerPx);
    camera.position.copy(home).add(pan);
    // A class, not an inline cursor: the hover picker in main.ts also writes canvas.style.cursor
    // every frame, and the two would fight over it.
    if (dragPx > DRAG_SLOP_PX) canvas.classList.add("panning");
  });

  const endDrag = (e: PointerEvent) => {
    if (!dragFrom) return;
    dragFrom = null;
    canvas.classList.remove("panning");
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  /** True when the gesture that just ended was a pan, so it must not also select an avatar. */
  const consumeDrag = () => {
    const panned = dragPx > DRAG_SLOP_PX;
    dragPx = 0;
    return panned;
  };

  const resetView = () => {
    pan.set(0, 0, 0);
    zoomScale = 1;
    resize();
  };

  const setTimeOfDay = (hour: number) => {
    // A slow day/night cycle tied to the wall clock, so an overnight run looks like one. The
    // night floor stays high enough that the village is still readable at 3am - this is a
    // monitoring tool first and a toy second.
    const daylight = Math.max(0, Math.sin(((hour - 6) / 12) * Math.PI));
    ambient.intensity = 0.4 + daylight * 0.25;
    hemi.intensity = 0.45 + daylight * 0.45;
    sun.intensity = 0.9 + daylight * 0.8;
    sun.color.setHSL(0.09 + daylight * 0.05, 0.35 - daylight * 0.3, 0.55 + daylight * 0.25);
    fill.intensity = 0.45 - daylight * 0.2;

    // Lamps and screens carry the scene after dark and stay faintly on by day.
    for (const material of glowing) {
      material.emissiveIntensity = material.userData.glow * (1.5 - daylight * 1.1);
    }
  };

  resize();
  window.addEventListener("resize", resize);
  return { scene, camera, renderer, resize, setTimeOfDay, consumeDrag, resetView };
}

/**
 * What the camera has to fit, in its own screen basis.
 *
 * The camera looks down (1,1,1), so screen-right is (1,0,-1)/sqrt(2) and screen-up is
 * (-1,2,-1)/sqrt(6). Projecting the zone corners through those two vectors gives the exact
 * rectangle to frame - including wall tops, which stick well above the floor plan.
 */
/** Canvas pixels at the bottom covered by the bar there, plus a little air above it. */
function reservedBelow(): number {
  const bar = document.getElementById("bottom");
  if (!bar) return 0;
  return Math.max(0, window.innerHeight - bar.getBoundingClientRect().top) + 8;
}

function contentBounds(): { halfW: number; halfH: number; target: Vec2 } {
  const sx = (x: number, z: number) => (x - z) / Math.SQRT2;
  const sy = (x: number, y: number, z: number) => (-x + 2 * y - z) / Math.sqrt(6);

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;

  for (const zone of Object.values(ZONES)) {
    for (const dx of [-1, 1]) {
      for (const dz of [-1, 1]) {
        const x = zone.centre.x + dx * (zone.half.x + 1.6);
        const z = zone.centre.z + dz * (zone.half.z + 1.6);
        for (const y of [0, 4.2]) {
          minX = Math.min(minX, sx(x, z));
          maxX = Math.max(maxX, sx(x, z));
          minY = Math.min(minY, sy(x, y, z));
          maxY = Math.max(maxY, sy(x, y, z));
        }
      }
    }
  }

  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  // Invert the projection for y=0 to get a ground point that sits at the centre of the frame.
  const target = {
    x: (cx * Math.SQRT2 - cy * Math.sqrt(6)) / 2,
    z: (-cy * Math.sqrt(6) - cx * Math.SQRT2) / 2,
  };
  return { halfW: (maxX - minX) / 2, halfH: (maxY - minY) / 2, target };
}

function buildGround(): THREE.Group {
  const g = new THREE.Group();

  const { centre, half } = villageBounds();

  const ground = new THREE.Mesh(
    new THREE.BoxGeometry(half.x * 2, 1.2, half.z * 2),
    new THREE.MeshStandardMaterial({
      color: 0x3c4a2e,
      roughness: 1,
      map: floorTexture("grass", half.x, half.z),
    }),
  );
  ground.position.set(centre.x, -0.6, centre.z);
  ground.receiveShadow = true;
  g.add(ground);

  // A thin lip around the island, so it reads as a place rather than a clipped plane.
  const lip = new THREE.Mesh(
    new THREE.BoxGeometry(half.x * 2 + 2, 0.5, half.z * 2 + 2),
    new THREE.MeshStandardMaterial({ color: 0x1b2029, roughness: 1 }),
  );
  lip.position.set(centre.x, -1.1, centre.z);
  g.add(lip);

  return g;
}

/**
 * Paving, generated from the road graph in `paths.ts`.
 *
 * Drawn from the same edges the avatars route along, so the ground can never promise a
 * connection the pathfinder does not have - or hide one it does.
 */
function buildPaths(): THREE.Group {
  const g = new THREE.Group();
  const material = new THREE.MeshStandardMaterial({
    color: 0x6e6a60,
    roughness: 1,
    map: floorTexture("dirt", 6, 2),
  });

  /** Grass level, or just proud of a zone floor when the run crosses one. */
  const top = (onFloor?: boolean) => (onFloor ? 0.29 : 0.14);

  const slab = (w: number, d: number, x: number, z: number, rotY: number, onFloor?: boolean) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, 0.14, d), material);
    mesh.position.set(x, top(onFloor) - 0.07, z);
    mesh.rotation.y = rotY;
    mesh.receiveShadow = true;
    g.add(mesh);
  };

  const widthOf = (link: { width?: number }) => link.width ?? PATH_W;

  for (const link of LINKS) {
    const a = NODES[link.from]!;
    const b = NODES[link.to]!;
    const len = Math.hypot(b.x - a.x, b.z - a.z);
    // A box stretched along +z and then turned to face b, which covers the diagonal runs as
    // well as the axis-aligned ones.
    slab(widthOf(link), len, (a.x + b.x) / 2, (a.z + b.z) / 2, Math.atan2(b.x - a.x, b.z - a.z), link.onFloor);
  }

  // Square patches at the junctions. The straight runs stop dead at their endpoints, which
  // leaves a notch bitten out of every corner without these.
  for (const [id, p] of Object.entries(NODES)) {
    const incident = LINKS.filter((l) => l.from === id || l.to === id);
    const w = Math.max(...incident.map(widthOf));
    slab(w, w, p.x, p.z, 0, incident.some((l) => l.onFloor));
  }

  return g;
}

function buildZone(zone: Zone): THREE.Group {
  const group = new THREE.Group();

  const floor = new THREE.Mesh(
    new THREE.BoxGeometry(zone.half.x * 2, 0.26, zone.half.z * 2),
    new THREE.MeshStandardMaterial({
      color: zone.colour,
      roughness: 0.92,
      map: floorTexture(FLOOR_KIND[zone.id]!, zone.half.x, zone.half.z),
    }),
  );
  floor.position.set(zone.centre.x, 0.13, zone.centre.z);
  floor.receiveShadow = true;
  group.add(floor);

  // A kerb all the way round, which is what stops the zones looking like flat decals.
  for (const [w, d, dx, dz] of [
    [zone.half.x * 2 + 0.5, 0.25, 0, zone.half.z + 0.12],
    [zone.half.x * 2 + 0.5, 0.25, 0, -zone.half.z - 0.12],
    [0.25, zone.half.z * 2 + 0.5, zone.half.x + 0.12, 0],
    [0.25, zone.half.z * 2 + 0.5, -zone.half.x - 0.12, 0],
  ] as const) {
    const kerb = new THREE.Mesh(
      new THREE.BoxGeometry(w, 0.34, d),
      mat(zone.id === "park" ? 0x6a6457 : 0x4a5160, 0.95),
    );
    kerb.position.set(zone.centre.x + dx, 0.17, zone.centre.z + dz);
    kerb.receiveShadow = true;
    group.add(kerb);
  }

  const anchor = signAnchor(zone);
  group.add(makeLabel(zone.label, anchor.x, anchor.y, anchor.z));

  FURNISH[zone.id]?.(group, zone);
  return group;
}

/** Place a prop in world space, facing +z unless rotated. */
function at(group: THREE.Group, obj: THREE.Object3D, x: number, z: number, rotY = 0): void {
  obj.position.set(x, 0.26, z);
  obj.rotation.y = rotY;
  group.add(obj);
}

const FURNISH: Record<string, (g: THREE.Group, z: Zone) => void> = {
  workshop(g, zone) {
    const backZ = zone.centre.z - zone.half.z;
    const leftX = zone.centre.x - zone.half.x;
    const h = zone.backWall!;
    g.add(place(wall(zone.half.x * 2, h), zone.centre.x, backZ, 0));
    g.add(place(wall(zone.half.z * 2, h), leftX, zone.centre.z, Math.PI / 2));

    // Built from the same positions the seats are derived from, so nobody ends up sitting
    // next to their desk. Rotation 0 points the screens at the camera, which is the only
    // reason to model a screen at all.
    for (let i = 0; i < WORKSHOP_DESKS; i++) {
      const spot = deskPosition(zone, i);
      at(g, desk(i), spot.x, spot.z);
    }
    at(g, whiteboard(3.4), leftX + 0.3, zone.centre.z + 3.4, Math.PI / 2);
    at(g, potPlant(1), leftX + 0.8, backZ + 1.0);
    at(g, potPlant(2), zone.centre.x + zone.half.x - 0.8, backZ + 1.0);
    at(g, potPlant(3), zone.centre.x + zone.half.x - 0.8, zone.centre.z + zone.half.z - 0.8);
  },

  park(g, zone) {
    // One continuous bench per row, centred exactly on the seat line the `bench` arrangement
    // uses. Half a unit out and everybody sits on the grass next to the bench instead.
    // One bench per side of the walkway, centred on the seat line the `bench` arrangement
    // uses. Half a unit out and everybody sits on the grass next to the bench instead.
    const benchSpan = BENCH_SEATS / 2 * 1.5;
    for (const row of [0, 1]) {
      const z = zone.centre.z - zone.half.z * 0.35 + row * 2.6;
      for (const side of [-1, 1]) {
        const mid = PARK_PATH_HALF_W + 0.2 + benchSpan / 2;
        at(g, bench(benchSpan), zone.centre.x + side * mid, z, row === 1 ? Math.PI : 0);
      }
    }

    for (const side of [-1, 1]) {
      at(g, tree(side + 4), zone.centre.x + side * (zone.half.x - 0.9), zone.centre.z + zone.half.z - 0.9);
      at(g, lampPost(), zone.centre.x + side * (zone.half.x - 0.7), zone.centre.z - zone.half.z + 0.6);
      at(g, hedge(4.2, side + 1), zone.centre.x + side * 5.4, zone.centre.z - zone.half.z + 0.6);
      at(g, flowerBed(2.4, side + 2), zone.centre.x + side * 4.6, zone.centre.z + zone.half.z - 0.7);
    }
  },

  cafe(g, zone) {
    // Must stay in step with the `table` arrangement in zones.ts or guests sit on thin air.
    for (let i = 0; i < 6; i++) {
      const x = zone.centre.x + ((i % 3) - 1) * 6.0;
      const z = zone.centre.z + (Math.floor(i / 3) - 0.5) * 6.4;
      at(g, cafeTable(i), x, z);
      // Four chairs on the axes, matching the seat angles in `slotPosition`.
      for (let s = 0; s < 4; s++) {
        const a = (s / 4) * Math.PI * 2;
        at(g, chair(i * 4 + s), x + Math.cos(a) * 1.85, z + Math.sin(a) * 1.85, -a + Math.PI / 2);
      }
    }

    const backZ = zone.centre.z - zone.half.z;
    at(g, railing(zone.half.x * 2 - 0.4), zone.centre.x, backZ + 0.3);
    // The alley from the workshop arrives through this side, so the railing opens where the
    // road meets it. Turned a quarter turn, the run's own +x points at -z in the world.
    at(
      g,
      withDoorway((len) => railing(len), zone.half.z * 2 - 0.4, zone.centre.z - ALLEY_Z),
      zone.centre.x - zone.half.x + 0.3,
      zone.centre.z,
      Math.PI / 2,
    );
    at(g, potPlant(7), zone.centre.x - zone.half.x + 0.8, backZ + 0.9);
    at(g, potPlant(8), zone.centre.x + zone.half.x - 0.8, backZ + 0.9);
  },

  library(g, zone) {
    const backZ = zone.centre.z - zone.half.z;
    const leftX = zone.centre.x - zone.half.x;
    const h = zone.backWall!;
    g.add(place(wall(zone.half.z * 2, h, 0x3b3350), leftX, zone.centre.z, Math.PI / 2));
    g.add(place(gatedWall(zone.half.x * 2, h, 0x3b3350), zone.centre.x, backZ, 0));

    // Shelves face into the room, so the camera sees books rather than three backboards.
    // Pushed out to either side of the doorway in the back wall.
    for (let i = 0; i < 2; i++) {
      at(g, bookshelf(3.4, i), zone.centre.x + (i - 0.5) * (GATE_W + 3.8), backZ + 0.45);
    }
    at(g, bookshelf(3.0, 5), leftX + 0.45, zone.centre.z + 1.2, Math.PI / 2);
    at(g, readingLamp(), zone.centre.x + zone.half.x - 0.7, zone.centre.z + zone.half.z - 0.8);
  },

  infirmary(g, zone) {
    const backZ = zone.centre.z - zone.half.z;
    const leftX = zone.centre.x - zone.half.x;
    const h = zone.backWall!;
    g.add(place(wall(zone.half.z * 2, h, 0x4e3236), leftX, zone.centre.z, Math.PI / 2));
    g.add(place(gatedWall(zone.half.x * 2, h, 0x4e3236), zone.centre.x, backZ, 0));

    // Beds run along the back wall, broadside to the camera so they read as beds. Clear of
    // the doorway, which is in the middle of that wall.
    for (let i = 0; i < 2; i++) {
      at(g, hospitalBed(), zone.centre.x + (i - 0.5) * (GATE_W + 2.4), backZ + 0.72, Math.PI / 2);
    }
    at(g, ivStand(), zone.centre.x + GATE_W / 2 + 3.6, backZ + 1.4);
    // The cross hangs on the wall, so it moves to the segment beside the doorway.
    const sign = crossSign();
    sign.position.set(zone.centre.x - GATE_W / 2 - 1.4, 2.3, backZ + 0.18);
    g.add(sign);
    at(g, potPlant(9), zone.centre.x + zone.half.x - 0.7, zone.centre.z + zone.half.z - 0.7);
  },

  hotel(g, zone) {
    const backZ = zone.centre.z - zone.half.z;
    const leftX = zone.centre.x - zone.half.x;
    const h = zone.backWall!;
    g.add(place(wall(zone.half.z * 2, h, 0x2f4850), leftX, zone.centre.z, Math.PI / 2));
    g.add(place(gatedWall(zone.half.x * 2, h, 0x2f4850), zone.centre.x, backZ, 0));

    // Built from the positions the sleepers are derived from, so nobody dozes off beside
    // their bed. A nightstand between each pair, rather than one per bed, which is a wall of
    // furniture at this scale.
    for (let i = 0; i < HOTEL_BEDS; i++) {
      const spot = bedPosition(zone, i);
      at(g, bed(i), spot.x, spot.z);
      if (i % 2 === 0) at(g, nightstand(), spot.x + 1.35, spot.z - 0.75);
    }
    at(g, potPlant(11), zone.centre.x + zone.half.x - 0.8, backZ + 0.9);
    at(g, potPlant(12), leftX + 0.8, zone.centre.z + zone.half.z - 0.8);
  },

  door(g, zone) {
    at(g, archway(), zone.centre.x, zone.centre.z - 0.2);
    for (const side of [-1, 1]) {
      at(g, lampPost(), zone.centre.x + side * 3.4, zone.centre.z + 0.6);
      at(g, hedge(3.2, side + 20), zone.centre.x + side * 5.6, zone.centre.z - 0.2);
    }
  },
};

/**
 * A run of walling or railing with a doorway cut through it.
 *
 * The library and the infirmary meet the road through their back wall, and the cafe meets the
 * alley through its west railing, so without an opening those routes would run through solid
 * masonry. `gapAt` is measured along the run from its middle, so an off-centre doorway leaves
 * two pieces of different lengths rather than sliding the whole fence sideways.
 */
function withDoorway(make: (length: number) => THREE.Object3D, length: number, gapAt = 0): THREE.Group {
  const g = new THREE.Group();
  for (const side of [-1, 1]) {
    const outer = (side * length) / 2;
    const inner = gapAt + (side * GATE_W) / 2;
    const span = Math.abs(outer - inner);
    if (span < 0.1) continue; // the doorway reaches the end of the run; nothing left to build
    const piece = make(span);
    piece.position.x = (outer + inner) / 2;
    g.add(piece);
  }
  return g;
}

const gatedWall = (length: number, height: number, colour: number): THREE.Group =>
  withDoorway((len) => wall(len, height, colour), length);

function place(obj: THREE.Object3D, x: number, z: number, rotY: number): THREE.Object3D {
  obj.position.set(x, 0.26, z);
  obj.rotation.y = rotY;
  return obj;
}

function labelTexture(text: string): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext("2d")!;
  ctx.font = "600 62px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  // A dark halo, so the text stays readable over any floor colour under it.
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(10,12,17,0.75)";
  ctx.strokeText(text, 256, 64);
  ctx.fillStyle = "rgba(240,243,248,0.92)";
  ctx.fillText(text, 256, 64);
  return new THREE.CanvasTexture(canvas);
}

/**
 * The zone's name, standing over its own floor.
 *
 * A billboard rather than paint on the ground: the village is packed tightly enough that a
 * floor-level name is swallowed by whatever stands in front of it - the workshop's was cut
 * down to "hop" behind the library wall and the cafe's disappeared entirely. Drawn with the
 * depth test off for the same reason, so a name is never half-eaten by a roofline.
 */
function makeLabel(text: string, x: number, y: number, z: number): THREE.Sprite {
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: labelTexture(text),
      transparent: true,
      depthWrite: false,
      depthTest: false,
    }),
  );
  sprite.position.set(x, y, z);
  sprite.scale.set(6.4, 1.6, 1);
  sprite.renderOrder = 1;
  return sprite;
}
