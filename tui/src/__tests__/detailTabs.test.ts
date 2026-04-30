import { afterEach, beforeEach, describe, expect, it } from "bun:test";

/**
 * Store wiring tests for the right-pane tab system. Mocks Bun.spawn at the
 * boundary so `setSelectedTab("BD")` doesn't trigger a real `bd show`
 * shellout while we assert on the synchronous state changes.
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

beforeEach(() => {
  // Stub every spawn — these tests don't care about subprocess output, only
  // about synchronous store state changes triggered by setSelectedTab/setSelected.
  (Bun as { spawn: typeof Bun.spawn }).spawn = ((cmd: string[]) => {
    const args = Array.isArray(cmd) ? cmd : [];
    if (args[0] === "bd" && args[1] === "list") {
      return makeProc("[]", "", 0);
    }
    return makeProc("[]", "", 0);
  }) as unknown as typeof Bun.spawn;
});

afterEach(() => {
  (Bun as { spawn: typeof Bun.spawn }).spawn = realSpawn;
});

async function freshStore(): Promise<typeof import("../state/store.js").useStore> {
  const mod = await import(`../state/store.js?t=${Math.random()}`);
  return mod.useStore;
}

describe("setSelectedTab() ↔ bdShowVisible coupling", () => {
  it("setSelectedTab('BD') flips bdShowVisible to true", async () => {
    const useStore = await freshStore();
    expect(useStore.getState().bdShowVisible).toBe(false);
    useStore.getState().setSelectedTab("BD");
    expect(useStore.getState().selectedTab).toBe("BD");
    expect(useStore.getState().bdShowVisible).toBe(true);
  });

  it("setSelectedTab('LIVE') after BD flips bdShowVisible back to false", async () => {
    const useStore = await freshStore();
    useStore.getState().setSelectedTab("BD");
    expect(useStore.getState().bdShowVisible).toBe(true);
    useStore.getState().setSelectedTab("LIVE");
    expect(useStore.getState().selectedTab).toBe("LIVE");
    expect(useStore.getState().bdShowVisible).toBe(false);
  });

  it("setSelectedTab('TRACE') keeps bdShowVisible false", async () => {
    const useStore = await freshStore();
    useStore.getState().setSelectedTab("TRACE");
    expect(useStore.getState().selectedTab).toBe("TRACE");
    expect(useStore.getState().bdShowVisible).toBe(false);
  });

  it("setSelectedTab('ACTIONS') keeps bdShowVisible false", async () => {
    const useStore = await freshStore();
    useStore.getState().setSelectedTab("ACTIONS");
    expect(useStore.getState().selectedTab).toBe("ACTIONS");
    expect(useStore.getState().bdShowVisible).toBe(false);
  });
});

describe("setSelected() resets selectedTab to LIVE", () => {
  it("flips selectedTab back to LIVE on row selection", async () => {
    const useStore = await freshStore();
    useStore.getState().setSelectedTab("BD");
    expect(useStore.getState().selectedTab).toBe("BD");
    useStore.getState().setSelected("some-issue-id");
    expect(useStore.getState().selectedTab).toBe("LIVE");
    expect(useStore.getState().selectedId).toBe("some-issue-id");
  });

  it("clearing selection (id=null) still resets selectedTab to LIVE", async () => {
    const useStore = await freshStore();
    useStore.getState().setSelectedTab("ACTIONS");
    useStore.getState().setSelected(null);
    expect(useStore.getState().selectedTab).toBe("LIVE");
    expect(useStore.getState().selectedId).toBeNull();
  });
});

describe("toggleShowDone()", () => {
  it("toggles showDone false → true → false", async () => {
    const useStore = await freshStore();
    expect(useStore.getState().showDone).toBe(false);
    useStore.getState().toggleShowDone();
    expect(useStore.getState().showDone).toBe(true);
    useStore.getState().toggleShowDone();
    expect(useStore.getState().showDone).toBe(false);
  });
});

describe("setPendingConfirm()", () => {
  it("sets and clears the pendingConfirm overlay state", async () => {
    const useStore = await freshStore();
    expect(useStore.getState().pendingConfirm).toBeNull();
    useStore.getState().setPendingConfirm({ action: "cancel", issueId: "rig-1" });
    expect(useStore.getState().pendingConfirm).toEqual({
      action: "cancel",
      issueId: "rig-1",
    });
    useStore.getState().setPendingConfirm(null);
    expect(useStore.getState().pendingConfirm).toBeNull();
  });
});
