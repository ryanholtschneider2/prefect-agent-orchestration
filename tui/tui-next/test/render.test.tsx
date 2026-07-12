import React from "react";
import {afterEach, describe, expect, test} from "bun:test";
import {renderToString} from "ink";
import {cleanup, render} from "ink-testing-library";
import {App} from "../src/app/App.js";
import {Detail} from "../src/components/Detail.js";
import {WorkTree} from "../src/components/Tree.js";
import {initialState, reducer} from "../src/state/store.js";
import {theme} from "../src/theme/theme.js";
import {fixtureModel} from "./fixtures.js";

afterEach(cleanup);
const state = (() => { let value = reducer(initialState(), {type: "model", model: fixtureModel()}); return reducer(value, {type: "toggle", id: "po-road"}); })();

describe("responsive rendering", () => {
  test.each([160, 100, 80, 60])("tree renders deterministically at %i columns", (columns) => {
    const frame = renderToString(<WorkTree state={state} width={columns} height={20} colors={theme(true)} ascii={columns === 60} />, {columns});
    expect(frame).toContain("Formula graph migration"); expect(frame).toContain("Replace verdict channel"); expect(frame.split("\n").every((line) => line.length <= columns)).toBeTrue();
  });
  test("epic and child detail expose required facts", () => {
    const epic = renderToString(<Detail object={fixtureModel().epics[0]} state={state} width={78} height={22} colors={theme(true)} />, {columns: 80});
    expect(epic).toContain("Progress"); expect(epic).toContain("Blockers & decisions"); expect(epic).toContain("Active work");
    const issue = renderToString(<Detail object={fixtureModel().epics[0]!.children[0]} state={state} width={78} height={22} colors={theme(true)} />, {columns: 80});
    expect(issue).toContain("Current attempt"); expect(issue).toContain("Role timeline"); expect(issue).toContain("Artifacts");
  });
  test("below-minimum app renders a clear explanation", () => {
    const app = render(<App rigPath="." prefectUrl="http://invalid" refreshMs={0} initialModel={fixtureModel()} />); expect(app.lastFrame()).toContain("PO");
  });
});

describe("keyboard interaction", () => {
  test("opens palette, filters, and cancels", async () => {
    const app = render(<App rigPath="." prefectUrl="http://invalid" refreshMs={0} initialModel={fixtureModel()} />);
    await Bun.sleep(10); app.stdin.write(":"); await Bun.sleep(5); for (const char of "pause") { app.stdin.write(char); await Bun.sleep(5); } expect(app.lastFrame()).toContain("Pause epic"); app.stdin.write("\u001b"); await Bun.sleep(120); expect(app.lastFrame()).not.toContain("Preview");
  });
  test("expands an epic without losing selection", async () => {
    const app = render(<App rigPath="." prefectUrl="http://invalid" refreshMs={0} initialModel={fixtureModel()} />); await Bun.sleep(10); app.stdin.write("l"); await Bun.sleep(10); expect(app.lastFrame()).toContain("Replace verdic");
  });
});
