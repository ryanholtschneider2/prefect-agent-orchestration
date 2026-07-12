import {describe, expect, test} from "bun:test";
import {ActionCoordinator, actions, executeAction, filterActions} from "../src/actions/registry.js";
import {initialState, reducer, selectedObject, visibleObjects} from "../src/state/store.js";
import {fixtureModel} from "./fixtures.js";

describe("navigation state", () => {
  test("keeps selection and expansion across refresh", () => {
    let state = reducer(initialState(), {type: "model", model: fixtureModel()});
    state = reducer(state, {type: "toggle", id: "po-road"}); state = reducer(state, {type: "select", id: "po-child"});
    state = reducer(state, {type: "model", model: fixtureModel()});
    expect(state.selectedId).toBe("po-child"); expect(state.expanded.has("po-road")).toBeTrue();
  });
  test("falls back when selection disappears", () => {
    let state = reducer(initialState(), {type: "model", model: fixtureModel()}); state = reducer(state, {type: "select", id: "gone"}); state = reducer(state, {type: "model", model: fixtureModel()});
    expect(selectedObject(state)?.id).toBe("po-road");
  });
  test("filters lifecycle scope without losing standalone work", () => {
    let state = reducer(initialState(), {type: "model", model: fixtureModel()}); state = reducer(state, {type: "scope", scope: "active"});
    expect(visibleObjects(state).map((item) => item.id)).toContain("po-loose");
  });
  test("keeps keyboard selection inside the visible window", () => {
    const model = fixtureModel(); model.standalone = Array.from({length: 20}, (_, index) => ({kind: "issue" as const, id: `loose-${index}`, title: `Issue ${index}`, state: "open" as const, dependencies: [], artifacts: [], sessions: [], comments: [], attempts: []}));
    let state = reducer(initialState(), {type: "model", model}); for (let index = 0; index < 12; index++) state = reducer(state, {type: "move", delta: 1, viewport: 5});
    expect(state.scroll).toBeGreaterThan(0); expect(visibleObjects(state).findIndex((item) => item.id === state.selectedId)).toBeLessThan(state.scroll + 5);
    state = reducer(state, {type: "model", model}); expect(state.scroll).toBeGreaterThan(0);
  });
  test("live-output scrolling disables follow until jump-to-bottom", () => {
    let state = reducer(initialState(), {type: "liveScroll", delta: 5}); expect(state.followOutput).toBeFalse(); expect(state.liveScroll).toBe(5);
    state = reducer(state, {type: "follow", value: true}); expect(state.followOutput).toBeTrue(); expect(state.liveScroll).toBe(0);
  });
});

describe("command discovery", () => {
  const issue = fixtureModel().epics[0]!.children[0]!;
  test("ranks aliases and contextual actions", () => { expect(filterActions("rer", issue)[0]?.id).toBe("retry"); expect(filterActions("Pause", issue).map((item) => item.id)).not.toContain("pause"); });
  test("smart-case is respected", () => { expect(filterActions("PREFECT", issue)).toHaveLength(0); expect(filterActions("prefect", issue)[0]?.id).toBe("prefect"); });
  test("every approved operator action is registered", () => { expect(filterActions("", issue).map((item) => item.id)).toEqual(expect.arrayContaining(["dispatch", "retry", "resume", "cancel", "attach", "prefect", "artifact", "state", "comment", "refresh", "diagnostics", "scope"])); });
  test("dispatch declares the complete runtime tuple", () => { expect(actions.find((item) => item.id === "dispatch")?.arguments?.map((item) => item.key)).toEqual(["formula", "backend", "provider", "account", "accountClass", "model", "effort", "rig", "rigPath"]); });
});

describe("mutation lifecycle", () => {
  const issue = fixtureModel().epics[0]!.children[0]!; const retry = actions.find((item) => item.id === "retry")!;
  test("suppresses an identical in-flight operation", async () => {
    let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
    const coordinator = new ActionCoordinator(async () => { await gate; return {state: "verified", message: "done"}; });
    const first = coordinator.run(retry, issue, ".", {}); expect(coordinator.isInFlight(`retry:${issue.id}`)).toBeTrue();
    expect(await coordinator.run(retry, issue, ".", {})).toMatchObject({state: "failed", message: expect.stringContaining("already")}); release(); expect((await first).state).toBe("verified");
  });
  test("re-reads until authoritative verification succeeds", async () => {
    let checks = 0; const coordinator = new ActionCoordinator(async () => ({state: "pending", message: "sent"}), async () => checks++ >= 1 ? true : undefined, 100, 1);
    expect(await coordinator.run(retry, issue, ".", {})).toMatchObject({state: "verified", message: expect.stringContaining("authoritative")});
  });
  test("returns pending when the verification window expires", async () => {
    const coordinator = new ActionCoordinator(async () => ({state: "pending", message: "sent"}), async () => undefined, 5, 1);
    expect(await coordinator.run(retry, issue, ".", {})).toMatchObject({state: "pending", message: expect.stringContaining("expired")});
  });
  test("normalizes executor errors and releases the lock", async () => {
    const coordinator = new ActionCoordinator(async () => { throw new Error("command failed"); });
    expect(await coordinator.run(retry, issue, ".", {})).toEqual({state: "failed", message: "command failed"}); expect(coordinator.isInFlight(`retry:${issue.id}`)).toBeFalse();
  });
  test("pause and resume use supported Prefect orchestration endpoints", async () => {
    const requests: Array<{path: string; body: unknown}> = []; const api = Bun.serve({port: 0, fetch: async (request) => { requests.push({path: new URL(request.url).pathname, body: await request.json()}); return Response.json({status: "ACCEPT"}); }});
    try {
      const pause = actions.find((item) => item.id === "pause")!; const epic = fixtureModel().epics[0]!;
      expect((await executeAction(pause, epic, ".", {prefectApi: `http://127.0.0.1:${api.port}`})).state).toBe("pending");
      epic.children[0]!.attempts[0]!.state = "PAUSED"; const resume = actions.find((item) => item.id === "resume")!;
      expect((await executeAction(resume, epic.children[0], ".", {prefectApi: `http://127.0.0.1:${api.port}`})).state).toBe("pending");
      expect(requests.map((item) => item.path)).toEqual(["/flow_runs/12345678-flow/set_state", "/flow_runs/12345678-flow/resume"]); expect(requests[0]!.body).toMatchObject({state: {type: "PAUSED"}, force: false});
    } finally { api.stop(true); }
  });
  test("pause/resume reject unsupported states and API failures", async () => {
    const issue = fixtureModel().epics[0]!.children[0]!; const resume = actions.find((item) => item.id === "resume")!;
    expect((await executeAction(resume, issue, ".", {prefectApi: "http://invalid"})).message).toContain("expected PAUSED"); issue.attempts[0]!.state = "PAUSED";
    const api = Bun.serve({port: 0, fetch: () => new Response("denied", {status: 409})}); try { expect((await executeAction(resume, issue, ".", {prefectApi: `http://127.0.0.1:${api.port}`}))).toMatchObject({state: "failed", message: expect.stringContaining("409")}); } finally { api.stop(true); }
  });
  test("artifact and attach require an explicitly discovered choice", async () => {
    const issue = fixtureModel().epics[0]!.children[0]!; const attach = actions.find((item) => item.id === "attach")!; const artifact = actions.find((item) => item.id === "artifact")!;
    expect(await executeAction(attach, issue, ".", {sessionTarget: "fabricated"})).toMatchObject({state: "failed"}); expect(await executeAction(attach, issue, ".", {sessionTarget: issue.sessions[0]!.target})).toMatchObject({attachTarget: "po-po-child-builder"});
    expect(await executeAction(artifact, issue, ".", {artifactPath: "/not/discovered"})).toMatchObject({state: "failed"});
  });
});
