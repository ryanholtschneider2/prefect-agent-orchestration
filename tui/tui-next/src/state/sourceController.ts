import type {SourceSnapshot} from "../domain/model.js";

export interface SourceControllerOptions<T> {
  load(signal: AbortSignal, previous?: SourceSnapshot<T>): Promise<SourceSnapshot<T>>;
  intervalMs: number;
  timeoutMs?: number;
  maxBackoffMs?: number;
  jitter?: () => number;
}

const hash = (value: unknown): string => {
  const text = JSON.stringify(value); let out = 2166136261;
  for (let index = 0; index < text.length; index++) { out ^= text.charCodeAt(index); out = Math.imul(out, 16777619); }
  return (out >>> 0).toString(16);
};

export class SourceController<T> {
  private timer?: ReturnType<typeof setTimeout>;
  private abort?: AbortController;
  private attempt = 0;
  private running = false;
  private stopped = true;
  private snapshot?: SourceSnapshot<T>;
  private listeners = new Set<(snapshot: SourceSnapshot<T>) => void>();

  constructor(private readonly options: SourceControllerOptions<T>) {}

  subscribe(listener: (snapshot: SourceSnapshot<T>) => void): () => void { this.listeners.add(listener); if (this.snapshot) listener(this.snapshot); return () => this.listeners.delete(listener); }
  start(): void { this.stopped = false; void this.refreshNow(); }
  stop(): void { this.stopped = true; if (this.timer) clearTimeout(this.timer); this.abort?.abort(); this.running = false; }

  async refreshNow(): Promise<void> {
    if (this.running) return;
    if (this.timer) clearTimeout(this.timer);
    this.running = true; this.abort = new AbortController();
    const timeout = setTimeout(() => this.abort?.abort(new Error("source refresh timed out")), this.options.timeoutMs ?? 10_000);
    try {
      const next = await this.options.load(this.abort.signal, this.snapshot);
      const contentHash = hash(next.data); const unchanged = contentHash === this.snapshot?.contentHash && next.freshness === this.snapshot?.freshness && next.error === this.snapshot?.error;
      this.attempt = next.freshness === "fresh" ? 0 : this.attempt + 1;
      const delay = next.freshness === "fresh" ? this.options.intervalMs : Math.min(this.options.maxBackoffMs ?? 60_000, this.options.intervalMs * 2 ** Math.min(this.attempt, 6));
      const jitter = Math.floor(delay * .1 * (this.options.jitter?.() ?? Math.random()));
      this.snapshot = {...next, contentHash, retry: {attempt: this.attempt, nextAt: new Date(Date.now() + delay + jitter).toISOString(), inFlight: false}};
      if (!unchanged) for (const listener of this.listeners) listener(this.snapshot);
      if (!this.stopped) this.timer = setTimeout(() => void this.refreshNow(), delay + jitter);
    } catch (error) {
      this.attempt += 1;
      const delay = Math.min(this.options.maxBackoffMs ?? 60_000, this.options.intervalMs * 2 ** Math.min(this.attempt, 6));
      if (this.snapshot) {
        this.snapshot = {...this.snapshot, freshness: "stale", error: error instanceof Error ? error.message : String(error), retry: {attempt: this.attempt, nextAt: new Date(Date.now() + delay).toISOString(), inFlight: false}};
        for (const listener of this.listeners) listener(this.snapshot);
      }
      if (!this.stopped) this.timer = setTimeout(() => void this.refreshNow(), delay);
    } finally { clearTimeout(timeout); this.running = false; }
  }
}
