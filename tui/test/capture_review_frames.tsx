import React from "react";
import {mkdir} from "node:fs/promises";
import {join} from "node:path";
import {render} from "ink-testing-library";
import {App} from "../tui-next/src/app/App.js";
import {fixtureModel} from "../tui-next/test/fixtures.js";

const output = process.argv[2];
if (!output) throw new Error("usage: capture_review_frames.tsx <output-dir>");
await mkdir(output, {recursive: true});

const escape = (value: string) => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
async function capture(name: string, columns: number, rows: number, keys = "", degraded = false) {
  const model = fixtureModel();
  if (degraded) model.snapshots.prefect = {...model.snapshots.prefect, freshness: "stale", error: "Prefect HTTP 503; last-good snapshot retained"};
  const app = render(<App rigPath="/fixture/rig" prefectUrl="http://127.0.0.1:4200/api" refreshMs={0} initialModel={model} dimensions={{columns, rows}} />);
  await Bun.sleep(20);
  for (const key of keys) { app.stdin.write(key); await Bun.sleep(8); }
  const frame = app.lastFrame() ?? ""; app.unmount();
  const lines = frame.split("\n"); const width = columns * 8 + 32; const height = Math.max(rows, lines.length) * 17 + 32;
  const text = lines.map((line, index) => `<text x="16" y="${28 + index * 17}">${escape(line)}</text>`).join("\n");
  await Bun.write(join(output, `${name}.svg`), `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="#111418"/><g xml:space="preserve" fill="#e6e2d9" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="13">${text}</g></svg>\n`);
  await Bun.write(join(output, `${name}.txt`), frame + "\n");
}

await capture("wide-160x48", 160, 48);
await capture("compact-80x24", 80, 24);
await capture("narrow-60x24", 60, 24);
await capture("below-minimum-50x16", 50, 16);
await capture("command-palette", 100, 30, ":pause");
await capture("artifact-choice", 100, 30, ":artifact\r");
await capture("degraded-prefect", 80, 24, "", true);
