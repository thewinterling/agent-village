/**
 * Talking back to the daemon.
 *
 * Every call here carries the control token and an explicit JSON content type. The content
 * type is not decoration: it is what forces a CORS preflight, which the daemon answers for
 * nobody, so a page on some other origin cannot reach these endpoints at all.
 */

let token: string | null = null;

async function controlToken(): Promise<string> {
  if (token === null) {
    const response = await fetch("/api/session");
    if (!response.ok) throw new Error("could not get a control token");
    token = (await response.json()).token as string;
  }
  return token;
}

async function post(path: string, payload: unknown = {}): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-village-token": await controlToken() },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error ?? `request failed (${response.status})`);
  }
}

/** Send the agent's process a Ctrl-C. Claude CLI only; the daemon enforces that. */
export async function stopAgent(sessionId: string): Promise<void> {
  await post(`/api/agents/${encodeURIComponent(sessionId)}/stop`);
}

/** Retire the avatar. Touches nothing outside the village. */
export async function dismissAgent(sessionId: string): Promise<void> {
  await post(`/api/agents/${encodeURIComponent(sessionId)}/dismiss`);
}

/** Type a prompt into the agent's terminal and press enter. Claude CLI under tmux only. */
export async function promptAgent(sessionId: string, text: string): Promise<void> {
  await post(`/api/agents/${encodeURIComponent(sessionId)}/prompt`, { text });
}

/** Give the session a name of your own. An empty name goes back to the generated one. */
export async function renameAgent(sessionId: string, title: string): Promise<void> {
  await post(`/api/agents/${encodeURIComponent(sessionId)}/rename`, { title });
}
