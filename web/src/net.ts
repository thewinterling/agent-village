/** WebSocket client with reconnect. The browser only ever receives; it holds no logic. */

import type { WorldSnapshot } from "./types";

export function connect(
  onWorld: (world: WorldSnapshot) => void,
  onStatus: (live: boolean) => void,
): void {
  let retry = 500;

  const open = () => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${location.host}/ws`);

    ws.onopen = () => {
      retry = 500;
      onStatus(true);
    };
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "world") onWorld(payload as WorldSnapshot);
      } catch {
        /* a malformed frame must not kill the socket */
      }
    };
    ws.onclose = () => {
      onStatus(false);
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 8000);
    };
    ws.onerror = () => ws.close();
  };

  open();
}
