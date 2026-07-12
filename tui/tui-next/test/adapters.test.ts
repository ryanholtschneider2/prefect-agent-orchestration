import {afterEach, describe, expect, test} from "bun:test";
import {mkdtemp, mkdir, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fetchArtifacts, fetchPrefect, healthy, unhealthy} from "../src/sources/adapters.js";

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
});
