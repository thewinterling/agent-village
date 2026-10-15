/** Wire the stage, the avatars, the HUD and the socket together. */

import * as THREE from "three";

import { createAvatar, createPlumbob, stepAvatar, updateAvatar, type Avatar } from "./avatars";
import { Hud } from "./hud";
import { renderList } from "./list";
import { connect } from "./net";
import { Chime } from "./sound";
import type { Agent, AgentState, WorldSnapshot } from "./types";
import { createStage } from "./world";
import { ZoneInfo } from "./zoneinfo";
import { routeTo } from "./paths";
import { layout, zoneFor, type Seat } from "./zones";

const canvas = document.getElementById("stage") as HTMLCanvasElement;
const stage = createStage(canvas);
const zoneInfo = new ZoneInfo(document.getElementById("zone-layer")!);
const chime = new Chime();

const plumbob = createPlumbob();
stage.scene.add(plumbob);

const avatars = new Map<string, Avatar>();
const previousState = new Map<string, AgentState>();
const seats = new Map<string, Seat>();
/** New arrivals walk in through the front door rather than materialising mid-room. */
const SPAWN = { x: 0, z: 19 };
let latest: WorldSnapshot | null = null;

const hud = new Hud(() => {
  plumbob.visible = false;
});

// -- list view ------------------------------------------------------------------------

const listEl = document.getElementById("list")!;
const viewToggle = document.getElementById("view-toggle")!;

function showList(on: boolean): void {
  document.body.classList.toggle("list", on);
  viewToggle.textContent = on ? "village view" : "list view";
  try {
    localStorage.setItem("view", on ? "list" : "village");
  } catch {
    // Private windows can refuse storage; the choice just will not survive a reload.
  }
  if (on && latest) renderList(listEl, latest.agents, hud.selected);
}

const listShown = (): boolean => document.body.classList.contains("list");

viewToggle.addEventListener("click", () => showList(!listShown()));
listEl.addEventListener("click", (e) => {
  const id = (e.target as HTMLElement).closest("tr")?.dataset.id;
  if (id) void hud.openDrawer(id);
});
try {
  showList(localStorage.getItem("view") === "list");
} catch {
  showList(false);
}

// -- input ----------------------------------------------------------------------------

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let pointerPx = { x: 0, y: 0 };
let hovered: string | null = null;
/**
 * Whether the cursor is actually over the village.
 *
 * Without this the picker keeps raycasting through the last known cursor position after the
 * pointer has moved onto the HUD, so the avatar card gets redrawn every frame and stamps on
 * whatever the panel underneath is trying to show. It also stops the untouched default of
 * (0,0) - dead centre of the screen - from picking an avatar before the mouse has moved.
 */
let pointerOverCanvas = false;

canvas.addEventListener("pointermove", (e) => {
  pointerOverCanvas = true;
  pointerPx = { x: e.clientX, y: e.clientY };
  pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(e.clientY / window.innerHeight) * 2 + 1;
});

canvas.addEventListener("pointerleave", () => {
  pointerOverCanvas = false;
  hovered = null;
  hud.hideHover();
});

canvas.addEventListener("click", () => {
  chime.unlock();
  // Dragging the view across an avatar must not also open it.
  if (stage.consumeDrag()) return;
  if (hovered) void hud.openDrawer(hovered);
  else hud.closeDrawer();
});

document.getElementById("reset-view")?.addEventListener("click", () => stage.resetView());

document.getElementById("sound-toggle")?.addEventListener("click", (e) => {
  chime.unlock();
  chime.enabled = !chime.enabled;
  (e.currentTarget as HTMLElement).classList.toggle("on", chime.enabled);
  (e.currentTarget as HTMLElement).textContent = chime.enabled ? "sound on" : "sound off";
});

function pickAgent(): string | null {
  raycaster.setFromCamera(pointer, stage.camera);
  const meshes: THREE.Object3D[] = [];
  for (const avatar of avatars.values()) meshes.push(avatar.group);
  const hits = raycaster.intersectObjects(meshes, true);
  for (const hit of hits) {
    let node: THREE.Object3D | null = hit.object;
    while (node) {
      for (const [id, avatar] of avatars) if (avatar.group === node) return id;
      node = node.parent;
    }
  }
  return null;
}

// -- world updates --------------------------------------------------------------------

function onWorld(world: WorldSnapshot): void {
  latest = world;
  // Each arrival takes the free seat nearest the door its route comes in by.
  const positions = layout(world.agents, seats, (agent) => {
    const p = avatars.get(agent.sessionId)?.group.position ?? SPAWN;
    const from = { x: p.x, z: p.z };
    return routeTo(from, zoneFor(agent)).at(-1) ?? from;
  });
  const seen = new Set<string>();

  for (const agent of world.agents) {
    seen.add(agent.sessionId);
    const target = positions.get(agent.sessionId) ?? { x: 0, z: 0 };
    let avatar = avatars.get(agent.sessionId);

    if (!avatar) {
      avatar = createAvatar(agent, SPAWN);
      avatars.set(agent.sessionId, avatar);
      stage.scene.add(avatar.group);
    }

    const before = previousState.get(agent.sessionId);
    if (before !== agent.state) {
      if (agent.state === "waiting" && !agent.waitingInferred) chime.bench();
      if (agent.state === "blocked") chime.trouble();
      previousState.set(agent.sessionId, agent.state);
    }

    updateAvatar(avatar, agent, target);
  }

  for (const [id, avatar] of avatars) {
    if (seen.has(id)) continue;
    stage.scene.remove(avatar.group);
    avatars.delete(id);
    previousState.delete(id);
    if (hud.selected === id) hud.closeDrawer();
  }

  hud.updateChrome(world);
  if (listShown()) renderList(listEl, world.agents, hud.selected);
}

connect(onWorld, (live) => {
  const el = document.getElementById("status")!;
  el.textContent = live ? "live" : "reconnecting\u2026";
  el.classList.toggle("off", !live);
});

void hud.refreshScrubber();
setInterval(() => void hud.refreshScrubber(), 15000);

// -- render loop ----------------------------------------------------------------------

const clock = new THREE.Clock();

function frame(): void {
  requestAnimationFrame(frame);
  // Nothing to draw while the list covers the village. Avatars catch up on the way back.
  if (listShown()) return;
  const dt = Math.min(clock.getDelta(), 0.1);
  const elapsed = clock.getElapsedTime();

  for (const avatar of avatars.values()) stepAvatar(avatar, dt, elapsed);

  const pick = pointerOverCanvas ? pickAgent() : null;
  if (pick !== hovered) {
    hovered = pick;
    canvas.style.cursor = pick ? "pointer" : "default";
    if (!pick) hud.hideHover();
  }
  if (hovered && latest) {
    const agent = latest.agents.find((a: Agent) => a.sessionId === hovered);
    if (agent) hud.showHover(agent, pointerPx.x, pointerPx.y);
  }

  const selected = hud.selected ? avatars.get(hud.selected) : undefined;
  plumbob.visible = Boolean(selected);
  if (selected) {
    plumbob.position.copy(selected.group.position);
    plumbob.position.y += 2.75 * selected.agent.appearance.height;
    plumbob.rotation.y = elapsed * 1.5;
  }

  stage.setTimeOfDay(new Date().getHours() + new Date().getMinutes() / 60);
  stage.renderer.render(stage.scene, stage.camera);
  // After the render, so the camera matrices the badges project through are the ones just used.
  zoneInfo.update(stage.camera, canvas);
}

frame();
