import type {Epic, Issue} from "../domain/model.js";
import {run} from "../sources/process.js";

export type Selection = Epic | Issue;
export type ActionId = "dispatch" | "retry" | "pause" | "resume" | "cancel" | "attach" | "prefect" | "artifact" | "state" | "comment" | "refresh" | "diagnostics" | "scope";
export interface OperatorAction {
  id: ActionId; title: string; aliases: string[]; applies: Array<Selection["kind"] | "global">;
  destructive?: boolean; mutates?: boolean; arguments?: ArgumentSpec[];
  preview(selection?: Selection, args?: Record<string, string>): string;
}
export interface ArgumentSpec {key: string; label: string; required?: boolean; defaultValue?: () => string}
const env = (key: string, fallback: string) => () => process.env[key] ?? fallback;

export const actions: OperatorAction[] = [
  {id: "dispatch", title: "Dispatch child issue…", aliases: ["run", "start"], applies: ["issue"], mutates: true, arguments: [
    {key: "formula", label: "Formula", required: true, defaultValue: env("PO_FORMULA", "software-dev-agentic")}, {key: "backend", label: "Backend", required: true, defaultValue: env("PO_BACKEND", "codex-tmux")},
    {key: "provider", label: "Provider", required: true, defaultValue: env("PO_PROVIDER", "openai")}, {key: "account", label: "Account", required: true, defaultValue: env("PO_ACCOUNT", "codex-personal")},
    {key: "accountClass", label: "Account class", required: true, defaultValue: env("PO_ACCOUNT_CLASS", "personal")}, {key: "model", label: "Model", required: true, defaultValue: env("PO_MODEL", "gpt-5.4")},
    {key: "effort", label: "Effort", required: true, defaultValue: env("PO_EFFORT", "xhigh")}, {key: "rig", label: "Rig", required: true, defaultValue: env("PO_RIG", "prefect-orchestration")},
    {key: "rigPath", label: "Rig path", required: true, defaultValue: () => process.cwd()},
  ], preview: (s, a = {}) => `po run ${a.formula ?? "<formula>"} --backend ${a.backend ?? "<backend>"} --provider ${a.provider ?? "<provider>"} --account ${a.account ?? "<account>"} --account-class ${a.accountClass ?? "<class>"} --model ${a.model ?? "<model>"} --effort ${a.effort ?? "<effort>"} --issue-id ${s?.id ?? "<issue>"} --rig ${a.rig ?? "<rig>"} --rig-path ${a.rigPath ?? "<path>"}`},
  {id: "retry", title: "Retry latest attempt", aliases: ["rerun"], applies: ["issue"], mutates: true, preview: (s) => `po retry ${s?.id ?? "<issue>"}`},
  {id: "pause", title: "Pause epic…", aliases: ["hold"], applies: ["epic"], destructive: true, mutates: true, preview: (s) => `Pause active Prefect runs belonging to ${s?.id ?? "<epic>"}`},
  {id: "resume", title: "Resume paused run", aliases: ["continue"], applies: ["issue"], mutates: true, preview: (s) => `Resume the latest paused Prefect attempt for ${s?.id ?? "<issue>"}`},
  {id: "cancel", title: "Cancel current attempt…", aliases: ["stop", "terminate"], applies: ["issue"], destructive: true, mutates: true, preview: (s) => `Cancel the current Prefect attempt for ${s?.id ?? "<issue>"}; agent work may stop`},
  {id: "attach", title: "Attach to active agent", aliases: ["tmux", "session"], applies: ["issue"], preview: (s) => `tmux attach to the active role for ${s?.id ?? "<issue>"}`},
  {id: "prefect", title: "Open Prefect run", aliases: ["flow", "browser"], applies: ["issue"], preview: (s) => `Open the latest flow run for ${s?.id ?? "<issue>"}`},
  {id: "artifact", title: "Open artifact…", aliases: ["file", "evidence"], applies: ["issue", "epic"], preview: (s) => `Choose an artifact produced for ${s?.id ?? "selection"}`},
  {id: "state", title: "Update issue state…", aliases: ["beads", "status"], applies: ["issue", "epic"], mutates: true, arguments: [{key: "state", label: "State", required: true, defaultValue: env("PO_TUI_STATE", "in_progress")}], preview: (s, a = {}) => `bd update ${s?.id ?? "<issue>"} --status ${a.state ?? "<state>"}`},
  {id: "comment", title: "Add Beads comment…", aliases: ["note"], applies: ["issue", "epic"], mutates: true, arguments: [{key: "comment", label: "Comment", required: true}], preview: (s, a = {}) => `bd comments add ${s?.id ?? "<issue>"} "${a.comment ?? "<comment>"}"`},
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
  if (action.id === "artifact") {
    const path = selection.kind === "issue" ? selection.artifacts[0]?.path : selection.children.flatMap((issue) => issue.artifacts)[0]?.path;
    if (!path) return {state: "failed", message: "No artifact is available for this selection"};
    const result = await run("xdg-open", [path], {cwd: rigPath, timeoutMs: 5_000});
    return result.code === 0 ? {state: "verified", message: `Opened ${path}`} : {state: "failed", message: result.stderr.trim()};
  }
  if (action.id === "prefect") {
    const attempt = selection.kind === "issue" ? selection.attempts[0] : selection.children.flatMap((issue) => issue.attempts)[0];
    if (!attempt) return {state: "failed", message: "No Prefect attempt is linked to this selection"};
    const result = await run("xdg-open", [`${args.prefectUi ?? "http://127.0.0.1:4200"}/runs/flow-run/${attempt.id}`], {timeoutMs: 5_000});
    return result.code === 0 ? {state: "verified", message: `Opened Prefect run ${attempt.id}`} : {state: "failed", message: result.stderr.trim()};
  }
  let command = "po"; let commandArgs: string[] = [];
  if (action.id === "retry") commandArgs = ["retry", selection.id];
  else if (action.id === "dispatch") {
    const required = ["formula", "backend", "provider", "account", "accountClass", "model", "effort", "rig", "rigPath"];
    const missing = required.filter((key) => !args[key]);
    if (missing.length) return {state: "failed", message: `Dispatch needs: ${missing.join(", ")}`};
    commandArgs = ["run", args.formula!, "--backend", args.backend!, "--provider", args.provider!, "--account", args.account!, "--account-class", args.accountClass!, "--model", args.model!, "--effort", args.effort!, "--issue-id", selection.id, "--rig", args.rig!, "--rig-path", args.rigPath ?? rigPath];
  } else if (action.id === "state") { command = "bd"; commandArgs = ["update", selection.id, `--status=${args.state ?? "in_progress"}`]; }
  else if (action.id === "comment") { command = "bd"; commandArgs = ["comments", "add", selection.id, args.comment ?? "Updated from po tui"]; }
  else if (["pause", "resume", "cancel"].includes(action.id)) {
    const attempts = selection.kind === "issue" ? selection.attempts.slice(0, 1) : selection.children.flatMap((issue) => issue.attempts.slice(0, 1));
    if (!attempts.length) return {state: "failed", message: "No current Prefect attempt is linked to this selection"};
    const verb = action.id === "cancel" ? "cancel" : action.id;
    const outcomes = await Promise.all(attempts.map((attempt) => run("prefect", ["flow-run", verb, attempt.id], {cwd: rigPath, timeoutMs: 30_000})));
    const failed = outcomes.find((outcome) => outcome.code !== 0);
    return failed ? {state: "failed", message: failed.stderr.trim() || `prefect flow-run ${verb} failed`} : {state: "pending", message: `${verb} requested for ${attempts.length} attempt(s); Prefect verification pending`};
  }
  const result = await run(command, commandArgs, {cwd: rigPath, timeoutMs: 30_000});
  return result.code === 0 ? {state: "pending", message: `${command} ${commandArgs.join(" ")} completed; source verification pending`} : {state: "failed", message: result.stderr.trim() || `exit ${result.code}`};
}

export class ActionCoordinator {
  private readonly inFlight = new Set<string>();
  constructor(
    private readonly execute: typeof executeAction = executeAction,
    private readonly verify?: (action: OperatorAction, selection: Selection | undefined, args: Record<string, string>) => Promise<boolean | undefined>,
    private readonly verificationMs = 2_000,
    private readonly pollMs = 100,
  ) {}
  isInFlight(key: string): boolean { return this.inFlight.has(key); }
  async run(action: OperatorAction, selection: Selection | undefined, rigPath: string, args: Record<string, string>): Promise<{state: "verified" | "pending" | "failed"; message: string; attachTarget?: string}> {
    const key = `${action.id}:${selection?.id ?? "global"}`;
    if (this.inFlight.has(key)) return {state: "failed", message: "An identical operation is already in flight"};
    this.inFlight.add(key);
    try {
      const result = await this.execute(action, selection, rigPath, args);
      if (result.state !== "pending" || !action.mutates || !this.verify) return result;
      const deadline = Date.now() + this.verificationMs;
      while (Date.now() < deadline) {
        const verified = await this.verify(action, selection, args);
        if (verified === true) return {...result, state: "verified", message: `${result.message}; authoritative source verified`};
        if (verified === false) return {...result, state: "failed", message: `${result.message}; authoritative source rejected the requested state`};
        await new Promise((resolve) => setTimeout(resolve, this.pollMs));
      }
      return {...result, state: "pending", message: `${result.message}; verification window expired`};
    }
    catch (error) { return {state: "failed", message: error instanceof Error ? error.message : String(error)}; }
    finally { this.inFlight.delete(key); }
  }
}
