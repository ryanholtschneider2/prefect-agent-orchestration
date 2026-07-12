#!/usr/bin/env bun
import {parseArgs} from "node:util";
import React from "react";
import {render} from "ink";
import {App} from "./app/App.js";
import {installTerminalLifecycle} from "./app/lifecycle.js";
import {reconcile} from "./domain/model.js";
import {fetchArtifacts, fetchBeads, fetchPrefect} from "./sources/adapters.js";

const {values} = parseArgs({options: {
  rigPath: {type: "string"}, prefectUrl: {type: "string"}, refreshMs: {type: "string"},
  ascii: {type: "boolean"}, plain: {type: "boolean"}, help: {type: "boolean", short: "h"},
}, strict: false});
const stringOption = (value: string | boolean | undefined, fallback: string): string => typeof value === "string" ? value : fallback;
const rigPath = stringOption(values.rigPath, process.cwd());
const prefectUrl = stringOption(values.prefectUrl, process.env.PREFECT_API_URL ?? "http://127.0.0.1:4200/api").replace(/\/$/, "");
const refreshMs = Math.max(1000, Number(stringOption(values.refreshMs, "5000")));

function usage(): string { return `po tui — epic-first PO operations console

Usage: po tui [--rig-path PATH] [--prefect-url URL] [--refresh-ms N] [--ascii] [--plain]

Interactive: arrows or hjkl navigate, Enter drills in, : opens every operator
action, ? opens help, and q exits. Non-TTY output automatically uses plain mode.`; }

async function plain(): Promise<void> {
  const [beads, prefect, artifacts] = await Promise.all([fetchBeads(rigPath), fetchPrefect(prefectUrl), fetchArtifacts(rigPath)]);
  const model = reconcile(beads.data, prefect.data, artifacts.data); const children = model.epics.reduce((count, epic) => count + epic.children.length, 0);
  process.stdout.write(`PO operations · ${model.epics.length} epics · ${children} child issues · ${model.standalone.length} standalone\n`);
  for (const epic of model.epics.slice(0, 20)) process.stdout.write(`${epic.state.padEnd(12)} ${epic.id}  ${epic.title}  (${epic.children.length} children)\n`);
  for (const [source, snapshot] of Object.entries({beads, prefect, artifacts})) if (snapshot.error) process.stdout.write(`source ${source}: ${snapshot.freshness} — ${snapshot.error}\n`);
}

async function main(): Promise<void> {
  if (values.help) { process.stdout.write(`${usage()}\n`); return; }
  if (values.plain || !process.stdin.isTTY || !process.stdout.isTTY || process.env.CI) { await plain(); return; }
  let app: ReturnType<typeof render> | undefined;
  const lifecycle = installTerminalLifecycle(process.stdout, () => app?.rerender(<App rigPath={rigPath} prefectUrl={prefectUrl} refreshMs={refreshMs} ascii={Boolean(values.ascii)} />));
  const fatal = (error: unknown) => { lifecycle.restore(); process.stderr.write(`po tui: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`); process.exit(1); };
  process.once("uncaughtException", fatal); process.once("unhandledRejection", fatal);
  try {
    app = render(<App rigPath={rigPath} prefectUrl={prefectUrl} refreshMs={refreshMs} ascii={Boolean(values.ascii)} onAttach={(target) => { lifecycle.restore(); app?.unmount(); process.stdout.write(`Attach with: tmux attach -t ${target}\n`); }} />, {exitOnCtrlC: false, patchConsole: false});
    await app.waitUntilExit();
  } finally { lifecycle.dispose(); process.off("uncaughtException", fatal); process.off("unhandledRejection", fatal); }
}

void main().catch((error) => { process.stderr.write(`po tui: ${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
