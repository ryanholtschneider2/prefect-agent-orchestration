#!/usr/bin/env bun
/** Hermetic real-stack smoke for the TUI dispatch and retry executors. */

import {rm} from "node:fs/promises";
import {resolve} from "node:path";
import {actions, executeAction} from "../tui-next/src/actions/registry.js";
import type {Issue} from "../tui-next/src/domain/model.js";
import {fetchBead, fetchPrefect} from "../tui-next/src/sources/adapters.js";
import {checked} from "../tui-next/src/sources/process.js";

const rigPath = resolve(import.meta.dir, "../..");
const prefectUrl = (process.env.PREFECT_API_URL ?? "http://127.0.0.1:4200/api").replace(/\/$/, "");
const dispatch = actions.find((action) => action.id === "dispatch")!;
const retry = actions.find((action) => action.id === "retry")!;

const waitForClosed = async (issueId: string, timeoutMs = 60_000): Promise<void> => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if ((await fetchBead(rigPath, issueId))?.status === "closed") return;
    await Bun.sleep(500);
  }
  throw new Error(`${issueId} did not close within ${timeoutMs}ms`);
};

const attemptsFor = async (issueId: string) => {
  const snapshot = await fetchPrefect(prefectUrl, undefined, undefined, issueId);
  if (snapshot.freshness !== "fresh") throw new Error(snapshot.error ?? "Prefect unavailable");
  return snapshot.data.filter((attempt) => attempt.issueId === issueId);
};

const created = JSON.parse(await checked("bd", ["create", "TUI dispatch/retry real-stack smoke", "--type", "task", "--priority", "4", "--ephemeral", "--json"], {cwd: rigPath})) as {id: string};
const issueId = created.id;
const issue = (): Issue => ({kind: "issue", id: issueId, title: "TUI action smoke", state: "open", dependencies: [], attempts: [], artifacts: [], sessions: [], comments: []});

try {
  await checked("bd", ["comments", "add", issueId, "PO runtime: formula=software-dev-agentic backend=stub provider=openai account=codex-personal account_class=personal model=gpt-5.4 effort=low rig=prefect-orchestration"], {cwd: rigPath});
  const before = await attemptsFor(issueId);
  const dispatched = await executeAction(dispatch, issue(), rigPath, {formula: "software-dev-agentic", backend: "stub", provider: "openai", account: "codex-personal", accountClass: "personal", model: "gpt-5.4", effort: "low", rig: "prefect-orchestration", rigPath, beadsBackend: "br"});
  if (dispatched.state === "failed") throw new Error(dispatched.message);
  await waitForClosed(issueId);
  const afterDispatch = await attemptsFor(issueId);
  if (!afterDispatch.some((attempt) => !before.some((prior) => prior.id === attempt.id))) throw new Error("dispatch produced no new Prefect attempt");
  const latest = afterDispatch[0];
  if (!latest) throw new Error("dispatch attempt missing");

  const retryIssue = {...issue(), state: "closed" as const, attempts: [latest]};
  const retried = await executeAction(retry, retryIssue, rigPath, {beadsBackend: "br", retryBackend: "stub"});
  if (retried.state === "failed") throw new Error(retried.message);
  await waitForClosed(issueId);
  const afterRetry = await attemptsFor(issueId);
  if (!afterRetry.some((attempt) => !afterDispatch.some((prior) => prior.id === attempt.id))) throw new Error("retry produced no new Prefect attempt");
  process.stdout.write(`PASS real-stack dispatch/retry: ${issueId} · ${afterDispatch[0]!.id.slice(0, 8)} -> ${afterRetry[0]!.id.slice(0, 8)}\n`);
} finally {
  await rm(resolve(rigPath, ".planning", "software-dev-agentic", `${issueId}.retry.lock`), {force: true});
  const rows = JSON.parse(await checked("bd", ["list", "--all", "--limit", "0", "--json"], {cwd: rigPath})) as Array<{id: string; title?: string}>;
  for (const row of rows.filter((item) => item.title?.endsWith(` for ${issueId}`))) {
    await checked("bd", ["delete", row.id, "--reason", "completed TUI real-stack smoke"], {cwd: rigPath});
  }
  await checked("bd", ["delete", issueId, "--cascade", "--reason", "completed TUI real-stack smoke"], {cwd: rigPath});
}
