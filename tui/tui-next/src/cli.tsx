#!/usr/bin/env bun
import {parseArgs} from "node:util";
import {spawn} from "node:child_process";
import React from "react";
import {render} from "ink";
import {App} from "./app/App.js";
import {installTerminalLifecycle} from "./app/lifecycle.js";
import {reconcile} from "./domain/model.js";
import {fetchArtifacts, fetchBeads, fetchPrefect} from "./sources/adapters.js";

const {values} = parseArgs({options: {
  "rig-path": {type: "string"}, "prefect-url": {type: "string"}, "refresh-ms": {type: "string"},
  ascii: {type: "boolean"}, plain: {type: "boolean"}, help: {type: "boolean", short: "h"},
}, strict: false});
const stringOption = (value: string | boolean | undefined, fallback: string): string => typeof value === "string" ? value : fallback;
const rigPath = stringOption(values["rig-path"], process.cwd());
const prefectUrl = stringOption(values["prefect-url"], process.env.PREFECT_API_URL ?? "http://127.0.0.1:4200/api").replace(/\/$/, "");
const refreshMs = Math.max(1000, Number(stringOption(values["refresh-ms"], "5000")));
const ascii = Boolean(values.ascii) || process.env.TERM === "dumb";

async function attachTmux(target: string): Promise<void> {
  if (process.env.PO_TUI_ATTACH_DRY_RUN === "1") { process.stdout.write(`Attach with: tmux attach -t ${target}\n`); return; }
  const code = await new Promise<number>((resolve, reject) => {
    const child = spawn("tmux", ["attach", "-t", target], {stdio: "inherit"});
    child.once("error", reject); child.once("exit", (value) => resolve(value ?? 0));
  });
  if (code !== 0) throw new Error(`tmux attach exited ${code}`);
}

function usage(): string { return `po tui — epic-first PO operations console

Usage: po tui [--rig-path PATH] [--prefect-url URL] [--refresh-ms N] [--ascii] [--plain]

Interactive: arrows or hjkl navigate, Enter drills in, : opens every operator
action, ? opens help, and q exits. Non-TTY output automatically uses plain mode.`; }

async function plain(): Promise<void> {
  const [beads, prefect] = await Promise.all([fetchBeads(rigPath), fetchPrefect(prefectUrl)]);
  const artifacts = await fetchArtifacts(rigPath, undefined, new Set(beads.data.map((item) => item.id)));
  const model = reconcile(beads.data, prefect.data, artifacts.data); const children = model.epics.reduce((count, epic) => count + epic.children.length, 0);
  process.stdout.write(`PO operations · ${model.epics.length} epics · ${children} child issues · ${model.standalone.length} standalone\n`);
  for (const epic of model.epics.slice(0, 20)) process.stdout.write(`${epic.state.padEnd(12)} ${epic.id}  ${epic.title}  (${epic.children.length} children)\n`);
  for (const [source, snapshot] of Object.entries({beads, prefect, artifacts})) if (snapshot.error) process.stdout.write(`source ${source}: ${snapshot.freshness} — ${snapshot.error}\n`);
}

async function main(): Promise<void> {
  if (values.help) { process.stdout.write(`${usage()}\n`); return; }
  if (values.plain || !process.stdin.isTTY || !process.stdout.isTTY || process.env.CI) { await plain(); return; }
  let app: ReturnType<typeof render> | undefined;
  let attachTarget: string | undefined;
  const lifecycle = installTerminalLifecycle(process.stdout, () => app?.rerender(<App rigPath={rigPath} prefectUrl={prefectUrl} refreshMs={refreshMs} ascii={ascii} />));
  const fatal = (error: unknown) => { lifecycle.restore(); process.stderr.write(`po tui: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`); process.exit(1); };
  process.once("uncaughtException", fatal); process.once("unhandledRejection", fatal);
  try {
    if (process.env.PO_TUI_TEST_FAILURE === "throw") throw new Error("intentional PTY crash test");
    if (process.env.PO_TUI_TEST_FAILURE === "reject") queueMicrotask(() => void Promise.reject(new Error("intentional PTY rejection test")));
    if (process.env.PO_TUI_TEST_ATTACH_TARGET) { lifecycle.restore(); await attachTmux(process.env.PO_TUI_TEST_ATTACH_TARGET); return; }
    app = render(<App rigPath={rigPath} prefectUrl={prefectUrl} refreshMs={refreshMs} ascii={ascii} onAttach={(target) => { attachTarget = target; lifecycle.restore(); app?.unmount(); }} />, {exitOnCtrlC: false, patchConsole: false});
    await app.waitUntilExit();
  } finally { lifecycle.dispose(); process.off("uncaughtException", fatal); process.off("unhandledRejection", fatal); }
  if (attachTarget) await attachTmux(attachTarget);
}

void main().catch((error) => { process.stderr.write(`po tui: ${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
