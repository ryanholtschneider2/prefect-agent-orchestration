import {readdir, readFile, stat} from "node:fs/promises";
import {join} from "node:path";
import type {Artifact, Attempt, RawBead, RoleExecution, SourceName, SourceSnapshot} from "../domain/model.js";
import {redact} from "../domain/text.js";
import {checked, run} from "./process.js";

const now = () => new Date().toISOString();
export const healthy = <T>(source: SourceName, data: T, diagnostic?: SourceSnapshot<T>["diagnostic"]): SourceSnapshot<T> => ({source, data, fetchedAt: now(), lastSuccessAt: now(), freshness: "fresh", diagnostic});
export const unhealthy = <T>(source: SourceName, data: T, error: unknown, previous?: SourceSnapshot<T>, diagnostic?: SourceSnapshot<T>["diagnostic"]): SourceSnapshot<T> => ({
  source, data: previous?.data ?? data, fetchedAt: now(), lastSuccessAt: previous?.lastSuccessAt,
  freshness: previous?.lastSuccessAt ? "stale" : "unavailable", error: redact(error instanceof Error ? error.message : String(error)), diagnostic,
});

export function arrayPayload<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object" && "issues" in value && Array.isArray((value as {issues: unknown}).issues)) return (value as {issues: T[]}).issues;
  return [];
}

export async function fetchBeads(rigPath: string, previous?: SourceSnapshot<RawBead[]>): Promise<SourceSnapshot<RawBead[]>> {
  const binaries = process.env.PO_BEADS_BACKEND === "br" ? ["br", "bd"] : ["bd", "br"];
  let last: unknown = new Error("no Beads binary available");
  for (const binary of binaries) {
    try {
      const stdout = await checked(binary, ["list", "--json", "--all", "--limit", "0"], {cwd: rigPath});
      const rows = arrayPayload<RawBead>(JSON.parse(stdout));
      const byId = new Map(rows.map((row) => [row.id, row]));
      const candidates = rows.filter((row) => (row.issue_type ?? row.type) === "epic" || (row.dependent_count ?? 0) > 0);
      await Promise.all(candidates.map(async (parent) => {
        try {
          const edgeOutput = await checked(binary, ["dep", "list", parent.id, "--direction=up", "--json"], {cwd: rigPath});
          const edges = arrayPayload<{issue_id?: string; id?: string; type?: string}>(JSON.parse(edgeOutput));
          for (const edge of edges) if (edge.type === "parent-child") {
            const child = byId.get(edge.issue_id ?? edge.id ?? ""); if (child) child.parent_id = parent.id;
          }
        } catch (error) { parent.relationship_error = redact(error instanceof Error ? error.message : String(error)); }
      }));
      return healthy("beads", rows, {operation: `${binary} list/dep list`, target: rigPath, logPath: join(rigPath, ".planning", "logs")});
    } catch (error) { last = error; }
  }
  return unhealthy("beads", [], last, previous, {operation: "bd/br list --json", target: rigPath, stderr: redact(String(last))});
}

interface RawFlow {id: string; state_type?: string; state_name?: string; start_time?: string; end_time?: string; tags?: string[]; parameters?: Record<string, unknown>}
interface RawTask {id: string; name?: string; state_type?: string; state_name?: string; start_time?: string; end_time?: string; run_count?: number; flow_run_id?: string}
const tagValue = (tags: string[] | undefined, prefix: string) => tags?.find((tag) => tag.startsWith(prefix))?.slice(prefix.length);

export async function fetchPrefect(apiUrl: string, signal?: AbortSignal, previous?: SourceSnapshot<Attempt[]>): Promise<SourceSnapshot<Attempt[]>> {
  try {
    const base = apiUrl.replace(/\/$/, "");
    const flows: RawFlow[] = [];
    for (let offset = 0; offset < 1000; offset += 200) {
      const response = await fetch(`${base}/flow_runs/filter`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({limit: 200, offset, sort: "START_TIME_DESC"}), signal});
      if (!response.ok) throw new Error(`Prefect flow runs: HTTP ${response.status}`);
      const page = await response.json() as RawFlow[]; flows.push(...page); if (page.length < 200) break;
    }
    const attempts: Attempt[] = [];
    for (const [flowIndex, flow] of flows.entries()) {
      const issueId = tagValue(flow.tags, "issue_id:");
      const epicId = tagValue(flow.tags, "epic_id:");
      if (!issueId && !epicId) continue;
      let roles: RoleExecution[] = [];
      try {
        if (flowIndex >= 50) throw new Error("task details deferred until attempt is visible");
        const taskResponse = await fetch(`${base}/task_runs/filter`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({limit: 200, task_runs: {flow_run_id: {any_: [flow.id]}}}), signal});
        if (taskResponse.ok) roles = (await taskResponse.json() as RawTask[]).map((task) => ({id: task.id, role: task.name ?? "task", state: task.state_name ?? task.state_type ?? "unknown", iteration: task.run_count ?? 1, startedAt: task.start_time, endedAt: task.end_time}));
      } catch { /* a task page failure degrades this attempt, not the hierarchy */ }
      const runtime: Record<string, string> = {};
      for (const key of ["backend", "provider", "account", "account_class", "model", "effort", "rig", "rig_path"] as const) {
        const value = flow.parameters?.[key]; if (typeof value === "string") runtime[key] = value;
      }
      attempts.push({id: flow.id, issueId, epicId, formula: tagValue(flow.tags, "formula:"), state: flow.state_name ?? flow.state_type ?? "unknown", startedAt: flow.start_time, endedAt: flow.end_time, runtime, roles});
    }
    return healthy("prefect", attempts, {operation: "POST flow_runs/filter + task_runs/filter", target: apiUrl});
  } catch (error) { return unhealthy("prefect", [], error, previous, {operation: "Prefect REST refresh", target: apiUrl, stderr: redact(String(error))}); }
}

export async function fetchTmux(target?: string, previous?: SourceSnapshot<{target?: string; output: string; available: boolean}>): Promise<SourceSnapshot<{target?: string; output: string; available: boolean}>> {
  if (!target) return healthy("tmux", {output: "", available: false});
  try {
    const output = await checked("tmux", ["capture-pane", "-t", target, "-p", "-S", "-200"], {timeoutMs: 3_000});
    return healthy("tmux", {target, output, available: true}, {operation: "tmux capture-pane", target});
  } catch (error) { return unhealthy("tmux", {target, output: "", available: false}, error, previous, {operation: "tmux capture-pane", target, stderr: redact(String(error))}); }
}

async function walkArtifacts(root: string, depth = 0): Promise<Artifact[]> {
  if (depth > 3) return [];
  let entries; try { entries = await readdir(root, {withFileTypes: true}); } catch { return []; }
  const found: Artifact[] = [];
  for (const entry of entries.slice(0, 500)) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) found.push(...await walkArtifacts(path, depth + 1));
    else if (/\.(json|md|txt|log|diff|svg|html)$/i.test(entry.name)) {
      const info = await stat(path); found.push({name: entry.name, kind: entry.name.split(".").pop() ?? "file", path, createdAt: info.mtime.toISOString()});
    }
  }
  return found;
}

export async function fetchArtifacts(rigPath: string, previous?: SourceSnapshot<Artifact[]>): Promise<SourceSnapshot<Artifact[]>> {
  try { return healthy("artifacts", await walkArtifacts(join(rigPath, ".planning")), {operation: "bounded artifact scan", target: join(rigPath, ".planning")}); }
  catch (error) { return unhealthy("artifacts", [], error, previous, {operation: "bounded artifact scan", target: join(rigPath, ".planning"), stderr: redact(String(error))}); }
}

export async function readArtifact(path: string): Promise<string> { return redact((await readFile(path, "utf8")).slice(-100_000)); }

export async function sourceDiagnostics(rigPath: string): Promise<Record<string, string>> {
  const [bd, tmux] = await Promise.all([run("bd", ["where"], {cwd: rigPath}), run("tmux", ["list-sessions"], {timeoutMs: 3_000})]);
  return {beads: redact(bd.stderr || bd.stdout), tmux: redact(tmux.stderr || tmux.stdout)};
}
