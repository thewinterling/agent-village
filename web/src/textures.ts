/**
 * Procedural floor textures, drawn on a canvas at load time.
 *
 * Greyscale on purpose. Three multiplies the map by the material colour, so one neutral pattern
 * can tint to any zone without shipping a texture file or a loader.
 */

import * as THREE from "three";

export type FloorKind = "tile" | "plank" | "grass" | "carpet" | "lino" | "dirt";

const SIZE = 256;

/** Deterministic noise, so the village looks the same on every reload. */
function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
}

function shade(v: number): string {
  const c = Math.round(Math.max(0, Math.min(255, v)));
  return `rgb(${c},${c},${c})`;
}

const DRAW: Record<FloorKind, (ctx: CanvasRenderingContext2D, rand: () => number) => void> = {
  tile(ctx, rand) {
    const n = 4;
    const cell = SIZE / n;
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        ctx.fillStyle = shade(238 + rand() * 17);
        ctx.fillRect(x * cell, y * cell, cell, cell);
      }
    }
    ctx.strokeStyle = shade(196);
    ctx.lineWidth = 3;
    for (let i = 0; i <= n; i++) {
      ctx.beginPath();
      ctx.moveTo(i * cell, 0);
      ctx.lineTo(i * cell, SIZE);
      ctx.moveTo(0, i * cell);
      ctx.lineTo(SIZE, i * cell);
      ctx.stroke();
    }
  },

  plank(ctx, rand) {
    const rows = 5;
    const h = SIZE / rows;
    for (let r = 0; r < rows; r++) {
      ctx.fillStyle = shade(226 + rand() * 29);
      ctx.fillRect(0, r * h, SIZE, h);
      // Grain: a few long, faint strokes along the plank.
      for (let g = 0; g < 7; g++) {
        ctx.strokeStyle = shade(210 + rand() * 20);
        ctx.lineWidth = 1;
        const y = r * h + rand() * h;
        ctx.beginPath();
        ctx.moveTo(rand() * SIZE * 0.4, y);
        ctx.lineTo(SIZE * (0.5 + rand() * 0.5), y);
        ctx.stroke();
      }
      ctx.strokeStyle = shade(180);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(0, r * h);
      ctx.lineTo(SIZE, r * h);
      ctx.stroke();
    }
  },

  grass(ctx, rand) {
    ctx.fillStyle = shade(236);
    ctx.fillRect(0, 0, SIZE, SIZE);
    for (let i = 0; i < 2600; i++) {
      ctx.strokeStyle = shade(205 + rand() * 50);
      ctx.lineWidth = 1 + rand();
      const x = rand() * SIZE;
      const y = rand() * SIZE;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + (rand() - 0.5) * 3, y - 2 - rand() * 4);
      ctx.stroke();
    }
  },

  carpet(ctx, rand) {
    ctx.fillStyle = shade(240);
    ctx.fillRect(0, 0, SIZE, SIZE);
    for (let i = 0; i < 9000; i++) {
      ctx.fillStyle = shade(215 + rand() * 40);
      ctx.fillRect(rand() * SIZE, rand() * SIZE, 2, 2);
    }
  },

  lino(ctx, rand) {
    ctx.fillStyle = shade(250);
    ctx.fillRect(0, 0, SIZE, SIZE);
    for (let i = 0; i < 1800; i++) {
      ctx.fillStyle = shade(228 + rand() * 24);
      ctx.fillRect(rand() * SIZE, rand() * SIZE, 2, 2);
    }
    ctx.strokeStyle = shade(226);
    ctx.lineWidth = 2;
    for (let i = 0; i <= 2; i++) {
      ctx.beginPath();
      ctx.moveTo((i * SIZE) / 2, 0);
      ctx.lineTo((i * SIZE) / 2, SIZE);
      ctx.moveTo(0, (i * SIZE) / 2);
      ctx.lineTo(SIZE, (i * SIZE) / 2);
      ctx.stroke();
    }
  },

  dirt(ctx, rand) {
    ctx.fillStyle = shade(242);
    ctx.fillRect(0, 0, SIZE, SIZE);
    for (let i = 0; i < 4200; i++) {
      ctx.fillStyle = shade(206 + rand() * 46);
      const r = 1 + rand() * 2.5;
      ctx.beginPath();
      ctx.arc(rand() * SIZE, rand() * SIZE, r, 0, Math.PI * 2);
      ctx.fill();
    }
  },
};

/** `repeatX`/`repeatY` are in tiles across the surface, not world units. */
export function floorTexture(kind: FloorKind, repeatX: number, repeatY: number): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext("2d")!;
  DRAW[kind](ctx, rng(kind.length * 7919 + 13));

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(repeatX, repeatY);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}
