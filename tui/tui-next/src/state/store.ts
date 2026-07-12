import type {Epic, Issue, OperationsModel, SourceName, SourceSnapshot} from "../domain/model.js";
import {lifecycleGroup} from "../domain/model.js";

export type Scope = "all" | "active" | "blocked" | "failed" | "completed" | "archived";
export interface ActivityRecord {at: string; objectId?: string; operation: string; result: string; verification: "verified" | "pending" | "failed"}
export interface UIState {
  model: OperationsModel; selectedId?: string; expanded: Set<string>; scroll: number; detailScroll: number;
  liveOutput: string; liveTarget?: string; liveError?: string; liveScroll: number; followOutput: boolean;
  detailTab: "overview" | "activity" | "artifacts" | "description"; narrowDetail: boolean; scope: Scope;
  overlay?: "palette" | "help" | "diagnostics"; query: string; commandIndex: number; pendingActionId?: string;
  activity: ActivityRecord[]; refreshing: Set<SourceName>;
}
export type UIAction =
  | {type: "model"; model: OperationsModel} | {type: "select"; id?: string} | {type: "move"; delta: number; viewport?: number}
  | {type: "toggle"; id: string} | {type: "overlay"; overlay?: UIState["overlay"]} | {type: "query"; value: string}
  | {type: "commandMove"; delta: number} | {type: "scope"; scope: Scope} | {type: "narrowDetail"; value: boolean}
  | {type: "activity"; record: ActivityRecord} | {type: "pending"; id?: string} | {type: "tab"; tab: UIState["detailTab"]}
  | {type: "detailScroll"; delta: number} | {type: "liveScroll"; delta: number} | {type: "follow"; value: boolean}
  | {type: "liveOutput"; output: string; target?: string; error?: string};

const emptySnapshot = (source: SourceName): SourceSnapshot<unknown> => ({source, data: [], fetchedAt: new Date(0).toISOString(), freshness: "unavailable"});
export const emptyModel = (): OperationsModel => ({epics: [], standalone: [], unattributedAttempts: [], unresolved: [], snapshots: {beads: emptySnapshot("beads"), prefect: emptySnapshot("prefect"), tmux: emptySnapshot("tmux"), artifacts: emptySnapshot("artifacts")}});
export const initialState = (): UIState => ({model: emptyModel(), expanded: new Set(), scroll: 0, detailScroll: 0, liveOutput: "", liveScroll: 0, followOutput: true, detailTab: "overview", narrowDetail: false, scope: "all", query: "", commandIndex: 0, activity: [], refreshing: new Set()});

export function visibleObjects(state: UIState): Array<Epic | Issue> {
  const list: Array<Epic | Issue> = [];
  const groups: Scope[] = ["active", "blocked", "failed", "completed", "archived"];
  for (const group of groups) {
    if (state.scope !== "all" && state.scope !== group) continue;
    const epics = state.model.epics.filter((epic) => state.scope === "all" ? lifecycleGroup(epic.state) === group : lifecycleGroup(epic.state) === group || epic.children.some((issue) => lifecycleGroup(issue.state) === group));
    for (const epic of epics) {
      list.push(epic);
      if (state.expanded.has(epic.id)) list.push(...epic.children.filter((issue) => state.scope === "all" || lifecycleGroup(issue.state) === state.scope));
    }
    list.push(...state.model.standalone.filter((issue) => lifecycleGroup(issue.state) === group));
  }
  return list;
}
export const selectedObject = (state: UIState) => visibleObjects(state).find((object) => object.id === state.selectedId) ?? visibleObjects(state)[0];

export function reducer(state: UIState, action: UIAction): UIState {
  if (action.type === "model") {
    const ids = new Set([...action.model.epics, ...action.model.epics.flatMap((epic) => epic.children), ...action.model.standalone].map((item) => item.id));
    const next = {...state, model: action.model};
    const selectedId = state.selectedId && ids.has(state.selectedId) ? state.selectedId : visibleObjects(next)[0]?.id;
    return {...next, selectedId};
  }
  if (action.type === "select") return {...state, selectedId: action.id};
  if (action.type === "move") {
    const rows = visibleObjects(state); const current = Math.max(0, rows.findIndex((item) => item.id === state.selectedId));
    const index = Math.max(0, Math.min(rows.length - 1, current + action.delta)); const viewport = Math.max(1, action.viewport ?? 10);
    const scroll = index < state.scroll ? index : index >= state.scroll + viewport ? index - viewport + 1 : state.scroll;
    return {...state, selectedId: rows[index]?.id, scroll, detailScroll: 0, liveScroll: 0, followOutput: true};
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
  if (action.type === "detailScroll") return {...state, detailScroll: Math.max(0, state.detailScroll + action.delta)};
  if (action.type === "liveScroll") return {...state, liveScroll: Math.max(0, state.liveScroll + action.delta), followOutput: false};
  if (action.type === "follow") return {...state, followOutput: action.value, liveScroll: action.value ? 0 : state.liveScroll};
  if (action.type === "liveOutput") return {...state, liveOutput: action.output, liveTarget: action.target, liveError: action.error};
  return state;
}
