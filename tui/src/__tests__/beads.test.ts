import { afterEach, beforeEach, describe, expect, it, mock } from "bun:test";

/**
 * Unit tests for `bdShow()` — the array-unwrap + throw-on-error contract
 * introduced in iter 2 (Δ B1). We monkey-patch `Bun.spawn` so each case
 * gets a controlled stdout/stderr/exit-code combination.
 */

interface FakeProc {
  stdout: ReadableStream<Uint8Array>;
  stderr: ReadableStream<Uint8Array>;
  exited: Promise<number>;
}

function makeProc(stdout: string, stderr: string, code: number): FakeProc {
  const enc = new TextEncoder();
  const mk = (s: string): ReadableStream<Uint8Array> =>
    new ReadableStream({
      start(ctrl) {
        if (s) ctrl.enqueue(enc.encode(s));
        ctrl.close();
      },
    });
  return { stdout: mk(stdout), stderr: mk(stderr), exited: Promise.resolve(code) };
}

const realSpawn = Bun.spawn;

afterEach(() => {
  // restore the real spawn after each test
  (Bun as { spawn: typeof Bun.spawn }).spawn = realSpawn;
});

describe("bdShow()", () => {
  it("unwraps the bd-json array and returns the first issue", async () => {
    (Bun as { spawn: typeof Bun.spawn }).spawn = mock(() =>
      makeProc('[{"id":"x","title":"hi","status":"open"}]', "", 0),
    ) as unknown as typeof Bun.spawn;
    const { bdShow } = await import("../data/beads.js");
    const issue = await bdShow("x");
    expect(issue?.id).toBe("x");
    expect(issue?.title).toBe("hi");
  });

  it("throws on shellout exit ≠ 0", async () => {
    (Bun as { spawn: typeof Bun.spawn }).spawn = mock(() =>
      makeProc("", "no such bead", 1),
    ) as unknown as typeof Bun.spawn;
    const { bdShow } = await import("../data/beads.js");
    await expect(bdShow("missing")).rejects.toThrow(/exited 1/);
  });

  it("throws on bad JSON", async () => {
    (Bun as { spawn: typeof Bun.spawn }).spawn = mock(() =>
      makeProc("not-json", "", 0),
    ) as unknown as typeof Bun.spawn;
    const { bdShow } = await import("../data/beads.js");
    await expect(bdShow("x")).rejects.toThrow();
  });

  it("throws on non-array root", async () => {
    (Bun as { spawn: typeof Bun.spawn }).spawn = mock(() =>
      makeProc('{"id":"x"}', "", 0),
    ) as unknown as typeof Bun.spawn;
    const { bdShow } = await import("../data/beads.js");
    await expect(bdShow("x")).rejects.toThrow(/expected array/);
  });

  it("returns null on legitimately empty array", async () => {
    (Bun as { spawn: typeof Bun.spawn }).spawn = mock(() =>
      makeProc("[]", "", 0),
    ) as unknown as typeof Bun.spawn;
    const { bdShow } = await import("../data/beads.js");
    expect(await bdShow("nonexistent-but-valid")).toBeNull();
  });
});
