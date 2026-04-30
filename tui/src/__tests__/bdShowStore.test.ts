import { afterEach, beforeEach, describe, expect, it } from "bun:test";

import { closedIssue, openIssue } from "./fixtures/issues.js";

/**
 * Store-level tests for the bd-show pane. We patch `Bun.spawn` to control
 * the wrapped `bd show` shellout from inside `data/beads.ts`'s `bdShow()`.
 * Avoids `mock.module`, which (as of bun:test 1.3) leaks into other test
 * files and broke this suite when imports were aliased.
 *
 * Each test re-imports the store with a cache-busting query param so it
 * starts from clean initial state — Zustand stores are module-scoped
 * singletons.
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

interface SpawnPlan {
  /** Plan-by-id for `bd show <id>` calls. Default: throw "unexpected". */
  showById: Record<string, () => FakeProc>;
  /** Tracks `bd show <id>` calls in invocation order. */
  showCalls: string[];
}

let plan: SpawnPlan = { showById: {}, showCalls: [] };

beforeEach(() => {
  plan = { showById: {}, showCalls: [] };
  (Bun as { spawn: typeof Bun.spawn }).spawn = ((cmd: string[]) => {
    const args = Array.isArray(cmd) ? cmd : [];
    if (args[0] === "bd" && args[1] === "show") {
      const id = args[2] ?? "";
      plan.showCalls.push(id);
      const handler = plan.showById[id];
      if (!handler) {
        return makeProc("", `unexpected bd show ${id} in test`, 1);
      }
      return handler();
    }
    if (args[0] === "bd" && args[1] === "list") {
      return makeProc("[]", "", 0);
    }
    return makeProc("", `unexpected spawn: ${args.join(" ")}`, 1);
  }) as unknown as typeof Bun.spawn;
});

afterEach(() => {
  (Bun as { spawn: typeof Bun.spawn }).spawn = realSpawn;
});

async function freshStore(): Promise<typeof import("../state/store.js").useStore> {
  const mod = await import(`../state/store.js?t=${Math.random()}`);
  return mod.useStore;
}

function planShowSuccess(id: string, body: object): void {
  plan.showById[id] = () => makeProc(JSON.stringify([body]), "", 0);
}

function planShowError(id: string, exitCode = 127, stderr = "bd not found"): void {
  plan.showById[id] = () => makeProc("", stderr, exitCode);
}

describe("bd-show store actions", () => {
  it("setSelected with bdShowVisible=true triggers a fetch and populates cache", async () => {
    planShowSuccess(openIssue.id, openIssue);
    const useStore = await freshStore();
    useStore.setState({ bdShowVisible: true });
    useStore.getState().setSelected(openIssue.id);
    // setSelected schedules refreshBdShow via void; await microtask drain.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(plan.showCalls).toEqual([openIssue.id]);
    const cache = useStore.getState().bdShowCache;
    expect(cache[openIssue.id]?.id).toBe(openIssue.id);
    expect(cache[openIssue.id]?.title).toBe(openIssue.title);
    expect(useStore.getState().bdShowError).toBeNull();
  });

  it("repeated refresh while in-flight does not re-fetch", async () => {
    let resolveExit: ((v: number) => void) | null = null;
    const enc = new TextEncoder();
    plan.showById[openIssue.id] = () => ({
      stdout: new ReadableStream({
        start(ctrl) {
          ctrl.enqueue(enc.encode(JSON.stringify([openIssue])));
          ctrl.close();
        },
      }),
      stderr: new ReadableStream({
        start(ctrl) {
          ctrl.close();
        },
      }),
      exited: new Promise<number>((res) => {
        resolveExit = res;
      }),
    });
    const useStore = await freshStore();
    useStore.setState({ bdShowVisible: true, selectedId: openIssue.id });
    void useStore.getState().refreshBdShow();
    void useStore.getState().refreshBdShow();
    await new Promise((r) => setTimeout(r, 0));
    expect(plan.showCalls.length).toBe(1);
    resolveExit!(0);
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(useStore.getState().bdShowCache[openIssue.id]?.id).toBe(openIssue.id);
  });

  it("visibility off → refreshBdShow does not call bdShow", async () => {
    planShowSuccess(openIssue.id, openIssue);
    const useStore = await freshStore();
    // bdShowVisible defaults to false.
    useStore.setState({ selectedId: openIssue.id });
    await useStore.getState().refreshBdShow();
    expect(plan.showCalls.length).toBe(0);
    expect(useStore.getState().bdShowCache[openIssue.id]).toBeUndefined();
  });

  it("rejection sets bdShowError, preserves cache, clears in-flight", async () => {
    const useStore = await freshStore();
    useStore.setState({
      bdShowVisible: true,
      selectedId: openIssue.id,
      bdShowCache: { [openIssue.id]: openIssue },
    });
    planShowError(openIssue.id, 127, "bd not found");
    await useStore.getState().refreshBdShow();
    const state = useStore.getState();
    expect(state.bdShowError).toContain("exited 127");
    // Cache UNTOUCHED.
    expect(state.bdShowCache[openIssue.id]).toEqual(openIssue);
    // In-flight cleared.
    expect(state.bdShowLoading.has(openIssue.id)).toBe(false);
    // Subsequent successful fetch on a different id clears error.
    planShowSuccess(closedIssue.id, closedIssue);
    useStore.setState({ selectedId: closedIssue.id });
    await useStore.getState().refreshBdShow();
    const after = useStore.getState();
    expect(after.bdShowError).toBeNull();
    expect(after.bdShowCache[closedIssue.id]?.id).toBe(closedIssue.id);
    expect(after.bdShowCache[openIssue.id]).toEqual(openIssue);
  });

  it("setBdShowVisible(true) eagerly schedules a refresh", async () => {
    planShowSuccess(openIssue.id, openIssue);
    const useStore = await freshStore();
    useStore.setState({ selectedId: openIssue.id });
    expect(plan.showCalls.length).toBe(0);
    useStore.getState().setBdShowVisible(true);
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(plan.showCalls).toEqual([openIssue.id]);
  });

  it("setBdShowVisible(false) does not trigger a refresh", async () => {
    planShowSuccess(openIssue.id, openIssue);
    const useStore = await freshStore();
    useStore.setState({ selectedId: openIssue.id, bdShowVisible: true });
    useStore.getState().setBdShowVisible(false);
    await new Promise((r) => setTimeout(r, 0));
    expect(useStore.getState().bdShowVisible).toBe(false);
    expect(plan.showCalls.length).toBe(0);
  });
});
