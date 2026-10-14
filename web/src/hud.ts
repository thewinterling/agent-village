/** Hover card, detail drawer, counters and the history scrubber. Plain DOM over the canvas. */

import type { Agent, AgentDetail, UsageBucket, UsageSeries, WorldSnapshot } from "./types";
import { stateColourCss } from "./avatars";
import { dismissAgent, promptAgent, stopAgent } from "./control";

/** Bar geometry, kept in step with the `#scrubber i` rule in index.html. */
const BAR_W = 10;
const BAR_GAP = 2;
/** A slice with any spend at all gets at least this, or a quiet minute looks like an idle one. */
const MIN_BAR = 2;

export const spend = (bucket: UsageBucket): number => bucket.in + bucket.out;

/**
 * The slice of the series the strip can actually show, oldest left, newest right.
 *
 * Takes them off the *newest* end: the right-hand edge is "now", the only part of the series
 * anybody is looking at. The bars stretch to fill whatever slack the slot count leaves, and
 * squeeze in a strip too narrow for the minimum of twelve.
 */
export function visibleBuckets(buckets: UsageBucket[], widthPx: number): UsageBucket[] {
  const slots = Math.max(12, Math.floor((widthPx + BAR_GAP) / (BAR_W + BAR_GAP)));
  return buckets.slice(-slots);
}

/**
 * Bar heights in pixels, scaled to the busiest slice on screen.
 *
 * Square root rather than linear. Token spend is violently spiky - one compaction or one
 * oversized file read outweighs an hour of ordinary turns - and on a linear axis that single
 * bar squashes everything else flat, which is the opposite of what a usage chart is for.
 */
export function barHeights(buckets: UsageBucket[], heightPx: number): number[] {
  const peak = Math.max(0, ...buckets.map(spend));
  if (peak === 0) return buckets.map(() => 0);
  return buckets.map((bucket) => {
    const tokens = spend(bucket);
    return tokens === 0 ? 0 : Math.max(MIN_BAR, Math.round(Math.sqrt(tokens / peak) * heightPx));
  });
}

/** Clearance between the pointer and the hover card, and between the card and the viewport. */
const HOVER_GAP = 16;
const HOVER_EDGE = 12;

/**
 * Where the hover card goes, given the pointer, the card's own size and the viewport.
 *
 * Below and right of the pointer, flipped to the other side when there is no room. Flipping
 * rather than sliding is the whole point: the old version pushed the card back inside the
 * viewport, which parked it on top of the usage strip along the bottom - so hovering a bar
 * covered the bars.
 */
export function hoverPosition(
  pointer: { x: number; y: number },
  card: { width: number; height: number },
  view: { width: number; height: number },
): { left: number; top: number } {
  const place = (at: number, size: number, limit: number): number =>
    at + HOVER_GAP + size + HOVER_EDGE > limit ? Math.max(HOVER_EDGE, at - HOVER_GAP - size) : at + HOVER_GAP;

  return {
    left: place(pointer.x, card.width, view.width),
    top: place(pointer.y, card.height, view.height),
  };
}

const esc = (v: unknown): string =>
  String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export const dur = (s: number): string =>
  s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;

const num = (n: number): string =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;

/** A slice of the usage strip is a whole minute wide, so seconds would be noise. */
const clockMinute = (iso: string): string =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

export const ago = (iso: string, now: number = Date.now()): string => {
  const seconds = (now - new Date(iso).getTime()) / 1000;
  return seconds < 1 ? "just now" : `${dur(seconds)} ago`;
};

export class Hud {
  private readonly hover = document.getElementById("hover")!;
  private readonly drawer = document.getElementById("drawer")!;
  private readonly counts = document.getElementById("counts")!;
  private readonly stats = document.getElementById("stats")!;
  private readonly scrubber = document.getElementById("scrubber")!;
  private readonly scrubberCap = document.getElementById("scrubber-cap")!;
  /** Everything the last fetch returned, oldest first. */
  private usage: UsageSeries = { bucketS: 60, buckets: [] };
  /** The subset currently drawn on the strip, oldest first, indexed by `data-tick`. */
  private shown: UsageBucket[] = [];
  selected: string | null = null;

  constructor(private readonly onClose: () => void) {
    document.getElementById("drawer-close")?.addEventListener("click", () => this.closeDrawer());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this.closeDrawer();
    });

    // Delegated, because the bars are replaced wholesale every refresh.
    this.scrubber.addEventListener("pointermove", (e) => {
      const bucket = this.bucketAt(e.target);
      if (bucket) this.showUsage(bucket, e.clientX, e.clientY);
      else this.hideHover();
    });
    this.scrubber.addEventListener("pointerleave", () => this.hideHover());
    this.scrubber.addEventListener("click", (e) => {
      const session = this.bucketAt(e.target)?.top?.sessionId;
      if (session) void this.openDrawer(session);
    });
    new ResizeObserver(() => this.drawScrubber()).observe(this.scrubber);
  }

  /** The hover card: everything you need without clicking. */
  showHover(agent: Agent, x: number, y: number): void {
    const colour = stateColourCss(agent.state);
    const guess = agent.waitingInferred ? ' <span class="guess">(inferred)</span>' : "";
    const tool = agent.currentTool ?? agent.openTools[0]?.name;
    const detail = agent.openTools[0]?.detail ?? agent.recentTools.at(-1)?.detail;

    this.hover.innerHTML = `
      <div class="hv-name">${esc(agent.name)}</div>
      ${agent.subtitle ? `<div class="hv-sub">${esc(agent.subtitle)}</div>` : ""}
      <div class="hv-state" style="color:${colour}">
        ${esc(agent.state)}${guess} &middot; ${dur(agent.stateAgeS)}
      </div>
      <dl>
        ${tool ? `<dt>tool</dt><dd>${esc(tool)}${detail ? ` <span class="dim">${esc(detail).slice(0, 60)}</span>` : ""}</dd>` : ""}
        ${agent.model ? `<dt>model</dt><dd>${esc(agent.model)}</dd>` : ""}
        ${agent.lastPrompt ? `<dt>prompt</dt><dd>${esc(agent.lastPrompt.split("\n")[0]).slice(0, 90)}</dd>` : ""}
      </dl>
      <div class="hv-hint">click for detail</div>`;
    this.hover.style.display = "block";
    this.positionHover(x, y);
  }

  /** Keep the card on screen when whatever it describes is near an edge. */
  private positionHover(x: number, y: number): void {
    const card = this.hover.getBoundingClientRect();
    const view = { width: window.innerWidth, height: window.innerHeight };
    const { left, top } = hoverPosition({ x, y }, card, view);
    this.hover.style.transform = `translate(${left}px, ${top}px)`;
  }

  hideHover(): void {
    this.hover.style.display = "none";
  }

  async openDrawer(sessionId: string): Promise<void> {
    this.selected = sessionId;
    this.drawer.classList.add("open");
    const body = document.getElementById("drawer-body")!;
    body.innerHTML = '<div class="dim">loading&hellip;</div>';

    const response = await fetch(`/api/agents/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      body.innerHTML = '<div class="dim">that agent has left the village.</div>';
      return;
    }
    const a: AgentDetail = await response.json();

    body.innerHTML = `
      <h2>${esc(a.name)}</h2>
      ${a.subtitle ? `<div class="drawer-sub">${esc(a.subtitle)}</div>` : ""}
      <div class="chips">
        <span class="chip" style="border-color:${stateColourCss(a.state)};color:${stateColourCss(a.state)}">
          ${esc(a.state)}${a.waitingInferred ? " (inferred)" : ""} &middot; ${dur(a.stateAgeS)}
        </span>
        <span class="chip">${esc(a.source)}</span>
        ${a.model ? `<span class="chip">${esc(a.model)}</span>` : ""}
        ${a.isSubagent ? `<span class="chip">subagent${a.agentType ? `: ${esc(a.agentType)}` : ""}</span>` : ""}
        ${a.isBackground ? '<span class="chip">background</span>' : ""}
      </div>

      ${a.prLinks.length ? section(`merge requests (${a.prLinks.length})`, `<ul class="mrs">${a.prLinks
        .map((p) => `
          <li>
            <a href="${esc(p.url)}" target="_blank" rel="noreferrer">
              <span class="mr-num">${p.number ? `!${p.number}` : "MR"}</span>
              <span class="mr-repo">${esc(p.repo ?? hostOf(p.url))}</span>
            </a>
          </li>`)
        .join("")}</ul>`) : ""}

      ${a.narrative.length ? section(`what happened (${plural(a.narrative.length, "turn")})`,
        `<ol class="story">${a.narrative.map((t) => `
          <li>
            <div class="st-ask">${esc(t.ask)}</div>
            <div class="st-out">${t.outcome ? esc(t.outcome) : '<i class="dim">still working on it&hellip;</i>'}</div>
            <div class="st-meta">${t.at ? clockMinute(t.at) : ""}${t.toolCalls ? ` &middot; ${plural(t.toolCalls, "tool call")}` : ""}</div>
          </li>`).join("")}</ol>`) : ""}

      ${section("say something", a.canPrompt
        ? `<div class="prompt-box">
            <textarea id="prompt-text" rows="2" placeholder="type a prompt for this agent&hellip;"></textarea>
            <button id="prompt-send">send</button>
          </div>
          <div class="dim act-note" id="prompt-note">Typed straight into the agent's terminal. If it is busy, the prompt waits its turn.</div>`
        : `<div class="dim">${esc(a.promptBlockedReason ?? "This agent cannot be prompted from here.")}</div>`)}

      ${section("where", `
        ${row("cwd", a.cwd)}
        ${row("branch", a.gitBranch)}
        ${row("pid", a.pid || null)}
        ${row("permission mode", a.permissionMode)}
      `)}

      ${section("effort", `
        ${row("turns", a.turns)}
        ${row("tool calls", a.toolCount)}
        ${row("tokens in / out", `${num(a.tokensIn)} / ${num(a.tokensOut)}`)}
        ${row("alive for", dur((Date.now() - Date.parse(a.spawnedAt)) / 1000))}
      `)}

      ${/* The last turn of the story says this already, so only fall back to it. */ ""}
      ${a.narrative.length ? "" : `
        ${a.lastPrompt ? section("last prompt", `<pre>${esc(a.lastPrompt)}</pre>`) : ""}
        ${a.lastAssistant ? section("last response", `<pre>${esc(a.lastAssistant)}</pre>`) : ""}`}

      ${a.filesTouched.length ? section(`files touched (${a.filesTouched.length})`,
        `<ul class="files">${a.filesTouched.slice(-20).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`) : ""}

      ${section("recent tools", a.recentTools.length
        ? `<ul class="timeline">${[...a.recentTools].reverse().slice(0, 15).map((t) => `
            <li class="${t.ok === false ? "bad" : ""}">
              <b>${esc(t.name)}</b>
              ${t.durationMs !== null ? `<span class="dim">${t.durationMs}ms</span>` : ""}
              ${t.detail ? `<div class="dim">${esc(t.detail)}</div>` : ""}
            </li>`).join("")}</ul>`
        : '<div class="dim">none yet</div>')}

      ${a.resumeCommand ? section("resume", `
        <div class="copy-row">
          <code id="resume-cmd">${esc(a.resumeCommand)}</code>
          <button id="copy-resume">copy</button>
        </div>`) : ""}

      ${section("when you are done with it", `
        <div class="actions">
          <button id="act-stop" class="danger"${a.canStop ? "" : " disabled"}>stop agent</button>
          <button id="act-dismiss">dismiss avatar</button>
        </div>
        <div class="dim act-note" id="act-note">${
          a.canStop
            ? "Stop sends Ctrl-C to the process. The session stays resumable with the command above."
            : esc(a.stopBlockedReason ?? "This agent cannot be stopped from here.")
        }</div>`)}

      <div class="dim session-id">${esc(a.sessionId)}</div>`;

    document.getElementById("copy-resume")?.addEventListener("click", (e) => {
      navigator.clipboard.writeText(a.resumeCommand ?? "");
      (e.target as HTMLButtonElement).textContent = "copied";
    });

    this.wirePrompt(a);
    this.wireAction("act-stop", a, "stop this agent?", () => stopAgent(a.sessionId), "signal sent");
    this.wireAction("act-dismiss", a, "remove from view?", () => dismissAgent(a.sessionId), "dismissed");
  }

  /**
   * The prompt box. No confirmation step here, unlike the buttons below it: you had to type
   * the thing, which is confirmation enough, and sending a prompt is undoable in a way that
   * Ctrl-C is not.
   */
  private wirePrompt(agent: AgentDetail): void {
    const box = document.getElementById("prompt-text") as HTMLTextAreaElement | null;
    const button = document.getElementById("prompt-send") as HTMLButtonElement | null;
    const note = document.getElementById("prompt-note");
    if (!box || !button) return;

    const send = async (): Promise<void> => {
      const text = box.value.trim();
      if (!text || button.disabled) return;
      button.disabled = true;
      button.textContent = "sending";
      try {
        await promptAgent(agent.sessionId, text);
        box.value = "";
        if (note) note.textContent = "Sent. The agent picks it up when it next reads its input.";
      } catch (error) {
        if (note) note.textContent = error instanceof Error ? error.message : "could not send that";
      }
      button.disabled = false;
      button.textContent = "send";
    };

    button.addEventListener("click", () => void send());
    // Enter sends, shift-enter makes a new line - the same bargain every chat box offers.
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void send();
      }
    });
  }

  /**
   * Two-click confirmation on the destructive buttons.
   *
   * The drawer opens from a single click on an avatar, so a stray click near the Stop button
   * should not be able to kill a running agent.
   */
  private wireAction(
    id: string,
    agent: AgentDetail,
    confirmLabel: string,
    run: () => Promise<void>,
    doneLabel: string,
  ): void {
    const button = document.getElementById(id) as HTMLButtonElement | null;
    const note = document.getElementById("act-note");
    if (!button || button.disabled) return;
    const original = button.textContent ?? "";
    let armed = false;

    button.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        button.textContent = confirmLabel;
        button.classList.add("armed");
        setTimeout(() => {
          if (!armed) return;
          armed = false;
          button.textContent = original;
          button.classList.remove("armed");
        }, 4000);
        return;
      }
      armed = false;
      button.disabled = true;
      button.classList.remove("armed");
      try {
        await run();
        button.textContent = doneLabel;
        if (agent.sessionId === this.selected && id === "act-dismiss") this.closeDrawer();
      } catch (error) {
        button.textContent = original;
        button.disabled = false;
        if (note) note.textContent = error instanceof Error ? error.message : String(error);
      }
    });
  }

  closeDrawer(): void {
    this.selected = null;
    this.drawer.classList.remove("open");
    this.onClose();
  }

  updateChrome(world: WorldSnapshot): void {
    const order = ["waiting", "blocked", "working", "thinking", "compacting", "done", "sleeping"] as const;
    this.counts.innerHTML = order
      .filter((s) => world.counts[s])
      .map(
        (s) =>
          `<span class="pill" style="border-color:${stateColourCss(s)}">
             <i style="background:${stateColourCss(s)}"></i>${s}<b>${world.counts[s]}</b>
           </span>`,
      )
      .join("");

    if (world.stats) {
      const s = world.stats;
      this.stats.innerHTML =
        `<span>${s.turns} turns</span><span>${s.toolCalls} tool calls</span>` +
        `<span>${s.benchVisits} bench visits</span><span>${s.failures} failures</span>` +
        `<span class="dim">today</span>`;
    }
  }

  /**
   * The strip along the bottom: tokens spent per minute, oldest left, newest right.
   *
   * It is where the village's effort actually went. Hovering a bar says how much, when, and
   * who spent most of it; clicking one opens that agent. Claude sessions only - token counts
   * come out of the transcript, and Cursor does not write one we can read.
   */
  async refreshScrubber(): Promise<void> {
    const response = await fetch("/api/usage");
    if (!response.ok) return;
    this.usage = (await response.json()) as UsageSeries;
    this.drawScrubber();
  }

  /**
   * Lay the bars out at the strip's current width.
   *
   * Kept separate from the fetch because the strip changes width without the series
   * changing: it is widest on the very first frame, before the stats panel beside it has any
   * numbers in it to take up room. Re-slicing only every 15 seconds left the newest bars
   * hanging off the clipped end in between.
   */
  private drawScrubber(): void {
    this.shown = visibleBuckets(this.usage.buckets, this.scrubber.clientWidth);
    const total = this.shown.reduce((sum, bucket) => sum + spend(bucket), 0);

    if (total === 0) {
      this.shown = [];
      this.scrubber.innerHTML = "";
      this.scrubberCap.textContent = "tokens";
      return;
    }

    const heights = barHeights(this.shown, this.scrubber.clientHeight);
    this.scrubber.innerHTML = this.shown
      .map((bucket, i) => {
        const height = heights[i]!;
        // Output stacked on top of input: output is the part that was actually generated,
        // and seeing the two apart is the difference between "read a lot" and "wrote a lot".
        const out = Math.round((bucket.out / Math.max(1, spend(bucket))) * height);
        return `<i data-tick="${i}" style="height:${height}px"><b style="height:${out}px"></b></i>`;
      })
      .join("");

    const span = dur(this.shown.length * this.usage.bucketS);
    this.scrubberCap.textContent = `${num(total)} tokens / ${span}`;
  }

  /** Which bar the pointer is over, if any. */
  private bucketAt(target: EventTarget | null): UsageBucket | undefined {
    const bar = (target as HTMLElement | null)?.closest?.("[data-tick]") as HTMLElement | null;
    return bar ? this.shown[Number(bar.dataset.tick)] : undefined;
  }

  private showUsage(bucket: UsageBucket, x: number, y: number): void {
    const until = new Date(Date.parse(bucket.t) + this.usage.bucketS * 1000).toISOString();
    this.hover.innerHTML = `
      <div class="hv-name">${num(spend(bucket))} tokens</div>
      <div class="hv-state">${esc(clockMinute(bucket.t))} <span class="dim">(${esc(ago(until))})</span></div>
      <dl>
        <dt>in / out</dt><dd>${num(bucket.in)} / ${num(bucket.out)}</dd>
        ${bucket.top ? `<dt>mostly</dt><dd>${esc(bucket.top.name)} <span class="dim">${num(bucket.top.tokens)}</span></dd>` : ""}
        ${bucket.sessions > 1 ? `<dt>agents</dt><dd>${bucket.sessions}</dd>` : ""}
      </dl>
      ${bucket.top ? '<div class="hv-hint">click to open that agent</div>' : ""}`;
    this.hover.style.display = "block";
    this.positionHover(x, y);
  }
}

function section(title: string, body: string): string {
  return `<section><h3>${title}</h3>${body}</section>`;
}

/** Fallback label when the transcript did not record which project the MR belongs to. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function row(label: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  return `<div class="kv"><span>${label}</span><span>${esc(value)}</span></div>`;
}
