/**
 * Minimal Prefect REST client. We only model the fields we render.
 *
 * Endpoints used:
 *   POST /flow_runs/filter      — list flow runs (filter by tags issue_id:* / epic_id:*)
 *   POST /task_runs/filter      — list task runs for a given flow run
 *   GET  /flow_runs/{id}/graph-v2 — DAG of nodes (used later for richer timelines)
 *
 * We use Bun's native `fetch`. All calls are read-only.
 */

export type PrefectStateType =
  | "SCHEDULED"
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "CRASHED"
  | "PAUSED"
  | "CANCELLING"
  | string;

export interface PrefectState {
  type: PrefectStateType;
  name: string;
}

export interface PrefectFlowRun {
  id: string;
  name: string;
  flow_id: string;
  state_type: PrefectStateType;
  state_name: string;
  state?: PrefectState;
  tags: string[];
  start_time: string | null;
  end_time: string | null;
  total_run_time?: number;
  parameters?: Record<string, unknown>;
  /** Set on subflow runs — points at the calling task run in the parent flow. */
  parent_task_run_id?: string | null;
}

export interface PrefectTaskRun {
  id: string;
  name: string;
  flow_run_id: string | null;
  state_type: PrefectStateType;
  state_name: string;
  tags: string[];
  start_time: string | null;
  end_time: string | null;
  run_count?: number;
}

const DEFAULT_BASE = "http://127.0.0.1:4200/api";

function baseUrl(override?: string): string {
  return (override ?? process.env.PREFECT_API_URL ?? DEFAULT_BASE).replace(/\/$/, "");
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`POST ${url} → ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`GET ${url} → ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

/** Fetch flow runs, optionally filtered by a tag prefix (e.g. "epic_id:4ja"). */
export async function fetchFlowRuns(opts: {
  apiUrl?: string;
  tags?: string[]; // e.g. ["issue_id:4ja.1"] — Prefect "all_" semantics
  limit?: number;
}): Promise<PrefectFlowRun[]> {
  const body: Record<string, unknown> = {
    limit: opts.limit ?? 200,
    sort: "START_TIME_DESC",
  };
  if (opts.tags && opts.tags.length > 0) {
    body.flow_runs = { tags: { all_: opts.tags } };
  }
  return postJson<PrefectFlowRun[]>(`${baseUrl(opts.apiUrl)}/flow_runs/filter`, body);
}

/** For each given flow_id, find the most recent COMPLETED flow run.
 *  Returns a map flow_id → flow_run id (missing keys = no completed run yet). */
export async function fetchLatestCompletedRunPerFlow(
  flowIds: string[],
  apiUrl?: string,
): Promise<Record<string, string>> {
  if (flowIds.length === 0) return {};
  const body = {
    limit: flowIds.length * 4, // overshoot to ensure each flow gets covered
    sort: "START_TIME_DESC",
    flow_runs: {
      flow_id: { any_: flowIds },
      state: { type: { any_: ["COMPLETED"] } },
    },
  };
  const runs = await postJson<PrefectFlowRun[]>(
    `${baseUrl(apiUrl)}/flow_runs/filter`,
    body,
  );
  const out: Record<string, string> = {};
  for (const r of runs) {
    if (!out[r.flow_id]) out[r.flow_id] = r.id;
  }
  return out;
}

export async function fetchTaskRuns(
  flowRunId: string,
  apiUrl?: string,
): Promise<PrefectTaskRun[]> {
  // Valid task_run sorts in Prefect 3: ID_DESC, EXPECTED_START_TIME_{ASC,DESC},
  // NAME_{ASC,DESC}, NEXT_SCHEDULED_START_TIME_ASC, END_TIME_DESC. (Note: NOT
  // START_TIME_ASC — that's flow_runs only.)
  const body = {
    limit: 200,
    sort: "EXPECTED_START_TIME_ASC",
    task_runs: { flow_run_id: { any_: [flowRunId] } },
  };
  return postJson<PrefectTaskRun[]>(`${baseUrl(apiUrl)}/task_runs/filter`, body);
}

/** Resolve task-run IDs (e.g. parent_task_run_ids) to their full records so
 *  we can read each one's flow_run_id and link a subflow to its parent. */
export async function fetchTaskRunsByIds(
  ids: string[],
  apiUrl?: string,
): Promise<PrefectTaskRun[]> {
  if (ids.length === 0) return [];
  const body = {
    limit: ids.length,
    task_runs: { id: { any_: ids } },
  };
  return postJson<PrefectTaskRun[]>(`${baseUrl(apiUrl)}/task_runs/filter`, body);
}

export async function fetchFlowRunGraph(
  flowRunId: string,
  apiUrl?: string,
): Promise<unknown> {
  return getJson(`${baseUrl(apiUrl)}/flow_runs/${flowRunId}/graph-v2`);
}

/** Pull the issue_id / epic_id values out of Prefect tags. */
export function tagValue(tags: string[], prefix: string): string | undefined {
  const hit = tags.find((t) => t.startsWith(`${prefix}:`));
  return hit ? hit.slice(prefix.length + 1) : undefined;
}
