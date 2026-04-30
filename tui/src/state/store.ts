/**
 * Zustand store: single source of truth for what the UI renders.
 *
 * Shape is issue-centric: each `IssueRow` aggregates Prefect flow runs +
 * the role->state map we derive from task runs.
 */

import { create } from "zustand";

import { bdList, bdShow, type BdIssue } from "../data/beads.js";
import {
  fetchFlowRuns,
  fetchLatestCompletedRunPerFlow,
  fetchTaskRuns,
  fetchTaskRunsByIds,
  tagValue,
  type PrefectFlowRun,
  type PrefectTaskRun,
} from "../data/prefect.js";
import {
  baseTaskName,
  capturePane,
  resolveSession,
  sessionFor,
} from "../data/tmux.js";

export type RoleState =
  | "not_started"
  | "running"
  | "succeeded"
  | "failed"
  | "looping"
  | "paused"
  | "cancelled";

export interface RoleSlot {
  role: string;
  state: RoleState;
  iterations: number;
  taskRunId?: string;
  startedAt?: string | null;
}

export interface IssueRow {
  issueId: string;
  epicId?: string;
  title?: string;
  bdStatus?: string;
  flowRunId?: string;
  flowState?: string; // Prefect state_type
  flowStateName?: string;
  roles: RoleSlot[];
  activeRole?: string;
  updatedAt: number;
  /** Prefect-native parent flow run id (subflow → parent). Null/absent for roots. */
  parentFlowRunId?: string | null;
  /** Issue id of the parent flow run, if known (computed during tick). */
  parentIssueId?: string | null;
  /** Issue ids of subflow children of this row, computed during tick. */
  childIssueIds: string[];
  startTime?: string | null;
}

export interface PoTuiState {
  issues: IssueRow[];
  selectedId: string | null;
  filter: string;
  refreshMs: number;
  prefectUrl?: string;
  /** When set, hide everything not under this parent issue id. */
  drillIntoIssueId: string | null;
  /** Legacy `--epic <id>` CLI arg — still maps to a tag filter on fetch. */
  epicFilter?: string;
  hideCompleted: boolean;
  lastError: string | null;
  loading: boolean;
  paneText: string;
  paneSession: string | null;
  /** flow_id → ordered base task names from the most recent COMPLETED run.
   *  Used as the "future stages" schema for in-progress runs of the same
   *  flow. Populated lazily on each tick. */
  schemaByFlowId: Record<string, string[]>;
  /** issueId → cached `bd show <id> --json` result. Populated by
   *  `refreshBdShow()` only when the bd-show pane is visible. Last-good
   *  wins on transient fetch errors (see `bdShowError`). */
  bdShowCache: Record<string, BdIssue>;
  /** Last `bd show` error string (cleared on next success). When both
   *  `bdShowError` and a cached entry are set, the UI overlays a
   *  yellow "(stale: …)" banner instead of wiping the pane. */
  bdShowError: string | null;
  /** Whether the bd-show pane is rendered in the right panel's bottom
   *  slot. False (default) means the slot shows TmuxTail and the store
   *  short-circuits any `bd show` shellouts. */
  bdShowVisible: boolean;
  /** Issue ids with an in-flight `bd show` shellout (de-dup guard). */
  bdShowLoading: Set<string>;
  // actions
  setSelected: (id: string | null) => void;
  setFilter: (f: string) => void;
  setDrill: (id: string | null) => void;
  toggleHideCompleted: () => void;
  tick: () => Promise<void>;
  refreshPane: () => Promise<void>;
  refreshBdShow: () => Promise<void>;
  setBdShowVisible: (v: boolean) => void;
}

function deriveRoles(
  taskRuns: PrefectTaskRun[],
  schema?: readonly string[],
): {
  roles: RoleSlot[];
  active?: string;
} {
  // Bucket THIS run's task_runs by underlying base task name.
  const byRole = new Map<string, PrefectTaskRun[]>();
  for (const tr of taskRuns) {
    const role = baseTaskName(tr.name);
    if (!role) continue;
    const arr = byRole.get(role) ?? [];
    arr.push(tr);
    byRole.set(role, arr);
  }

  const roles: RoleSlot[] = [];
  let active: string | undefined;
  const seen = new Set<string>();

  // 1. Schema columns first (if a recent completed run gave us one). Tasks
  //    not yet started in *this* run render as `not_started` (greyed). This
  //    is how we get a "future stages preview" without hardcoding anything.
  if (schema) {
    for (const role of schema) {
      seen.add(role);
      const runs = byRole.get(role) ?? [];
      if (runs.length === 0) {
        roles.push({ role, state: "not_started", iterations: 0 });
        continue;
      }
      const latest = runs[runs.length - 1]!;
      const state = mapTaskState(latest.state_type, runs.length);
      if (state === "running") active = role;
      roles.push({
        role,
        state,
        iterations: runs.length,
        taskRunId: latest.id,
        startedAt: latest.start_time,
      });
    }
  }

  // 2. Append everything in this run that the schema didn't cover, sorted
  //    by earliest start time (so brand-new tasks land at the right edge).
  const extras = Array.from(byRole.entries())
    .filter(([role]) => !seen.has(role))
    .sort(([, a], [, b]) => {
      const aStart = a[0]?.start_time ?? "";
      const bStart = b[0]?.start_time ?? "";
      return aStart.localeCompare(bStart);
    });
  for (const [role, runs] of extras) {
    const latest = runs[runs.length - 1]!;
    const state = mapTaskState(latest.state_type, runs.length);
    if (state === "running") active = role;
    roles.push({
      role,
      state,
      iterations: runs.length,
      taskRunId: latest.id,
      startedAt: latest.start_time,
    });
  }
  return { roles, active };
}

/** Build an ordered task-name schema from a flow run's task runs. */
function schemaFromTaskRuns(trs: PrefectTaskRun[]): string[] {
  const seen = new Set<string>();
  const ordered: { name: string; start: string }[] = [];
  for (const tr of trs) {
    const name = baseTaskName(tr.name);
    if (!name || seen.has(name)) continue;
    seen.add(name);
    ordered.push({ name, start: tr.start_time ?? "" });
  }
  ordered.sort((a, b) => a.start.localeCompare(b.start));
  return ordered.map((x) => x.name);
}

function mapTaskState(t: string, count: number): RoleState {
  switch (t) {
    case "RUNNING":
      return "running";
    case "COMPLETED":
      return count > 1 ? "looping" : "succeeded";
    case "FAILED":
    case "CRASHED":
      return "failed";
    case "PAUSED":
      return "paused";
    case "CANCELLED":
    case "CANCELLING":
      return "cancelled";
    default:
      return "not_started";
  }
}

function flowToIssueId(fr: PrefectFlowRun): string | undefined {
  return tagValue(fr.tags, "issue_id");
}

export const useStore = create<PoTuiState>((set, get) => ({
  issues: [],
  selectedId: null,
  filter: "",
  refreshMs: 2000,
  prefectUrl: undefined,
  drillIntoIssueId: null,
  epicFilter: undefined,
  hideCompleted: false,
  lastError: null,
  loading: false,
  paneText: "",
  paneSession: null,
  schemaByFlowId: {},
  bdShowCache: {},
  bdShowError: null,
  bdShowVisible: false,
  bdShowLoading: new Set<string>(),

  setSelected: (id) => {
    set({ selectedId: id });
    void get().refreshPane();
    void get().refreshBdShow();
  },

  setFilter: (f) => set({ filter: f }),

  setDrill: (id) => set({ drillIntoIssueId: id }),

  toggleHideCompleted: () => set((s) => ({ hideCompleted: !s.hideCompleted })),

  tick: async () => {
    const { prefectUrl, epicFilter, selectedId } = get();
    set({ loading: true });
    try {
      const tags = epicFilter ? [`epic_id:${epicFilter}`] : [];
      const flowRuns = await fetchFlowRuns({
        apiUrl: prefectUrl,
        tags,
        limit: 200,
      });

      // bd issues — best-effort; failure is non-fatal.
      let bdMap = new Map<string, { title?: string; status?: string }>();
      try {
        const issues = await bdList({});
        bdMap = new Map(
          issues.map((i) => [i.id, { title: i.title, status: i.status }]),
        );
      } catch {
        /* bd not on PATH or no rig — ignore */
      }

      // Dedupe to one flow run per issue_id (most recent start).
      const byIssue = new Map<string, PrefectFlowRun>();
      for (const fr of flowRuns) {
        const iid = flowToIssueId(fr);
        if (!iid) continue;
        const existing = byIssue.get(iid);
        if (!existing) {
          byIssue.set(iid, fr);
          continue;
        }
        const a = existing.start_time ?? "";
        const b = fr.start_time ?? "";
        if (b > a) byIssue.set(iid, fr);
      }

      const flowEntries = Array.from(byIssue.entries());

      // Resolve subflow → parent linkage via parent_task_run_id. Prefect tags
      // are unreliable for hierarchy across formulas; flow-run parentage isn't.
      const parentTaskIds = flowEntries
        .map(([, fr]) => fr.parent_task_run_id)
        .filter((x): x is string => !!x);
      const parentTaskRuns = await fetchTaskRunsByIds(parentTaskIds, prefectUrl).catch(
        () => [] as PrefectTaskRun[],
      );
      const taskIdToFlowId = new Map<string, string>();
      for (const tr of parentTaskRuns) {
        if (tr.flow_run_id) taskIdToFlowId.set(tr.id, tr.flow_run_id);
      }
      const flowIdToIssueId = new Map<string, string>();
      for (const [issueId, fr] of flowEntries) flowIdToIssueId.set(fr.id, issueId);

      // Pull task runs for each flow in parallel. We surface the FIRST error
      // to lastError so an API-shape regression doesn't silently empty every
      // role timeline (this very thing happened with a stale sort enum).
      let firstFetchErr: string | null = null;
      const taskRunsPerFlow = await Promise.all(
        flowEntries.map(([, fr]) =>
          fetchTaskRuns(fr.id, prefectUrl).catch((e: unknown) => {
            if (!firstFetchErr) {
              firstFetchErr =
                e instanceof Error ? e.message : String(e);
            }
            return [] as PrefectTaskRun[];
          }),
        ),
      );
      if (firstFetchErr) {
        set({ lastError: `task_runs fetch: ${firstFetchErr}` });
      }

      // Lazy-fetch a "future stages" schema for any flow_id we haven't seen
      // yet. We use the most recent COMPLETED run of the same flow as the
      // canonical task list, then cache forever in this session.
      const knownSchemas = get().schemaByFlowId;
      const schemaMissing = Array.from(
        new Set(flowEntries.map(([, fr]) => fr.flow_id)),
      ).filter((fid) => !(fid in knownSchemas));
      if (schemaMissing.length > 0) {
        try {
          const latestRunIds = await fetchLatestCompletedRunPerFlow(
            schemaMissing,
            prefectUrl,
          );
          const fetched = await Promise.all(
            Object.entries(latestRunIds).map(async ([fid, runId]) => {
              const trs = await fetchTaskRuns(runId, prefectUrl).catch(
                () => [] as PrefectTaskRun[],
              );
              return [fid, schemaFromTaskRuns(trs)] as const;
            }),
          );
          if (fetched.length > 0) {
            const next = { ...knownSchemas };
            for (const [fid, schema] of fetched) next[fid] = schema;
            set({ schemaByFlowId: next });
          }
        } catch {
          /* schema fetch is a polish — don't block the main render */
        }
      }
      const schemas = get().schemaByFlowId;

      const issues: IssueRow[] = flowEntries.map(([issueId, fr], idx) => {
        const trs = taskRunsPerFlow[idx] ?? [];
        const { roles, active } = deriveRoles(trs, schemas[fr.flow_id]);
        const meta = bdMap.get(issueId);
        const parentFlowId = fr.parent_task_run_id
          ? taskIdToFlowId.get(fr.parent_task_run_id) ?? null
          : null;
        const parentIssueId = parentFlowId
          ? flowIdToIssueId.get(parentFlowId) ?? null
          : null;
        return {
          issueId,
          epicId: tagValue(fr.tags, "epic_id"),
          title: meta?.title,
          bdStatus: meta?.status,
          flowRunId: fr.id,
          flowState: fr.state_type,
          flowStateName: fr.state_name,
          roles,
          activeRole: active,
          updatedAt: Date.now(),
          parentFlowRunId: parentFlowId,
          parentIssueId,
          childIssueIds: [],
          startTime: fr.start_time,
        };
      });

      // Populate childIssueIds on each parent.
      const byId = new Map(issues.map((i) => [i.issueId, i]));
      for (const iss of issues) {
        if (iss.parentIssueId) {
          const parent = byId.get(iss.parentIssueId);
          if (parent) parent.childIssueIds.push(iss.issueId);
        }
      }

      const nextSelected =
        selectedId && issues.some((i) => i.issueId === selectedId)
          ? selectedId
          : (pickInitialSelection(issues) ?? null);

      set({ issues, selectedId: nextSelected, lastError: null, loading: false });
      void get().refreshPane();
      void get().refreshBdShow();
    } catch (err) {
      set({
        lastError: err instanceof Error ? err.message : String(err),
        loading: false,
      });
    }
  },

  refreshPane: async () => {
    const { issues, selectedId } = get();
    if (!selectedId) {
      set({ paneText: "", paneSession: null });
      return;
    }
    const row = issues.find((i) => i.issueId === selectedId);
    if (!row) return;
    const byId = new Map(issues.map((i) => [i.issueId, i]));

    // Decide what to tail: the selected row's own running role wins; else
    // the deepest running descendant; else the row's last-touched role
    // (post-mortem); else nothing.
    let target: { issueId: string; role: string } | null = null;
    if (row.activeRole) {
      target = { issueId: row.issueId, role: row.activeRole };
    } else {
      const deep = findRunningDescendant(row, byId);
      if (deep) target = { issueId: deep.issue.issueId, role: deep.role };
    }
    if (!target) {
      const last = findLastTouchedRole(row);
      if (last) target = { issueId: row.issueId, role: last };
    }
    if (!target) {
      set({
        paneText: row.childIssueIds.length
          ? "(parent flow — waiting for first child task)"
          : "(no tasks running yet)",
        paneSession: null,
      });
      return;
    }
    const resolved = await resolveSession(target.issueId, target.role);
    const session = resolved ?? sessionFor(target.issueId, target.role);
    const text = resolved ? await capturePane(resolved, 200) : "";
    set({
      paneText:
        text ||
        `(no live tmux session for ${target.issueId}/${target.role} — looked for ${session}${resolved ? "" : " or fork)"}`,
      paneSession: resolved,
    });
  },

  refreshBdShow: async () => {
    const { selectedId, bdShowVisible, bdShowLoading } = get();
    // Visibility-gated: zero shellouts when the pane is hidden.
    if (!bdShowVisible || !selectedId) return;
    // De-dup: don't kick a second fetch while one is in flight for the same id.
    if (bdShowLoading.has(selectedId)) return;
    set({ bdShowLoading: new Set(bdShowLoading).add(selectedId) });
    try {
      const issue = await bdShow(selectedId);
      if (issue) {
        set({
          bdShowCache: { ...get().bdShowCache, [selectedId]: issue },
          bdShowError: null,
        });
      } else {
        // bd returned []; treat as a soft error and preserve cache.
        set({ bdShowError: `bd show ${selectedId}: empty response` });
      }
    } catch (err) {
      // Cache-preservation contract: leave bdShowCache untouched on error.
      set({ bdShowError: err instanceof Error ? err.message : String(err) });
    } finally {
      // Build the next loading set fresh from current state — another tick
      // may have moved on; we only want to remove ourselves.
      const after = new Set(get().bdShowLoading);
      after.delete(selectedId);
      set({ bdShowLoading: after });
    }
  },

  setBdShowVisible: (v) => {
    set({ bdShowVisible: v });
    if (v) void get().refreshBdShow();
  },
}));

const STATE_PRIORITY: Record<string, number> = {
  RUNNING: 0,
  PAUSED: 1,
  FAILED: 2,
  CRASHED: 2,
  CANCELLING: 3,
  CANCELLED: 4,
  PENDING: 5,
  SCHEDULED: 5,
  COMPLETED: 6,
};

/** Sort: running first, then failed, then completed; recent within each. */
export function activitySort(a: IssueRow, b: IssueRow): number {
  const pa = STATE_PRIORITY[a.flowState ?? ""] ?? 9;
  const pb = STATE_PRIORITY[b.flowState ?? ""] ?? 9;
  if (pa !== pb) return pa - pb;
  const sa = a.startTime ?? "";
  const sb = b.startTime ?? "";
  if (sa !== sb) return sb.localeCompare(sa);
  return a.issueId.localeCompare(b.issueId);
}

function pickInitialSelection(issues: IssueRow[]): string | undefined {
  const sorted = [...issues].sort(activitySort);
  return sorted[0]?.issueId;
}

function findLastTouchedRole(row: IssueRow): string | undefined {
  for (let i = row.roles.length - 1; i >= 0; i--) {
    const r = row.roles[i]!;
    if (r.state !== "not_started") return r.role;
  }
  return undefined; // truly nothing started — let caller pivot to children
}

/** DFS for the deepest descendant with a currently-running role. Used both
 *  for the live tmux pane (so the user always sees real activity) and for
 *  the "active subtask" header line. */
export function findRunningDescendant(
  row: IssueRow,
  byId: Map<string, IssueRow>,
): { issue: IssueRow; role: string } | null {
  for (const childId of row.childIssueIds) {
    const child = byId.get(childId);
    if (!child) continue;
    if (child.activeRole) return { issue: child, role: child.activeRole };
    const deeper = findRunningDescendant(child, byId);
    if (deeper) return deeper;
  }
  return null;
}
