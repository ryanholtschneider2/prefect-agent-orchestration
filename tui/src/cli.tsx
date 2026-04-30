#!/usr/bin/env bun
/**
 * Entry point. Parses CLI args, renders <App />, and after Ink exits,
 * checks for the attach sentinel — if present, execs `tmux attach`.
 */

import fs from "node:fs";
import { spawn } from "node:child_process";

import React from "react";
import { render } from "ink";
import meow from "meow";

import { App, ATTACH_SENTINEL } from "./App.js";

const cli = meow(
  `
  Usage
    $ po-tui [--epic <id>] [--prefect-url <url>] [--refresh-ms <n>] [--mobile]

  Options
    --epic          Filter to a single epic (matches Prefect tag epic_id:<id>)
    --prefect-url   Prefect API base URL (default: http://127.0.0.1:4200/api,
                    or $PREFECT_API_URL)
    --refresh-ms    Poll cadence in milliseconds (default: 2000)
    --mobile        Single-column stacked layout for narrow terminals (Termius
                    etc). Auto-enabled when stdout columns < 80 or
                    $PO_TUI_MOBILE=1.

  Hotkeys
    ↑/↓     navigate issues
    a       attach to the active tmux pane (exits TUI, execs tmux attach)
    r       force refresh now
    /       filter issues
    q       quit
`,
  {
    importMeta: import.meta,
    flags: {
      epic: { type: "string" },
      prefectUrl: { type: "string" },
      refreshMs: { type: "number", default: 2000 },
      mobile: { type: "boolean" },
    },
  },
);

function resolveMobile(flag: boolean | undefined): boolean {
  if (flag !== undefined) return flag;
  if (process.env.PO_TUI_MOBILE === "1") return true;
  const cols = process.stdout.columns ?? 0;
  return cols > 0 && cols < 80;
}

async function main(): Promise<void> {
  // Best-effort cleanup of any stale sentinel from a prior run.
  try {
    fs.unlinkSync(ATTACH_SENTINEL);
  } catch {
    /* ignore */
  }

  const app = render(
    <App
      epicFilter={cli.flags.epic}
      prefectUrl={cli.flags.prefectUrl}
      refreshMs={cli.flags.refreshMs}
      mobile={resolveMobile(cli.flags.mobile)}
    />,
  );

  await app.waitUntilExit();

  // After Ink exits cleanly, see if user requested a tmux attach.
  let target: string | null = null;
  try {
    target = fs.readFileSync(ATTACH_SENTINEL, "utf8").trim();
    fs.unlinkSync(ATTACH_SENTINEL);
  } catch {
    /* no attach requested */
  }

  if (target) {
    // Hand the terminal to tmux. We use spawn with stdio:'inherit' rather
    // than execvp because Bun doesn't expose execvp directly; this is
    // equivalent for our purposes — TUI has already cleared the screen.
    const child = spawn("tmux", ["attach", "-t", target], { stdio: "inherit" });
    child.on("exit", (code) => process.exit(code ?? 0));
    return;
  }
}

main().catch((err) => {
  console.error("po-tui crashed:", err);
  process.exit(1);
});
