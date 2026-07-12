import {describe, expect, test} from "bun:test";
import {ActionCoordinator, actions, filterActions} from "../src/actions/registry.js";
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
});
