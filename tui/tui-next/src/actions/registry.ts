import type {Epic, Issue} from "../domain/model.js";
import {run} from "../sources/process.js";

export type Selection = Epic | Issue;
export type ActionId = "dispatch" | "retry" | "pause" | "resume" | "cancel" | "attach" | "prefect" | "artifact" | "state" | "comment" | "refresh" | "diagnostics" | "scope";
export interface OperatorAction {
  id: ActionId; title: string; aliases: string[]; applies: Array<Selection["kind"] | "global">;
  destructive?: boolean; mutates?: boolean; preview(selection?: Selection): string;
}

export const actions: OperatorAction[] = [
  {id: "dispatch", title: "Dispatch child issue…", aliases: ["run", "start"], applies: ["issue"], mutates: true, preview: (s) => `po run <formula> --issue-id ${s?.id ?? "<issue>"} with explicit runtime tuple`},
  {id: "retry", title: "Retry latest attempt", aliases: ["rerun"], applies: ["issue"], mutates: true, preview: (s) => `po retry ${s?.id ?? "<issue>"}`},
  {id: "pause", title: "Pause epic…", aliases: ["hold"], applies: ["epic"], destructive: true, mutates: true, preview: (s) => `Pause active Prefect runs belonging to ${s?.id ?? "<epic>"}`},
  {id: "resume", title: "Resume paused run", aliases: ["continue"], applies: ["issue"], mutates: true, preview: (s) => `Resume the latest paused Prefect attempt for ${s?.id ?? "<issue>"}`},
  {id: "cancel", title: "Cancel current attempt…", aliases: ["stop", "terminate"], applies: ["issue"], destructive: true, mutates: true, preview: (s) => `Cancel the current Prefect attempt for ${s?.id ?? "<issue>"}; agent work may stop`},
  {id: "attach", title: "Attach to active agent", aliases: ["tmux", "session"], applies: ["issue"], preview: (s) => `tmux attach to the active role for ${s?.id ?? "<issue>"}`},
  {id: "prefect", title: "Open Prefect run", aliases: ["flow", "browser"], applies: ["issue"], preview: (s) => `Open the latest flow run for ${s?.id ?? "<issue>"}`},
  {id: "artifact", title: "Open artifact…", aliases: ["file", "evidence"], applies: ["issue", "epic"], preview: (s) => `Choose an artifact produced for ${s?.id ?? "selection"}`},
  {id: "state", title: "Update issue state…", aliases: ["beads", "status"], applies: ["issue", "epic"], mutates: true, preview: (s) => `bd update ${s?.id ?? "<issue>"} --status <state>`},
  {id: "comment", title: "Add Beads comment…", aliases: ["note"], applies: ["issue", "epic"], mutates: true, preview: (s) => `bd comments add ${s?.id ?? "<issue>"} <comment>`},
  {id: "refresh", title: "Refresh all sources", aliases: ["reload", "sync"], applies: ["global"], preview: () => "Refresh Beads, Prefect, tmux, and artifacts independently"},
  {id: "diagnostics", title: "Open source diagnostics", aliases: ["health", "errors"], applies: ["global"], preview: () => "Show source commands, endpoints, freshness, and errors"},
  {id: "scope", title: "Change scope…", aliases: ["filter", "lifecycle"], applies: ["global"], preview: () => "Choose all, active, blocked, failed, completed, or archived work"},
];

export function applicableActions(selection?: Selection): OperatorAction[] {
  return actions.filter((action) => action.applies.includes("global") || (selection && action.applies.includes(selection.kind)));
}

function score(haystack: string, query: string): number {
  if (!query) return 0;
  const smart = /[A-Z]/.test(query); const text = smart ? haystack : haystack.toLowerCase(); const needle = smart ? query : query.toLowerCase();
  let cursor = 0; let scoreValue = 0; let streak = 0;
  for (const char of needle) {
    const index = text.indexOf(char, cursor); if (index < 0) return -1;
    streak = index === cursor ? streak + 1 : 0; scoreValue += 10 + streak * 4 - index; cursor = index + 1;
  }
  return scoreValue + (text.startsWith(needle) ? 100 : 0);
}

export function filterActions(input: string, selection?: Selection): OperatorAction[] {
  return applicableActions(selection).map((action) => ({action, score: score(`${action.title} ${action.aliases.join(" ")}`, input)}))
    .filter((entry) => entry.score >= 0).sort((a, b) => b.score - a.score).map((entry) => entry.action);
}

export async function executeAction(action: OperatorAction, selection: Selection | undefined, rigPath: string, args: Record<string, string> = {}): Promise<{state: "verified" | "pending" | "failed"; message: string; attachTarget?: string}> {
  if (!selection && !action.applies.includes("global")) return {state: "failed", message: "No applicable selection"};
  if (action.id === "refresh" || action.id === "diagnostics" || action.id === "scope") return {state: "verified", message: action.title};
  if (!selection) return {state: "failed", message: "Selection required"};
  if (action.id === "attach") return {state: "verified", message: "Terminal handoff requested", attachTarget: `po-${selection.id}-${args.role ?? "builder"}`};
  if (action.id === "artifact" || action.id === "prefect") return {state: "pending", message: "Choose the concrete target in detail view"};
  let command = "po"; let commandArgs: string[] = [];
  if (action.id === "retry") commandArgs = ["retry", selection.id];
  else if (action.id === "dispatch") {
    const required = ["formula", "backend", "account", "accountClass", "model", "effort"];
    const missing = required.filter((key) => !args[key]);
    if (missing.length) return {state: "failed", message: `Dispatch needs: ${missing.join(", ")}`};
    commandArgs = ["run", args.formula!, "--backend", args.backend!, "--account", args.account!, "--account-class", args.accountClass!, "--model", args.model!, "--effort", args.effort!, "--issue-id", selection.id, "--rig", args.rig ?? selection.id.split("-")[0]!, "--rig-path", rigPath];
  } else if (action.id === "state") { command = "bd"; commandArgs = ["update", selection.id, `--status=${args.state ?? "in_progress"}`]; }
  else if (action.id === "comment") { command = "bd"; commandArgs = ["comments", "add", selection.id, args.comment ?? "Updated from po tui"]; }
  else if (["pause", "resume", "cancel"].includes(action.id)) return {state: "pending", message: `${action.title}: select a concrete attempt first`};
  const result = await run(command, commandArgs, {cwd: rigPath, timeoutMs: 30_000});
  return result.code === 0 ? {state: "pending", message: `${command} ${commandArgs.join(" ")} completed; source verification pending`} : {state: "failed", message: result.stderr.trim() || `exit ${result.code}`};
}
