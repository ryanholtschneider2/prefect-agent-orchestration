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
  test.each([[160,48,"EPIC OVERVIEW"],[100,30,"EPIC OVERVIEW"],[80,24,"overview  activity"],[60,24,"ACTIVE"],[50,16,"Terminal too small"]] as const)("full app renders %ix%i breakpoint", async (columns, rows, expected) => {
    const app = render(<App rigPath="." prefectUrl="http://invalid" refreshMs={0} initialModel={fixtureModel()} dimensions={{columns, rows}} />); await Bun.sleep(10); expect(app.lastFrame()).toContain(expected);
  });
  test.each([160, 100, 80, 60])("tree renders deterministically at %i columns", (columns) => {
    const frame = renderToString(<WorkTree state={state} width={columns} height={20} colors={theme(true)} ascii={columns === 60} />, {columns});
    expect(frame).toContain("Formula graph migration"); expect(frame).toContain("Replace verdict channel"); expect(frame.split("\n").every((line) => line.length <= columns)).toBeTrue();
  });
  test("tree keeps a selected row visible across lifecycle headers", () => {
    const model = fixtureModel(); model.standalone = Array.from({length: 12}, (_, index) => ({kind: "issue" as const, id: `closed-${index}`, title: `Closed issue ${index}`, state: "closed" as const, dependencies: [], artifacts: [], sessions: [], comments: [], attempts: []}));
    let selected = reducer(initialState(), {type: "model", model}); selected = reducer(selected, {type: "select", id: "closed-11"});
    const frame = renderToString(<WorkTree state={selected} width={60} height={5} colors={theme(true)} ascii />, {columns: 60});
    expect(frame).toContain("Closed issue 11");
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
  test("renders empty and partial-source states", async () => {
    const empty = fixtureModel(); empty.epics = []; empty.standalone = []; empty.snapshots.prefect = {...empty.snapshots.prefect, freshness: "stale", error: "HTTP 503"};
    const app = render(<App rigPath="." prefectUrl="http://invalid" refreshMs={0} initialModel={empty} dimensions={{columns: 80, rows: 24}} />); await Bun.sleep(10); expect(app.lastFrame()).toContain("No work in this scope"); expect(app.lastFrame()).toContain("!p");
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
  test("collects structured arguments and renders a concrete preview", async () => {
    const app = render(<App rigPath="/rig" prefectUrl="http://invalid" refreshMs={0} initialModel={fixtureModel()} />); await Bun.sleep(10); app.stdin.write(":"); await Bun.sleep(5);
    for (const char of "state") { app.stdin.write(char); await Bun.sleep(3); } app.stdin.write("\r"); await Bun.sleep(10);
    expect(app.lastFrame()).toContain("State (required)"); for (let index = 0; index < "in_progress".length; index++) { app.stdin.write("\x7f"); await Bun.sleep(2); } for (const char of "closed") { app.stdin.write(char); await Bun.sleep(2); } app.stdin.write("\r"); await Bun.sleep(10);
    expect(app.lastFrame()).toContain("bd update po-road --status closed"); app.stdin.write("\u001b"); await Bun.sleep(120); expect(app.lastFrame()).not.toContain("Concrete preview");
  });
  test("destructive commands require the exact selected ID and default-cancel", async () => {
    const app = render(<App rigPath="." prefectUrl="http://invalid" refreshMs={0} initialModel={fixtureModel()} />); await Bun.sleep(10); app.stdin.write(":"); await Bun.sleep(5);
    for (const char of "pause") { app.stdin.write(char); await Bun.sleep(3); } app.stdin.write("\r"); await Bun.sleep(5); app.stdin.write("\r"); await Bun.sleep(10);
    expect(app.lastFrame()).toContain("Type the exact ID"); expect(app.lastFrame()).toContain("po-road"); app.stdin.write("\u001b"); await Bun.sleep(120); expect(app.lastFrame()).not.toContain("Destructive confirmation");
  });
});
