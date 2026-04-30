import { useEffect } from "react";

/**
 * Drives `tick()` on a setInterval. Calls once immediately so the first
 * frame isn't empty, then on the cadence. Cleans up on unmount.
 */
export function useTicker(tick: () => Promise<void> | void, ms: number): void {
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    const run = () => {
      if (cancelled) return;
      void tick();
    };

    run();
    timer = setInterval(run, ms);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [tick, ms]);
}
