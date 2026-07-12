export type WorkState = "open" | "in_progress" | "blocked" | "failed" | "closed" | "archived";
export type SourceName = "beads" | "prefect" | "tmux" | "artifacts";

export interface Dependency { id: string; type: string }
export interface Artifact { name: string; kind: string; path: string; producer?: string; createdAt?: string }
export interface AgentSession {id: string; role: string; target: string; available: boolean; outputHash?: string}
export interface TmuxSession {target: string; available: boolean; output?: string}
export interface RoleExecution { id: string; role: string; state: string; iteration: number; startedAt?: string; endedAt?: string }
export interface Attempt {
  id: string; issueId?: string; epicId?: string; formula?: string; state: string;
  startedAt?: string; endedAt?: string; runtime: Record<string, string>; roles: RoleExecution[];
}
export interface Issue {
  kind: "issue"; id: string; epicId?: string; title: string; state: WorkState;
  description?: string; dependencies: Dependency[]; attempts: Attempt[]; artifacts: Artifact[];
  sessions: AgentSession[]; comments: Array<{author?: string; text: string; createdAt?: string}>; updatedAt?: string; assignee?: string;
}
export interface Epic {
  kind: "epic"; id: string; title: string; state: WorkState; children: Issue[];
  dependencies: Dependency[]; updatedAt?: string;
}
export interface SourceSnapshot<T> {
  source: SourceName; data: T; fetchedAt: string; lastSuccessAt?: string;
  freshness: "fresh" | "stale" | "unavailable"; error?: string; contentHash?: string;
  retry?: {attempt: number; nextAt?: string; inFlight: boolean};
  diagnostic?: {operation: string; target?: string; exitStatus?: number; stderr?: string; logPath?: string};
}
export interface OperationsModel {
  epics: Epic[]; standalone: Issue[]; unattributedAttempts: Attempt[];
  unresolved: Array<{source: SourceName; id: string; reason: string}>;
  snapshots: Record<SourceName, SourceSnapshot<unknown>>;
}

export interface RawBead {
  id: string; title?: string; status?: string; issue_type?: string; type?: string;
  parent_id?: string; parent?: string; updated_at?: string; description?: string;
  assignee?: string; dependent_count?: number; relationship_error?: string; comments?: Array<{author?: string; text?: string; created_at?: string}>; dependencies?: Array<string | {id?: string; depends_on_id?: string; type?: string; dependency_type?: string}>;
}

const states: Record<string, WorkState> = {
  open: "open", in_progress: "in_progress", running: "in_progress", blocked: "blocked",
  failed: "failed", closed: "closed", completed: "closed", archived: "archived",
};
export const normalizeState = (value?: string): WorkState => states[(value ?? "open").toLowerCase()] ?? "open";

export function dependencyList(raw?: RawBead["dependencies"]): Dependency[] {
  return (raw ?? []).flatMap((entry) => {
    if (typeof entry === "string") return [{id: entry, type: "blocks"}];
    const id = entry.id ?? entry.depends_on_id;
    return id ? [{id, type: entry.type ?? entry.dependency_type ?? "blocks"}] : [];
  });
}

export function normalizeBeads(raw: RawBead[]): {epics: Epic[]; standalone: Issue[]} {
  const declaredParents = new Set(raw.map((row) => row.parent_id ?? row.parent).filter((id): id is string => Boolean(id)));
  const epicRows = raw.filter((row) => (row.issue_type ?? row.type) === "epic" || declaredParents.has(row.id));
  const epicIds = new Set(epicRows.map((row) => row.id));
  const issues = raw.filter((row) => !epicIds.has(row.id)).map<Issue>((row) => ({
    kind: "issue", id: row.id, epicId: row.parent_id ?? row.parent, title: row.title ?? row.id,
    state: normalizeState(row.status), description: row.description, dependencies: dependencyList(row.dependencies),
    attempts: [], artifacts: [], sessions: [], comments: (row.comments ?? []).map((comment) => ({author: comment.author, text: comment.text ?? "", createdAt: comment.created_at})), updatedAt: row.updated_at, assignee: row.assignee,
  }));
  const epics = epicRows.map<Epic>((row) => ({
    kind: "epic", id: row.id, title: row.title ?? row.id, state: normalizeState(row.status),
    dependencies: dependencyList(row.dependencies), updatedAt: row.updated_at,
    children: issues.filter((issue) => issue.epicId === row.id),
  }));
  return {epics, standalone: issues.filter((issue) => !issue.epicId || !epicIds.has(issue.epicId))};
}

export function reconcile(beads: RawBead[], attempts: Attempt[], artifacts: Artifact[], tmuxSessions: TmuxSession[] = []): Pick<OperationsModel, "epics" | "standalone" | "unattributedAttempts" | "unresolved"> {
  const result = normalizeBeads(beads);
  const issues = [...result.epics.flatMap((epic) => epic.children), ...result.standalone];
  const byId = new Map(issues.map((issue) => [issue.id, issue]));
  const unattributedAttempts: Attempt[] = [];
  for (const attempt of attempts) {
    const issue = attempt.issueId ? byId.get(attempt.issueId) : undefined;
    if (issue) issue.attempts.push(attempt); else unattributedAttempts.push(attempt);
  }
  for (const artifact of artifacts) {
    const match = issues.find((issue) => artifact.path.includes(issue.id));
    if (match) match.artifacts.push(artifact);
  }
  for (const session of tmuxSessions) {
    const issue = issues.find((candidate) => session.target.startsWith(`po-${candidate.id}-`));
    if (!issue) continue;
    const role = session.target.slice(`po-${issue.id}-`.length) || "agent";
    issue.sessions.push({id: session.target, role, target: session.target, available: session.available});
  }
  for (const issue of issues) issue.attempts.sort((a, b) => (b.startedAt ?? "").localeCompare(a.startedAt ?? ""));
  const unresolved: OperationsModel["unresolved"] = beads.flatMap((row) => row.relationship_error ? [{source: "beads" as const, id: row.id, reason: row.relationship_error}] : row.parent_id && !beads.some((candidate) => candidate.id === row.parent_id) ? [{source: "beads" as const, id: row.id, reason: `missing parent ${row.parent_id}`}] : []);
  unresolved.push(...unattributedAttempts.map((attempt) => ({source: "prefect" as const, id: attempt.id, reason: attempt.issueId ? `unknown issue ${attempt.issueId}` : "missing issue_id tag"})));
  return {...result, unattributedAttempts, unresolved};
}

export function epicRollup(epic: Epic): {complete: number; running: number; blocked: number; failed: number; total: number} {
  return epic.children.reduce((out, child) => {
    out.total += 1;
    if (child.state === "closed") out.complete += 1;
    if (child.state === "in_progress") out.running += 1;
    if (child.state === "blocked") out.blocked += 1;
    if (child.state === "failed") out.failed += 1;
    return out;
  }, {complete: 0, running: 0, blocked: 0, failed: 0, total: 0});
}

export const lifecycleGroup = (state: WorkState): "active" | "blocked" | "failed" | "completed" | "archived" =>
  state === "blocked" ? "blocked" : state === "failed" ? "failed" : state === "closed" ? "completed" : state === "archived" ? "archived" : "active";
