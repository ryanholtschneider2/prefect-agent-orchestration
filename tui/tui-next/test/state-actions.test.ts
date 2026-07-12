import {describe, expect, test} from "bun:test";
import {filterActions} from "../src/actions/registry.js";
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
});

describe("command discovery", () => {
  const issue = fixtureModel().epics[0]!.children[0]!;
  test("ranks aliases and contextual actions", () => { expect(filterActions("rer", issue)[0]?.id).toBe("retry"); expect(filterActions("Pause", issue).map((item) => item.id)).not.toContain("pause"); });
  test("smart-case is respected", () => { expect(filterActions("PREFECT", issue)).toHaveLength(0); expect(filterActions("prefect", issue)[0]?.id).toBe("prefect"); });
  test("every approved operator action is registered", () => { expect(filterActions("", issue).map((item) => item.id)).toEqual(expect.arrayContaining(["dispatch", "retry", "resume", "cancel", "attach", "prefect", "artifact", "state", "comment", "refresh", "diagnostics", "scope"])); });
});
