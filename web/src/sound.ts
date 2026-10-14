/**
 * Audio cues, synthesised rather than shipped as assets.
 *
 * Only two events are worth interrupting someone for: an agent sitting down on the bench, and
 * an agent landing in the infirmary. Everything else stays silent on purpose - a village that
 * chimes constantly gets muted, and then it chimes for nothing.
 */

export class Chime {
  private ctx: AudioContext | null = null;
  enabled = false;

  /** Browsers only allow audio after a gesture, so this is wired to the first click. */
  unlock(): void {
    if (!this.ctx) this.ctx = new AudioContext();
    void this.ctx.resume();
  }

  bench(): void {
    this.play([660, 880], 0.16);
  }

  trouble(): void {
    this.play([320, 240], 0.22);
  }

  private play(notes: number[], length: number): void {
    if (!this.enabled || !this.ctx) return;
    const ctx = this.ctx;
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = ctx.currentTime + i * length * 0.7;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.07, start + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + length);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + length + 0.05);
    });
  }
}
