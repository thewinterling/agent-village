/** The plain list view: every agent as a table row, for when the village is too much. */

import { stateColourCss } from "./avatars";
import { STATE_ORDER, ago, dur, esc } from "./hud";
import type { Agent } from "./types";

const rank = (agent: Agent): number => {
  const i = (STATE_ORDER as readonly string[]).indexOf(agent.state);
  return i < 0 ? STATE_ORDER.length : i;
};

/** Most urgent state first, then whoever has been in it longest. */
export function listOrder(agents: Agent[]): Agent[] {
  return [...agents].sort((a, b) => rank(a) - rank(b) || b.stateAgeS - a.stateAgeS);
}

const tokens = (n: number): string => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

export function renderList(el: HTMLElement, agents: Agent[], selected: string | null): void {
  if (agents.length === 0) {
    el.innerHTML = '<p class="dim">nobody in the village.</p>';
    return;
  }
  el.innerHTML = `
    <table>
      <thead><tr><th>state</th><th>source</th><th>agent</th><th>repo</th><th>tool</th><th>model</th><th>tokens</th><th>last seen</th></tr></thead>
      <tbody>${listOrder(agents)
        .map((a) => {
          const colour = stateColourCss(a.state);
          const tool = a.currentTool ?? a.openTools[0]?.name;
          return `
          <tr data-id="${esc(a.sessionId)}"${a.sessionId === selected ? ' class="sel"' : ""}>
            <td style="color:${colour}"><i style="background:${colour}"></i>${esc(a.state)}${a.waitingInferred ? "?" : ""}
              <span class="dim">${dur(a.stateAgeS)}</span></td>
            <td>${esc(a.source)}</td>
            <td>${a.isSubagent ? '<span class="dim">&#8627; </span>' : ""}${esc(a.name)}
              <button class="rename" data-rename title="rename">&#9998;</button>
              ${a.subtitle ? `<div class="dim">${esc(a.subtitle)}</div>` : ""}</td>
            <td>${esc(a.repo ?? "")}${a.gitBranch ? `<div class="dim">${esc(a.gitBranch)}</div>` : ""}</td>
            <td>${esc(tool ?? "")}</td>
            <td>${esc(a.model ?? "")}</td>
            <td>${tokens(a.tokensIn + a.tokensOut)}</td>
            <td class="dim">${ago(a.lastEventAt)}</td>
          </tr>`;
        })
        .join("")}</tbody>
    </table>`;
}
