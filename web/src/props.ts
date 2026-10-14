/**
 * Furniture.
 *
 * Every builder returns a group standing on y=0 facing +z, so callers only ever place and
 * rotate. Materials are shared through `mat` because the village puts a few hundred of these
 * on screen and they are nearly all the same handful of colours.
 */

import * as THREE from "three";

const MATERIALS = new Map<string, THREE.MeshStandardMaterial>();

export function mat(colour: number, roughness = 0.85, metalness = 0): THREE.MeshStandardMaterial {
  const key = `${colour}:${roughness}:${metalness}`;
  let material = MATERIALS.get(key);
  if (!material) {
    material = new THREE.MeshStandardMaterial({ color: colour, roughness, metalness });
    MATERIALS.set(key, material);
  }
  return material;
}

/**
 * A surface that lights up after dark: screens, lamps, windows.
 *
 * `glow` is picked up by the stage's day/night cycle, which is the only thing allowed to write
 * to `emissiveIntensity`.
 */
export function glowMat(colour: number, strength = 1): THREE.MeshStandardMaterial {
  const material = new THREE.MeshStandardMaterial({
    color: colour,
    emissive: new THREE.Color(colour),
    emissiveIntensity: 0.4,
    roughness: 0.4,
  });
  material.userData.glow = strength;
  return material;
}

function part(geometry: THREE.BufferGeometry, material: THREE.Material, x = 0, y = 0, z = 0): THREE.Mesh {
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

export function box(w: number, h: number, d: number, colour: number, x = 0, y = 0, z = 0): THREE.Mesh {
  return part(new THREE.BoxGeometry(w, h, d), mat(colour), x, y, z);
}

function cyl(rt: number, rb: number, h: number, colour: number, x = 0, y = 0, z = 0, seg = 12): THREE.Mesh {
  return part(new THREE.CylinderGeometry(rt, rb, h, seg), mat(colour), x, y, z);
}

const WOOD = 0x8a6a46;
const DARK_WOOD = 0x5f472e;
const METAL = 0x707889;
const WHITE = 0xe8ecf2;

/** Deterministic per-instance variation, so two desks are not pixel-identical. */
function wobble(seed: number): number {
  const s = Math.sin(seed * 12.9898) * 43758.5453;
  return s - Math.floor(s);
}

// -- workshop ---------------------------------------------------------------------------

export function desk(seed = 0): THREE.Group {
  const g = new THREE.Group();
  const w = 2.3;

  g.add(box(w, 0.09, 1.0, 0x9aa3b5, 0, 0.74, 0));
  g.add(box(0.09, 0.7, 0.92, 0x6b7384, -w / 2 + 0.1, 0.37, 0));
  g.add(box(0.09, 0.7, 0.92, 0x6b7384, w / 2 - 0.1, 0.37, 0));
  g.add(box(w - 0.3, 0.06, 0.4, 0x6b7384, 0, 0.5, -0.2));

  // Monitor, tilted back a touch and facing the near side of the room.
  const stand = box(0.12, 0.22, 0.12, 0x3a4150, 0, 0.88, -0.22);
  g.add(stand);
  const panel = new THREE.Group();
  panel.position.set(0, 1.28, -0.22);
  panel.rotation.x = -0.12;
  panel.add(box(1.15, 0.68, 0.06, 0x232834));
  const screen = part(new THREE.PlaneGeometry(1.0, 0.54), glowMat(wobble(seed) > 0.5 ? 0x6fa8ff : 0x7fd8b0, 1.4));
  screen.position.z = 0.035;
  screen.castShadow = false;
  panel.add(screen);
  g.add(panel);

  g.add(box(0.62, 0.03, 0.22, 0x2b303c, 0, 0.8, 0.26));
  g.add(cyl(0.09, 0.09, 0.14, 0xd6d9e0, 0.72, 0.85, 0.22, 10));
  if (wobble(seed + 3) > 0.45) g.add(box(0.26, 0.05, 0.34, 0xc0724a, -0.7, 0.81, 0.2));

  g.add(chair(0, 0.95));
  return g;
}

export function chair(seed = 0, z = 0): THREE.Group {
  const g = new THREE.Group();
  g.position.z = z;
  g.rotation.y = (wobble(seed + 11) - 0.5) * 0.5;
  g.add(box(0.52, 0.08, 0.52, 0x4a5265, 0, 0.44, 0));
  g.add(box(0.52, 0.5, 0.08, 0x4a5265, 0, 0.72, -0.22));
  for (const [x, zz] of [[-0.2, -0.2], [0.2, -0.2], [-0.2, 0.2], [0.2, 0.2]] as const) {
    g.add(cyl(0.035, 0.035, 0.42, METAL, x, 0.21, zz, 6));
  }
  return g;
}

export function whiteboard(width = 3): THREE.Group {
  const g = new THREE.Group();
  g.add(box(width, 1.7, 0.08, 0xd8dce4, 0, 1.6, 0));
  g.add(box(width + 0.14, 0.1, 0.12, 0x3a4150, 0, 0.72, 0.01));
  // A few scribbles so it does not read as a blank slab.
  for (let i = 0; i < 4; i++) {
    g.add(box(width * (0.2 + wobble(i) * 0.4), 0.05, 0.02, 0x5a6378, -width * 0.2, 2.1 - i * 0.3, 0.05));
  }
  return g;
}

// -- park -------------------------------------------------------------------------------

export function bench(length = 3.4): THREE.Group {
  const g = new THREE.Group();
  for (let i = 0; i < 3; i++) g.add(box(length, 0.07, 0.17, WOOD, 0, 0.46, -0.22 + i * 0.21));
  const back = new THREE.Group();
  back.position.set(0, 0.78, -0.34);
  back.rotation.x = -0.18;
  for (let i = 0; i < 2; i++) back.add(box(length, 0.17, 0.06, WOOD, 0, i * 0.26, 0));
  g.add(back);
  for (const x of [-length / 2 + 0.25, length / 2 - 0.25]) {
    g.add(box(0.1, 0.44, 0.62, 0x3f4653, x, 0.22, 0));
    g.add(box(0.1, 0.46, 0.1, 0x3f4653, x, 0.68, -0.3));
  }
  return g;
}

export function tree(seed = 0): THREE.Group {
  const g = new THREE.Group();
  const h = 1.5 + wobble(seed) * 0.8;
  g.add(cyl(0.16, 0.28, h * 1.3, DARK_WOOD, 0, (h * 1.3) / 2, 0, 8));
  const greens = [0x4f8757, 0x5e9a63, 0x447a4e];
  for (let i = 0; i < 3; i++) {
    const r = (1.45 - i * 0.33) * (0.85 + wobble(seed + i) * 0.35);
    const blob = part(
      new THREE.IcosahedronGeometry(r, 0),
      mat(greens[(seed + i) % greens.length]!, 0.95),
      (wobble(seed + i * 5) - 0.5) * 0.5,
      h * 1.3 + 0.2 + i * r * 0.95,
      (wobble(seed + i * 9) - 0.5) * 0.5,
    );
    blob.rotation.set(wobble(seed + i) * 3, wobble(seed + i * 2) * 3, 0);
    g.add(blob);
  }
  return g;
}

export function hedge(length: number, seed = 0): THREE.Group {
  const g = new THREE.Group();
  const n = Math.max(2, Math.round(length / 1.1));
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1) - 0.5) * length;
    const r = 0.52 + wobble(seed + i) * 0.14;
    g.add(part(new THREE.IcosahedronGeometry(r, 0), mat(0x3f6e4a, 0.95), x, 0.42, 0));
  }
  return g;
}

export function lampPost(): THREE.Group {
  const g = new THREE.Group();
  g.add(cyl(0.22, 0.3, 0.16, 0x2f3542, 0, 0.08, 0, 10));
  g.add(cyl(0.07, 0.09, 3.2, 0x39404f, 0, 1.6, 0, 8));
  g.add(part(new THREE.SphereGeometry(0.26, 12, 10), glowMat(0xffd9a0, 2.2), 0, 3.3));
  g.add(cyl(0.3, 0.1, 0.2, 0x39404f, 0, 3.58, 0, 10));
  return g;
}

export function flowerBed(width: number, seed = 0): THREE.Group {
  const g = new THREE.Group();
  g.add(box(width, 0.22, 1.0, DARK_WOOD, 0, 0.11, 0));
  const petals = [0xe06a8c, 0xf0c05a, 0xc87fe0, 0xe88a52];
  const n = Math.round(width * 2.2);
  for (let i = 0; i < n; i++) {
    const x = (wobble(seed + i) - 0.5) * (width - 0.4);
    const z = (wobble(seed + i * 3) - 0.5) * 0.6;
    g.add(part(new THREE.SphereGeometry(0.1, 7, 6), mat(petals[(seed + i) % petals.length]!, 0.8), x, 0.32, z));
  }
  return g;
}

// -- cafe -------------------------------------------------------------------------------

export function cafeTable(seed = 0): THREE.Group {
  const g = new THREE.Group();
  g.add(cyl(0.45, 0.52, 0.07, 0x3d4350, 0, 0.04, 0, 14));
  g.add(cyl(0.1, 0.12, 0.68, METAL, 0, 0.38, 0, 10));
  g.add(cyl(0.95, 0.95, 0.09, 0xa9825c, 0, 0.75, 0, 20));
  g.add(cyl(0.98, 0.98, 0.03, 0xbb9068, 0, 0.8, 0, 20));

  // A cup or two, placed off-centre so tables do not look stamped.
  const cups = 1 + Math.round(wobble(seed) * 2);
  for (let i = 0; i < cups; i++) {
    const a = wobble(seed + i * 7) * Math.PI * 2;
    const r = 0.3 + wobble(seed + i) * 0.3;
    g.add(cyl(0.085, 0.07, 0.14, WHITE, Math.cos(a) * r, 0.88, Math.sin(a) * r, 10));
  }
  return g;
}

export function railing(length: number): THREE.Group {
  const g = new THREE.Group();
  g.add(box(length, 0.09, 0.09, 0x6a5540, 0, 0.92, 0));
  g.add(box(length, 0.07, 0.07, 0x6a5540, 0, 0.5, 0));
  const n = Math.max(2, Math.round(length / 1.6));
  for (let i = 0; i < n; i++) {
    g.add(box(0.1, 0.95, 0.1, 0x6a5540, (i / (n - 1) - 0.5) * length, 0.48, 0));
  }
  return g;
}

// -- library ----------------------------------------------------------------------------

export function bookshelf(width = 2.6, seed = 0): THREE.Group {
  const g = new THREE.Group();
  const h = 2.5;
  const depth = 0.5;
  g.add(box(width, h, 0.07, DARK_WOOD, 0, h / 2, -depth / 2));
  g.add(box(0.08, h, depth, WOOD, -width / 2, h / 2, 0));
  g.add(box(0.08, h, depth, WOOD, width / 2, h / 2, 0));
  g.add(box(width, 0.08, depth, WOOD, 0, h, 0));

  const spines = [0x8c4a46, 0x3f6e8c, 0x6a7a3f, 0x8a6a46, 0x6b4a7a, 0xa8803c];
  for (let shelf = 0; shelf < 4; shelf++) {
    const y = 0.28 + shelf * 0.58;
    g.add(box(width - 0.14, 0.05, depth - 0.06, WOOD, 0, y, 0));
    let x = -width / 2 + 0.16;
    let i = 0;
    while (x < width / 2 - 0.2) {
      const t = 0.07 + wobble(seed + shelf * 17 + i) * 0.09;
      const bh = 0.32 + wobble(seed + shelf + i * 3) * 0.14;
      g.add(box(t, bh, depth - 0.18, spines[(shelf + i + seed) % spines.length]!, x + t / 2, y + bh / 2 + 0.03, 0.02));
      x += t + 0.015;
      i++;
    }
  }
  return g;
}

export function readingLamp(): THREE.Group {
  const g = new THREE.Group();
  g.add(cyl(0.2, 0.26, 0.07, 0x2f3542, 0, 0.04, 0, 10));
  g.add(cyl(0.035, 0.035, 1.5, 0x4a5265, 0, 0.75, 0, 8));
  g.add(part(new THREE.ConeGeometry(0.32, 0.36, 12, 1, true), mat(0x6b4a7a, 0.8), 0, 1.62));
  g.add(part(new THREE.SphereGeometry(0.12, 8, 6), glowMat(0xffe0b0, 1.8), 0, 1.5));
  return g;
}

// -- infirmary --------------------------------------------------------------------------

export function hospitalBed(): THREE.Group {
  const g = new THREE.Group();
  g.add(box(1.05, 0.1, 2.1, METAL, 0, 0.44, 0));
  for (const [x, z] of [[-0.44, -0.9], [0.44, -0.9], [-0.44, 0.9], [0.44, 0.9]] as const) {
    g.add(cyl(0.04, 0.04, 0.4, METAL, x, 0.2, z, 6));
  }
  g.add(box(0.98, 0.18, 1.98, WHITE, 0, 0.58, 0));
  g.add(box(0.66, 0.15, 0.36, 0xf2f5f9, 0, 0.73, -0.74));
  g.add(box(1.0, 0.09, 1.15, 0x7fa8c4, 0, 0.71, 0.4));
  g.add(box(1.05, 0.5, 0.07, METAL, 0, 0.72, -1.05));
  g.add(box(1.05, 0.34, 0.07, METAL, 0, 0.64, 1.05));
  return g;
}

// -- hotel ------------------------------------------------------------------------------

/**
 * A hotel bed, with the mattress at chair height.
 *
 * That is not a detail: sitting is handled by one rule shared across the whole village, and a
 * bed any taller would leave its occupant hovering above the covers.
 */
export function bed(seed = 0): THREE.Group {
  const g = new THREE.Group();
  const quilt = [0x5f7f8c, 0x7a6a8c, 0x8c7a5f, 0x5f8c74][seed % 4]!;

  g.add(box(1.2, 0.26, 2.2, DARK_WOOD, 0, 0.15, 0));
  g.add(box(1.1, 0.18, 2.05, 0xe8e3d8, 0, 0.39, 0));
  g.add(box(1.12, 0.1, 1.2, quilt, 0, 0.52, 0.45));
  g.add(box(0.68, 0.14, 0.36, 0xf4f1ea, 0, 0.55, -0.78));
  g.add(box(1.2, 0.62, 0.09, WOOD, 0, 0.57, -1.14));
  for (const x of [-0.5, 0.5]) {
    for (const z of [-1.0, 1.0]) g.add(box(0.1, 0.16, 0.1, DARK_WOOD, x, 0.08, z));
  }
  return g;
}

/** Bedside table with a lamp on it, so a row of beds is not just a row of slabs. */
export function nightstand(): THREE.Group {
  const g = new THREE.Group();
  g.add(box(0.5, 0.05, 0.44, WOOD, 0, 0.52, 0));
  g.add(box(0.44, 0.44, 0.38, DARK_WOOD, 0, 0.28, 0));
  g.add(cyl(0.05, 0.07, 0.22, 0x4a5265, 0, 0.65, 0, 8));
  g.add(part(new THREE.ConeGeometry(0.17, 0.2, 10, 1, true), mat(0xd8b98a, 0.8), 0, 0.84));
  g.add(part(new THREE.SphereGeometry(0.07, 8, 6), glowMat(0xffd9a0, 1.5), 0, 0.78));
  return g;
}

export function ivStand(): THREE.Group {
  const g = new THREE.Group();
  g.add(cyl(0.22, 0.26, 0.06, METAL, 0, 0.03, 0, 10));
  g.add(cyl(0.03, 0.03, 1.9, METAL, 0, 0.95, 0, 6));
  g.add(box(0.2, 0.3, 0.1, 0xbfe0d2, 0.08, 1.72, 0));
  return g;
}

export function crossSign(): THREE.Group {
  const g = new THREE.Group();
  g.add(box(0.9, 0.26, 0.08, 0xe8646e, 0, 0, 0));
  g.add(box(0.26, 0.9, 0.08, 0xe8646e, 0, 0, 0));
  return g;
}

// -- door -------------------------------------------------------------------------------

export function archway(): THREE.Group {
  const g = new THREE.Group();
  for (const x of [-1.9, 1.9]) {
    g.add(box(0.55, 3.3, 0.6, 0x47506a, x, 1.65, 0));
    g.add(box(0.7, 0.18, 0.75, 0x5a647e, x, 3.38, 0));
  }
  g.add(box(4.9, 0.55, 0.7, 0x47506a, 0, 3.72, 0));
  g.add(part(new THREE.SphereGeometry(0.18, 10, 8), glowMat(0xffd9a0, 2.0), 0, 3.3, 0.42));
  // Steps down into the village.
  for (let i = 0; i < 3; i++) g.add(box(4.6 - i * 0.5, 0.12, 0.5, 0x3a4154, 0, 0.06 + i * 0.12, 1.1 + i * 0.5));
  return g;
}

// -- shared -----------------------------------------------------------------------------

/**
 * A wall with window openings.
 *
 * Only ever used on a zone's far edges. A wall on a near edge would stand between the camera
 * and the avatars it is supposed to be showing.
 */
export function wall(length: number, height = 3.0, colour = 0x39404f, windows = true): THREE.Group {
  const g = new THREE.Group();
  g.add(box(length, height, 0.3, colour, 0, height / 2, 0));
  g.add(box(length + 0.2, 0.18, 0.46, 0x2b313d, 0, height, 0));
  if (windows) {
    const n = Math.max(1, Math.floor(length / 3.2));
    for (let i = 0; i < n; i++) {
      const x = (i - (n - 1) / 2) * 3.2;
      g.add(box(1.7, 1.1, 0.12, 0x2b313d, x, height * 0.62, 0.16));
      const pane = part(new THREE.PlaneGeometry(1.5, 0.92), glowMat(0xffd9a0, 1.6), x, height * 0.62, 0.23);
      pane.castShadow = false;
      g.add(pane);
    }
  }
  return g;
}

export function potPlant(seed = 0): THREE.Group {
  const g = new THREE.Group();
  g.add(cyl(0.3, 0.22, 0.42, 0xb0724e, 0, 0.21, 0, 10));
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + wobble(seed) * 2;
    const blade = part(new THREE.ConeGeometry(0.12, 0.9 + wobble(seed + i) * 0.5, 6), mat(0x4f8757, 0.95));
    blade.position.set(Math.cos(a) * 0.12, 0.85, Math.sin(a) * 0.12);
    blade.rotation.z = Math.cos(a) * 0.3;
    blade.rotation.x = -Math.sin(a) * 0.3;
    g.add(blade);
  }
  return g;
}
