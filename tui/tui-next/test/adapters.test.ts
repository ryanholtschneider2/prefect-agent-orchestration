import {afterEach, describe, expect, test} from "bun:test";
import {mkdtemp, mkdir, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {arrayPayload, fetchArtifacts, fetchPrefect, healthy, readArtifact, unhealthy} from "../src/sources/adapters.js";
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

  test("artifact discovery is bounded by type and depth", async () => {
    const root = await mkdtemp(join(tmpdir(), "po-tui-")); await mkdir(join(root, ".planning", "run"), {recursive: true}); await writeFile(join(root, ".planning", "run", "gate.txt"), "green"); await writeFile(join(root, ".planning", "run", "secret.bin"), "skip");
    const snapshot = await fetchArtifacts(root); expect(snapshot.data.map((item) => item.name)).toEqual(["gate.txt"]);
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
    let calls = 0; const controller = new SourceController({intervalMs: 10_000, timeoutMs: 1000, load: async (signal) => { calls++; await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), {once: true})); return healthy("tmux", []); }});
    controller.start(); void controller.refreshNow(); await Bun.sleep(2); expect(calls).toBe(1); controller.stop();
  });
});
