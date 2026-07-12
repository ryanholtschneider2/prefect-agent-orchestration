import {afterEach, describe, expect, test} from "bun:test";
import {chmod, mkdtemp, mkdir, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {arrayPayload, fetchArtifacts, fetchBead, fetchBeads, fetchPrefect, healthy, readArtifact, unhealthy} from "../src/sources/adapters.js";
import {checked} from "../src/sources/process.js";
import {SourceController} from "../src/state/sourceController.js";

let server: ReturnType<typeof Bun.serve> | undefined;
afterEach(() => { server?.stop(true); server = undefined; });

describe("source snapshots", () => {
  test("retains the last successful data when a source fails", () => {
    const previous = healthy("prefect", [{id: "kept"}]); const failed = unhealthy("prefect", [], new Error("token=secret"), previous);
    expect(failed.freshness).toBe("stale"); expect(failed.data).toEqual([{id: "kept"}]); expect(failed.error).toContain("[redacted]");
  });

  test("Prefect adapter parses stable tags, runtime, and role iterations", async () => {
    server = Bun.serve({port: 0, routes: {"/api/flow_runs/filter": {POST: async (request) => {
      const body = await request.json() as {offset?: number}; return Response.json(body.offset ? [] : [{id: "flow-1", state_name: "Running", tags: ["issue_id:po-1", "epic_id:po-e", "formula:agentic"], start_time: "2026-07-12T00:00:00Z", parameters: {model: "gpt-5.4", effort: "xhigh"}}]);
    }}, "/api/task_runs/filter": {POST: () => Response.json([{id: "task-1", name: "builder", state_name: "Running", run_count: 2}])}}});
    const snapshot = await fetchPrefect(`http://127.0.0.1:${server.port}/api`);
    expect(snapshot.freshness).toBe("fresh"); expect(snapshot.data[0]).toMatchObject({issueId: "po-1", epicId: "po-e", formula: "agentic", runtime: {model: "gpt-5.4", effort: "xhigh"}}); expect(snapshot.data[0]?.roles[0]?.iteration).toBe(2);
  });

  test("Prefect failure is localized", async () => {
    server = Bun.serve({port: 0, routes: {"/api/flow_runs/filter": {POST: () => new Response("down", {status: 503})}}});
    const snapshot = await fetchPrefect(`http://127.0.0.1:${server.port}/api`); expect(snapshot.freshness).toBe("unavailable"); expect(snapshot.error).toContain("503");
  });
  test("Prefect pagination continues through full pages", async () => {
    const offsets: number[] = []; server = Bun.serve({port: 0, routes: {"/api/flow_runs/filter": {POST: async (request) => { const body = await request.json() as {offset: number}; offsets.push(body.offset); return Response.json(body.offset === 0 ? Array.from({length: 200}, (_, index) => ({id: `flow-${index}`, state_name: "Running", tags: [`issue_id:po-${index}`]})) : []); }}, "/api/task_runs/filter": {POST: () => Response.json([])}}});
    const snapshot = await fetchPrefect(`http://127.0.0.1:${server.port}/api`); expect(snapshot.data).toHaveLength(200); expect(offsets).toEqual([0, 200]);
  });
  test("Prefect always hydrates task roles for a selected historical issue", async () => {
    let taskCalls = 0;
    server = Bun.serve({port: 0, routes: {
      "/api/flow_runs/filter": {POST: () => Response.json([{id: "old-flow", state_name: "Completed", tags: ["issue_id:po-selected"]}])},
      "/api/task_runs/filter": {POST: () => { taskCalls++; return Response.json([{id: "task", name: "review", state_name: "Completed"}]); }},
    }});
    const withoutSelection = await fetchPrefect(`http://127.0.0.1:${server.port}/api`);
    const withSelection = await fetchPrefect(`http://127.0.0.1:${server.port}/api`, undefined, withoutSelection, "po-selected");
    expect(withoutSelection.data[0]?.roles).toEqual([]);
    expect(withSelection.data[0]?.roles[0]?.role).toBe("review");
    expect(taskCalls).toBe(1);
  });
  test("Prefect abort retains the prior snapshot", async () => {
    server = Bun.serve({port: 0, routes: {"/api/flow_runs/filter": {POST: async () => { await Bun.sleep(100); return Response.json([]); }}}}); const controller = new AbortController(); const prior = healthy("prefect", [{id: "prior", state: "RUNNING", runtime: {}, roles: []}]); const pending = fetchPrefect(`http://127.0.0.1:${server.port}/api`, controller.signal, prior); controller.abort(); const snapshot = await pending; expect(snapshot.freshness).toBe("stale"); expect(snapshot.data[0]?.id).toBe("prior");
  });
  test("Beads backend selection honors PO_BEADS_BACKEND", async () => {
    const bin = await mkdtemp(join(tmpdir(), "po-tui-bin-")); const script = join(bin, "br"); await writeFile(script, "#!/bin/sh\nif [ \"$1\" = list ]; then printf '[{\"id\":\"from-br\",\"title\":\"selected\"}]'; else printf '[]'; fi\n"); await chmod(script, 0o755); const oldPath = process.env.PATH; const oldBackend = process.env.PO_BEADS_BACKEND; process.env.PATH = `${bin}:${oldPath}`; process.env.PO_BEADS_BACKEND = "br";
    try { expect((await fetchBeads(".")).data[0]?.id).toBe("from-br"); } finally { process.env.PATH = oldPath; if (oldBackend === undefined) delete process.env.PO_BEADS_BACKEND; else process.env.PO_BEADS_BACKEND = oldBackend; }
  });
  test("Beads adapter excludes deleted and tombstoned records", async () => {
    const bin = await mkdtemp(join(tmpdir(), "po-tui-deleted-")); const script = join(bin, "br"); await writeFile(script, "#!/bin/sh\nif [ \"$1\" = list ]; then printf '[{\"id\":\"live\",\"status\":\"open\"},{\"id\":\"gone\",\"status\":\"tombstone\"}]'; else printf '[]'; fi\n"); await chmod(script, 0o755);
    const oldPath = process.env.PATH; const oldBackend = process.env.PO_BEADS_BACKEND; process.env.PATH = `${bin}:${oldPath}`; process.env.PO_BEADS_BACKEND = "br";
    try { expect((await fetchBeads(".")).data.map((row) => row.id)).toEqual(["live"]); } finally { process.env.PATH = oldPath; if (oldBackend === undefined) delete process.env.PO_BEADS_BACKEND; else process.env.PO_BEADS_BACKEND = oldBackend; }
  });
  test("single Beads reads expose authoritative comments for verification", async () => {
    const bin = await mkdtemp(join(tmpdir(), "po-tui-show-")); const script = join(bin, "br"); await writeFile(script, "#!/bin/sh\nif [ \"$1\" = show ]; then printf '[{\"id\":\"po-1\",\"comments\":[{\"text\":\"verified note\"}]}]'; else printf '[]'; fi\n"); await chmod(script, 0o755);
    const oldPath = process.env.PATH; const oldBackend = process.env.PO_BEADS_BACKEND; process.env.PATH = `${bin}:${oldPath}`; process.env.PO_BEADS_BACKEND = "br";
    try { expect((await fetchBead(".", "po-1"))?.comments?.[0]?.text).toBe("verified note"); } finally { process.env.PATH = oldPath; if (oldBackend === undefined) delete process.env.PO_BEADS_BACKEND; else process.env.PO_BEADS_BACKEND = oldBackend; }
  });

  test("artifact discovery is bounded by type and depth", async () => {
    const root = await mkdtemp(join(tmpdir(), "po-tui-")); const nested = join(root, ".planning", "formula", "run", "review-artifacts", "iter-2"); await mkdir(nested, {recursive: true}); await writeFile(join(nested, "gate.txt"), "green"); await writeFile(join(nested, "secret.bin"), "skip");
    const snapshot = await fetchArtifacts(root, undefined, new Set(["run"])); expect(snapshot.data.map((item) => item.name)).toEqual(["gate.txt"]); expect(snapshot.data[0]?.ownerId).toBe("run");
  });
  test("normalizes backend array/object variants and rejects malformed JSON", () => {
    expect(arrayPayload<{id: string}>([{id: "br"}])).toEqual([{id: "br"}]); expect(arrayPayload<{id: string}>({issues: [{id: "dolt"}]})).toEqual([{id: "dolt"}]);
    expect(() => JSON.parse("{malformed")).toThrow();
  });
  test("surfaces non-zero subprocess exits", async () => { expect(checked(process.execPath, ["-e", "process.stderr.write('bad'); process.exit(7)"])).rejects.toThrow("exited 7: bad"); });
  test("artifact read errors remain local", async () => { expect(readArtifact("/definitely/missing/artifact.txt")).rejects.toThrow(); });
});

describe("independent refresh controllers", () => {
  test("a slow source does not delay a fast source", async () => {
    const seen: string[] = [];
    const slow = new SourceController<string[]>({intervalMs: 10_000, load: async () => { await Bun.sleep(50); return healthy("prefect", ["slow"]); }});
    const fast = new SourceController<string[]>({intervalMs: 10_000, load: async () => healthy("beads", ["fast"])});
    slow.subscribe(() => seen.push("slow")); fast.subscribe(() => seen.push("fast")); slow.start(); fast.start(); await Bun.sleep(10);
    expect(seen).toEqual(["fast"]); await Bun.sleep(50); expect(seen).toEqual(["fast", "slow"]); slow.stop(); fast.stop();
  });
  test("backs off failures, retains stale data, and recovers", async () => {
    let call = 0; const states: string[] = [];
    const controller = new SourceController<number[]>({intervalMs: 5, maxBackoffMs: 10, jitter: () => 0, load: async (_signal, previous) => { call++; if (call === 1) return healthy("beads", [1]); if (call === 2) return unhealthy("beads", [], new Error("down"), previous); return healthy("beads", [2]); }});
    controller.subscribe((snapshot) => states.push(`${snapshot.freshness}:${String(snapshot.data[0])}`)); controller.start(); await Bun.sleep(35); controller.stop();
    expect(states).toEqual(expect.arrayContaining(["fresh:1", "stale:1", "fresh:2"]));
  });
  test("manual refresh is duplicate-safe and stop aborts slow work", async () => {
    let calls = 0; let published = 0; const controller = new SourceController({intervalMs: 10_000, timeoutMs: 1000, load: async (signal) => { calls++; await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), {once: true})); return healthy("tmux", []); }});
    controller.subscribe(() => published++); controller.start(); void controller.refreshNow(); await Bun.sleep(2); expect(calls).toBe(1); controller.stop(); await Bun.sleep(2); expect(published).toBe(0);
  });
});
