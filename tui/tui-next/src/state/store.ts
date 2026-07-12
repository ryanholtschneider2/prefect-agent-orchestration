import type {Epic, Issue, OperationsModel, SourceName, SourceSnapshot} from "../domain/model.js";
import {lifecycleGroup} from "../domain/model.js";

export type Scope = "all" | "active" | "blocked" | "failed" | "completed" | "archived";
export interface ActivityRecord {at: string; objectId?: string; operation: string; result: string; verification: "verified" | "pending" | "failed"}
export interface UIState {
  model: OperationsModel; selectedId?: string; expanded: Set<string>; scroll: number; detailScroll: number;
  detailTab: "overview" | "activity" | "artifacts" | "description"; narrowDetail: boolean; scope: Scope;
  overlay?: "palette" | "help" | "diagnostics"; query: string; commandIndex: number; pendingActionId?: string;
  activity: ActivityRecord[]; refreshing: Set<SourceName>;
}
export type UIAction =
  | {type: "model"; model: OperationsModel} | {type: "select"; id?: string} | {type: "move"; delta: number}
  | {type: "toggle"; id: string} | {type: "overlay"; overlay?: UIState["overlay"]} | {type: "query"; value: string}
  | {type: "commandMove"; delta: number} | {type: "scope"; scope: Scope} | {type: "narrowDetail"; value: boolean}
  | {type: "activity"; record: ActivityRecord} | {type: "pending"; id?: string} | {type: "tab"; tab: UIState["detailTab"]};

const emptySnapshot = (source: SourceName): SourceSnapshot<unknown> => ({source, data: [], fetchedAt: new Date(0).toISOString(), freshness: "unavailable"});
export const emptyModel = (): OperationsModel => ({epics: [], standalone: [], unattributedAttempts: [], snapshots: {beads: emptySnapshot("beads"), prefect: emptySnapshot("prefect"), tmux: emptySnapshot("tmux"), artifacts: emptySnapshot("artifacts")}});
export const initialState = (): UIState => ({model: emptyModel(), expanded: new Set(), scroll: 0, detailScroll: 0, detailTab: "overview", narrowDetail: false, scope: "all", query: "", commandIndex: 0, activity: [], refreshing: new Set()});

export function visibleObjects(state: UIState): Array<Epic | Issue> {
  const include = (epic: Epic) => state.scope === "all" || lifecycleGroup(epic.state) === state.scope || epic.children.some((issue) => lifecycleGroup(issue.state) === state.scope);
  const list: Array<Epic | Issue> = [];
  for (const epic of state.model.epics.filter(include)) {
    list.push(epic);
    if (state.expanded.has(epic.id)) list.push(...epic.children.filter((issue) => state.scope === "all" || lifecycleGroup(issue.state) === state.scope));
  }
  list.push(...state.model.standalone.filter((issue) => state.scope === "all" || lifecycleGroup(issue.state) === state.scope));
  return list;
}
export const selectedObject = (state: UIState) => visibleObjects(state).find((object) => object.id === state.selectedId) ?? visibleObjects(state)[0];

export function reducer(state: UIState, action: UIAction): UIState {
  if (action.type === "model") {
    const ids = new Set([...action.model.epics, ...action.model.epics.flatMap((epic) => epic.children), ...action.model.standalone].map((item) => item.id));
    const selectedId = state.selectedId && ids.has(state.selectedId) ? state.selectedId : action.model.epics[0]?.id ?? action.model.standalone[0]?.id;
    return {...state, model: action.model, selectedId};
  }
  if (action.type === "select") return {...state, selectedId: action.id};
  if (action.type === "move") {
    const rows = visibleObjects(state); const current = Math.max(0, rows.findIndex((item) => item.id === state.selectedId));
    return {...state, selectedId: rows[Math.max(0, Math.min(rows.length - 1, current + action.delta))]?.id};
  }
  if (action.type === "toggle") { const expanded = new Set(state.expanded); expanded.has(action.id) ? expanded.delete(action.id) : expanded.add(action.id); return {...state, expanded}; }
  if (action.type === "overlay") return {...state, overlay: action.overlay, query: action.overlay === "palette" ? "" : state.query, commandIndex: 0};
  if (action.type === "query") return {...state, query: action.value, commandIndex: 0};
  if (action.type === "commandMove") return {...state, commandIndex: Math.max(0, state.commandIndex + action.delta)};
  if (action.type === "scope") return {...state, scope: action.scope};
  if (action.type === "narrowDetail") return {...state, narrowDetail: action.value};
  if (action.type === "activity") return {...state, activity: [action.record, ...state.activity].slice(0, 200)};
  if (action.type === "pending") return {...state, pendingActionId: action.id};
  if (action.type === "tab") return {...state, detailTab: action.tab};
  return state;
}
