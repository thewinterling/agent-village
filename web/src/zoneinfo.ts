/**
 * An (i) badge on every zone sign, explaining what standing there means.
 *
 * DOM rather than 3D geometry: the copy is a paragraph long, it has to stay crisp at every
 * zoom, and it must not tilt with the isometric projection. The badges are re-projected from
 * their world anchor each frame, so they stay pinned to their zone while the camera pans.
 */

import * as THREE from "three";

import { signAnchor, STATE_COLOUR, statesIn, ZONES, type ZoneId } from "./zones";

interface Badge {
  id: ZoneId;
  el: HTMLButtonElement;
  anchor: THREE.Vector3;
}

const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;

export class ZoneInfo {
  private readonly badges: Badge[] = [];
  private readonly popover: HTMLDivElement;
  private readonly scratch = new THREE.Vector3();
  private open: ZoneId | null = null;
  /** A pinned card survives the pointer leaving the badge; a hovered one does not. */
  private pinned = false;

  constructor(layer: HTMLElement) {
    this.popover = document.createElement("div");
    this.popover.className = "zi-pop panel";
    this.popover.hidden = true;
    layer.appendChild(this.popover);

    for (const zone of Object.values(ZONES)) {
      const el = document.createElement("button");
      el.className = "zi-dot";
      el.type = "button";
      el.textContent = "i";
      el.setAttribute("aria-label", `What is the ${zone.label}?`);
      // Hover to glance, click to pin. A touch screen has no hover, and holding the mouse
      // still while reading a paragraph is irritating on a desktop too.
      el.addEventListener("pointerenter", () => this.show(zone.id, false));
      el.addEventListener("pointerleave", () => {
        if (!this.pinned) this.hide();
      });
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (this.pinned && this.open === zone.id) this.hide();
        else this.show(zone.id, true);
      });
      layer.appendChild(el);

      const a = signAnchor(zone);
      this.badges.push({ id: zone.id, el, anchor: new THREE.Vector3(a.x, a.y, a.z) });
    }

    // Clicking the village dismisses a pinned card, same as the detail drawer.
    document.addEventListener("click", () => this.hide());
  }

  private show(id: ZoneId, pin: boolean): void {
    const zone = ZONES[id];
    const states = statesIn(id)
      .map((s) => `<i style="background:${hex(STATE_COLOUR[s])}"></i>${s}`)
      .join("");
    this.popover.innerHTML = `
      <div class="zi-head">${zone.label}</div>
      <div class="zi-states">${states}</div>
      <p>${zone.info}</p>`;
    this.popover.hidden = false;
    this.open = id;
    this.pinned = pin;
  }

  private hide(): void {
    this.popover.hidden = true;
    this.open = null;
    this.pinned = false;
  }

  /** Re-pin every badge to its zone. Cheap enough for the render loop: six projections. */
  update(camera: THREE.Camera, canvas: HTMLCanvasElement): void {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;

    for (const badge of this.badges) {
      const v = this.scratch.copy(badge.anchor).project(camera);
      const x = (v.x * 0.5 + 0.5) * w;
      const y = (-v.y * 0.5 + 0.5) * h;

      // A sign panned off the edge should not leave its badge stuck to the rim.
      const onScreen = x > -40 && x < w + 40 && y > -40 && y < h + 40;
      badge.el.style.display = onScreen ? "block" : "none";
      badge.el.style.transform = `translate(-50%, 0) translate(${x}px, ${y + 13}px)`;

      if (badge.id === this.open) {
        // Flip above the badge when there is no room below, or the low signs (the front door
        // especially) push their card off the bottom of the window.
        const tall = this.popover.offsetHeight;
        const below = y + 38;
        const top = below + tall > h - 10 ? Math.max(10, y - tall - 12) : below;
        this.popover.style.transform =
          `translate(-50%, 0) translate(${Math.min(Math.max(x, 150), w - 150)}px, ${top}px)`;
      }
    }
  }
}
