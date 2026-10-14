/** Avatars: one little person per agent, walking between zones. */

import * as THREE from "three";

import { advanceRoute, routeTo } from "./paths";
import type { Agent, AgentState } from "./types";
import { STATE_COLOUR, zoneFor, type Slot, type Vec2 } from "./zones";

const WALK_SPEED = 6.5; // units per second
const ARRIVE = 0.12;

/** Hip and shoulder pivots, as a fraction of the avatar's height. */
const HIP_Y = 0.62;
const SHOULDER_Y = 1.24;

export interface Avatar {
  group: THREE.Group;
  agent: Agent;
  target: Slot;
  /**
   * Gates and junctions still to pass through before the target, from `paths.ts`.
   *
   * Empty means walk straight there, which is right inside a zone and wrong between them -
   * the straight line from the cafe to the workshop goes through the cafe railing.
   */
  route: Vec2[];
  /** Set while the avatar is still walking, so animations can tell moving from arrived. */
  moving: boolean;
  bubble: THREE.Sprite;
  marker: THREE.Mesh;
  body: THREE.Mesh;
  head: THREE.Mesh;
  tool: THREE.Mesh;
  /** Joint groups, rotated about their pivot to swing. */
  limbs: { armL: THREE.Group; armR: THREE.Group; legL: THREE.Group; legR: THREE.Group };
}

/** A prop in the avatar's hand, so what it is doing reads from across the room. */
const TOOL_PROP: Record<string, { colour: number; shape: "box" | "sphere" | "cone" }> = {
  Bash: { colour: 0x2b2f38, shape: "box" },
  Shell: { colour: 0x2b2f38, shape: "box" },
  Edit: { colour: 0xc08a4a, shape: "box" },
  Write: { colour: 0xc08a4a, shape: "box" },
  Read: { colour: 0x7fb3ff, shape: "sphere" },
  Grep: { colour: 0x7fb3ff, shape: "sphere" },
  Glob: { colour: 0x7fb3ff, shape: "sphere" },
  WebFetch: { colour: 0x9ad6b0, shape: "cone" },
  WebSearch: { colour: 0x9ad6b0, shape: "cone" },
  Task: { colour: 0xd2a8ff, shape: "cone" },
};

/**
 * A limb hung from a pivot.
 *
 * The mesh sits below the group's origin so rotating the group swings the limb from the joint
 * rather than spinning it about its own middle.
 */
function limb(
  radius: number,
  length: number,
  colour: THREE.ColorRepresentation,
  x: number,
  y: number,
): THREE.Group {
  const joint = new THREE.Group();
  joint.position.set(x, y, 0);
  const mesh = new THREE.Mesh(
    new THREE.CapsuleGeometry(radius, length, 3, 8),
    new THREE.MeshStandardMaterial({ color: colour, roughness: 0.75 }),
  );
  mesh.position.y = -length / 2 - radius * 0.5;
  mesh.castShadow = true;
  joint.add(mesh);
  return joint;
}

export function createAvatar(agent: Agent, at: Vec2): Avatar {
  const look = agent.appearance;
  const group = new THREE.Group();
  group.position.set(at.x, 0.26, at.z);

  const scale = agent.isSubagent ? 0.68 : 1;
  const h = look.height * scale;
  const skin = 0xe8c9a8;
  const trousers = 0x37405a;

  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.3 * look.girth * scale, 0.52 * h, 4, 12),
    new THREE.MeshStandardMaterial({ color: look.body, roughness: 0.7 }),
  );
  body.position.y = 0.95 * h;
  body.castShadow = true;
  group.add(body);

  // Collar, so the head does not look screwed straight onto the torso.
  const neck = new THREE.Mesh(
    new THREE.CylinderGeometry(0.12 * scale, 0.15 * scale, 0.12 * scale, 8),
    new THREE.MeshStandardMaterial({ color: skin, roughness: 0.8 }),
  );
  neck.position.y = 1.3 * h;
  group.add(neck);

  const head = new THREE.Mesh(
    new THREE.SphereGeometry(0.28 * scale, 18, 14),
    new THREE.MeshStandardMaterial({ color: skin, roughness: 0.8 }),
  );
  head.position.y = 1.52 * h;
  head.castShadow = true;
  group.add(head);

  // Hair always, hat on top of it. A bare sphere reads as a mannequin.
  const hair = new THREE.Mesh(
    new THREE.SphereGeometry(0.29 * scale, 14, 10, 0, Math.PI * 2, 0, Math.PI * 0.62),
    new THREE.MeshStandardMaterial({ color: look.hair, roughness: 0.9 }),
  );
  hair.position.y = head.position.y + 0.02;
  group.add(hair);

  const eyeMat = new THREE.MeshStandardMaterial({ color: 0x222733, roughness: 0.5 });
  for (const side of [-1, 1]) {
    const eye = new THREE.Mesh(new THREE.SphereGeometry(0.038 * scale, 8, 6), eyeMat);
    eye.position.set(side * 0.1 * scale, head.position.y + 0.02, 0.255 * scale);
    group.add(eye);
  }

  if (look.hat !== "none") {
    const hat = new THREE.Mesh(
      look.hat === "cap"
        ? new THREE.CylinderGeometry(0.3, 0.32, 0.18, 14)
        : look.hat === "beanie"
          ? new THREE.SphereGeometry(0.32, 14, 9, 0, Math.PI * 2, 0, Math.PI / 2)
          : look.hat === "band"
            ? new THREE.TorusGeometry(0.29, 0.05, 6, 18)
            : new THREE.ConeGeometry(0.24, 0.36, 12),
      new THREE.MeshStandardMaterial({ color: look.hair, roughness: 0.8 }),
    );
    hat.position.y = head.position.y + 0.24 * scale;
    hat.scale.setScalar(scale);
    if (look.hat === "band") hat.rotation.x = Math.PI / 2;
    hat.castShadow = true;
    group.add(hat);
  }

  // Sleeves a shade darker than the torso. Same colour and the arms vanish into the body at
  // this zoom, which is what makes an avatar read as a capsule rather than a person.
  const sleeve = new THREE.Color(look.body).multiplyScalar(0.78);
  const armL = limb(0.085 * scale, 0.44 * h, sleeve, -0.32 * look.girth * scale, SHOULDER_Y * h);
  const armR = limb(0.085 * scale, 0.44 * h, sleeve, 0.32 * look.girth * scale, SHOULDER_Y * h);
  const legL = limb(0.105 * scale, 0.46 * h, trousers, -0.13 * scale, HIP_Y * h);
  const legR = limb(0.105 * scale, 0.46 * h, trousers, 0.13 * scale, HIP_Y * h);
  group.add(armL, armR, legL, legR);

  for (const leg of [legL, legR]) {
    const shoe = new THREE.Mesh(
      new THREE.BoxGeometry(0.19 * scale, 0.1 * scale, 0.28 * scale),
      new THREE.MeshStandardMaterial({ color: 0x242a36, roughness: 0.9 }),
    );
    shoe.position.set(0, -0.52 * h, 0.05 * scale);
    shoe.castShadow = true;
    leg.add(shoe);
  }

  // Held tool, hidden until the agent actually picks something up. Parented to the right hand
  // so it swings with the arm instead of floating alongside.
  const tool = new THREE.Mesh(
    new THREE.BoxGeometry(0.24, 0.24, 0.24),
    new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.6 }),
  );
  tool.position.set(0, -0.5 * h, 0.12);
  tool.scale.setScalar(scale);
  tool.visible = false;
  tool.castShadow = true;
  armR.add(tool);

  // The floor ring is the state colour: readable even when the avatar is behind a prop.
  const marker = new THREE.Mesh(
    new THREE.RingGeometry(0.42, 0.56, 24),
    new THREE.MeshBasicMaterial({
      color: STATE_COLOUR[agent.state],
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
    }),
  );
  marker.rotation.x = -Math.PI / 2;
  marker.position.y = 0.02;
  group.add(marker);

  const bubble = makeBubble("");
  bubble.position.y = 2.3 * h;
  bubble.visible = false;
  group.add(bubble);

  return {
    group,
    agent,
    target: at,
    route: [],
    moving: false,
    bubble,
    marker,
    body,
    head,
    tool,
    limbs: { armL, armR, legL, legR },
  };
}

export function updateAvatar(avatar: Avatar, agent: Agent, target: Slot): void {
  const moved = target.x !== avatar.target.x || target.z !== avatar.target.z;
  avatar.agent = agent;
  avatar.target = target;
  // Re-route only when the destination actually changes. Doing it every frame would reset
  // progress through the current leg and leave the avatar twitching at the first gate.
  if (moved) {
    const from = avatar.group.position;
    avatar.route = routeTo({ x: from.x, z: from.z }, zoneFor(agent));
  }
  (avatar.marker.material as THREE.MeshBasicMaterial).color.setHex(STATE_COLOUR[agent.state]);

  const glyph = bubbleGlyph(agent);
  if (glyph) {
    setBubble(avatar.bubble, glyph.text, glyph.colour);
    avatar.bubble.visible = true;
  } else {
    avatar.bubble.visible = false;
  }

  const toolName = agent.currentTool ?? agent.openTools[0]?.name ?? null;
  const prop = toolName ? TOOL_PROP[toolName] ?? { colour: 0x9aa3b5, shape: "box" as const } : null;
  avatar.tool.visible = prop !== null && agent.state === "working";
  if (prop) (avatar.tool.material as THREE.MeshStandardMaterial).color.setHex(prop.colour);
}

/**
 * The speech bubble glyph.
 *
 * A reported permission prompt gets a solid "!"; an inferred one gets "?". A guess must never
 * look like a fact - that distinction is the whole reason `waitingInferred` exists.
 */
function bubbleGlyph(agent: Agent): { text: string; colour: string } | null {
  switch (agent.state) {
    case "waiting":
      return agent.waitingInferred ? { text: "?", colour: "#ffd089" } : { text: "!", colour: "#ffb43d" };
    case "blocked":
      return { text: "\u2715", colour: "#ff9aa2" };
    case "compacting":
      return { text: "z", colour: "#d2a8ff" };
    case "thinking":
      return { text: "\u2026", colour: "#b0b4ff" };
    case "done":
      return agent.needsAttention ? { text: "\u2713", colour: "#87e6b0" } : null;
    default:
      return null;
  }
}

export function stepAvatar(avatar: Avatar, dt: number, elapsed: number): void {
  const pos = avatar.group.position;

  avatar.route = advanceRoute(pos, avatar.route);

  const goal = avatar.route[0] ?? avatar.target;
  const dx = goal.x - pos.x;
  const dz = goal.z - pos.z;
  const dist = Math.hypot(dx, dz);

  avatar.moving = avatar.route.length > 0 || dist > ARRIVE;
  if (dist > 1e-4) {
    const step = Math.min(dist, WALK_SPEED * dt);
    pos.x += (dx / dist) * step;
    pos.z += (dz / dist) * step;
    avatar.group.rotation.y = Math.atan2(dx, dz);
  }

  const look = avatar.agent.appearance;
  const { armL, armR, legL, legR } = avatar.limbs;
  const phase = elapsed * 9 + look.gaitPhase * Math.PI * 2;

  if (avatar.moving) {
    // Walk: contralateral swing, plus the bob that comes from the stride.
    const swing = Math.sin(phase);
    legL.rotation.x = swing * 0.75;
    legR.rotation.x = -swing * 0.75;
    armL.rotation.x = -swing * 0.55;
    armR.rotation.x = swing * 0.55;
    pos.y = 0.26 + Math.abs(Math.sin(phase)) * 0.07;
    avatar.group.rotation.z = Math.sin(phase) * 0.03;
    avatar.body.rotation.x = 0.06;
  } else {
    pos.y = 0.26;
    avatar.group.rotation.z = 0;
    if (avatar.target.facing !== undefined) avatar.group.rotation.y = avatar.target.facing;
    idlePose(avatar, elapsed);
  }

  // Breathing, so nobody looks like a statue.
  avatar.head.position.y += Math.sin(elapsed * 2 + look.gaitPhase) * 0.0012;

  if (avatar.bubble.visible) {
    avatar.bubble.position.y = 2.3 * look.height + Math.sin(elapsed * 3) * 0.08;
  }
}

/** What an avatar does once it has arrived. One pose per state, readable at a glance. */
function idlePose(avatar: Avatar, elapsed: number): void {
  const { armL, armR, legL, legR } = avatar.limbs;
  const look = avatar.agent.appearance;
  const sway = Math.sin(elapsed * 1.6 + look.gaitPhase * 6);
  // Reset every joint each frame: poses set different subsets, and a leftover rotation from
  // the previous state is how you end up with an avatar permanently holding its chin.
  legL.rotation.set(0, 0, 0);
  legR.rotation.set(0, 0, 0);
  armL.rotation.set(0, 0, 0);
  armR.rotation.set(0, 0, 0);

  // Sitting is a property of the slot, not of the state: the same "working" agent stands on
  // the open floor and sits once a desk frees up.
  if (avatar.target.seated) {
    avatar.group.position.y = 0.26 - 0.17;
    legL.rotation.x = -1.5;
    legR.rotation.x = -1.5;
  }

  switch (avatar.agent.state) {
    case "working": {
      // Typing: forearms up and out in front, shoulders jittering.
      const tap = Math.sin(elapsed * 14);
      avatar.body.rotation.x = 0.08 + tap * 0.03;
      armL.rotation.x = -1.15 + tap * 0.12;
      armR.rotation.x = -1.15 - tap * 0.12;
      break;
    }
    case "thinking":
      // Hand to chin.
      avatar.body.rotation.x = 0.04;
      armL.rotation.x = -0.1;
      armR.rotation.x = -2.3;
      armR.rotation.z = -0.5 + sway * 0.05;
      break;
    case "waiting":
      // An impatient sway. Whether there is a bench, and which way it points, is the layout's
      // business, not this function's.
      avatar.body.rotation.x = 0.05 + sway * 0.05;
      armL.rotation.x = -0.35;
      armR.rotation.x = -0.35;
      break;
    case "blocked":
      // Flat out, arms at the sides.
      avatar.body.rotation.x = 0.4;
      armL.rotation.x = 0.25;
      armR.rotation.x = 0.25;
      armL.rotation.z = 0.3;
      armR.rotation.z = -0.3;
      break;
    case "compacting":
      // Reading: head down, arms holding something at chest height.
      avatar.body.rotation.x = 0.18;
      armL.rotation.x = -1.0;
      armR.rotation.x = -1.0;
      break;
    case "done":
      // Relaxed, one arm resting on the table.
      avatar.body.rotation.x = 0.02 + sway * 0.02;
      armL.rotation.x = -0.15 + sway * 0.06;
      armR.rotation.x = -0.9;
      break;
    case "sleeping":
      // Dozed off propped against the pillow: tipped back, arms down on the covers, and a
      // slower sway than anybody still awake.
      avatar.body.rotation.x = -0.62 + sway * 0.015;
      armL.rotation.x = -0.2;
      armR.rotation.x = -0.2;
      armL.rotation.z = 0.42;
      armR.rotation.z = -0.42;
      break;
    default:
      avatar.body.rotation.x = 0;
      armL.rotation.x = sway * 0.05;
      armR.rotation.x = -sway * 0.05;
  }
}

function makeBubble(text: string): THREE.Sprite {
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ transparent: true, depthTest: false, depthWrite: false }),
  );
  sprite.scale.set(1.1, 1.1, 1);
  sprite.renderOrder = 10;
  setBubble(sprite, text, "#ffffff");
  return sprite;
}

function setBubble(sprite: THREE.Sprite, text: string, colour: string): void {
  const material = sprite.material as THREE.SpriteMaterial;
  if (material.userData.text === text && material.userData.colour === colour) return;
  material.userData.text = text;
  material.userData.colour = colour;

  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "rgba(17,19,26,0.92)";
  ctx.beginPath();
  ctx.arc(64, 56, 42, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(52, 92);
  ctx.lineTo(64, 120);
  ctx.lineTo(76, 92);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = colour;
  ctx.font = "700 56px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, 64, 58);

  material.map?.dispose();
  material.map = new THREE.CanvasTexture(canvas);
  material.needsUpdate = true;
}

/** The Sims plumbob, floating over whichever avatar is selected. */
export function createPlumbob(): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.OctahedronGeometry(0.3),
    new THREE.MeshBasicMaterial({ color: 0x66ff88, transparent: true, opacity: 0.85 }),
  );
  mesh.visible = false;
  return mesh;
}

export function stateColourCss(state: AgentState): string {
  return `#${STATE_COLOUR[state].toString(16).padStart(6, "0")}`;
}
