/** Framing the village on screen. No three.js, so testable. */

export interface Frustum {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/**
 * The orthographic frustum that fits the village into the part of the canvas nothing covers.
 *
 * The bar along the bottom sits on top of the canvas. Fitting to the full canvas parked the
 * front row of zones behind it, so the fit is done for the canvas minus `reservedPx` at the
 * bottom, and the frustum is then extended downwards by that strip so pixels stay square.
 *
 * Never fits into less than 40% of the canvas: in a window no taller than the bar, a
 * village shrunk to a sliver is no more use than one half hidden.
 */
export function fitFrustum(
  bounds: { halfW: number; halfH: number },
  widthPx: number,
  heightPx: number,
  reservedPx: number,
  zoom: number,
): Frustum {
  const visiblePx = Math.max(heightPx * 0.4, heightPx - reservedPx);
  const fitHalf = Math.max(bounds.halfH, (bounds.halfW * visiblePx) / widthPx) * zoom;
  const worldPerPx = (2 * fitHalf) / visiblePx;
  const top = fitHalf;
  return {
    left: (-widthPx / 2) * worldPerPx,
    right: (widthPx / 2) * worldPerPx,
    top,
    bottom: top - heightPx * worldPerPx,
  };
}
