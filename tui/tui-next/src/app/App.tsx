import React, {useCallback, useEffect, useMemo, useReducer, useRef} from "react";
import {Box, Text, useApp, useInput, useStdout} from "ink";
import {executeAction, filterActions, type Selection} from "../actions/registry.js";
import {Detail} from "../components/Detail.js";
import {Diagnostics, Help, Palette} from "../components/Overlays.js";
import {WorkTree} from "../components/Tree.js";
import {reconcile, type Artifact, type Attempt, type OperationsModel, type RawBead, type SourceSnapshot} from "../domain/model.js";
import {fetchArtifacts, fetchBeads, fetchPrefect, fetchTmux} from "../sources/adapters.js";
import {initialState, reducer, selectedObject, type Scope} from "../state/store.js";
import {theme} from "../theme/theme.js";

export interface AppProps {rigPath: string; prefectUrl: string; refreshMs: number; ascii?: boolean; initialModel?: OperationsModel; onAttach?: (target: string) => void}
const scopes: Scope[] = ["all", "active", "blocked", "failed", "completed", "archived"];

export function App({rigPath, prefectUrl, refreshMs, ascii = false, initialModel, onAttach}: AppProps) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState); const {exit} = useApp(); const {stdout} = useStdout();
  const colors = useMemo(() => theme(), []); const request = useRef(0); const selected = selectedObject(state) as Selection | undefined;
  const snapshots = useRef(state.model.snapshots); snapshots.current = state.model.snapshots;
  const columns = stdout.columns || 100; const rows = stdout.rows || 30; const belowMinimum = columns < 56 || rows < 18; const narrow = columns < 80; const compact = columns < 100;
  const results = filterActions(state.query, selected); const selectedAction = results[Math.min(state.commandIndex, Math.max(0, results.length - 1))];

  const refresh = useCallback(async () => {
    const id = ++request.current;
    const tmuxTarget = selected?.kind === "issue" && selected.attempts[0]?.roles.at(-1) ? `po-${selected.id}-${selected.attempts[0].roles.at(-1)!.role}` : undefined;
    const previous = snapshots.current;
    const [beads, prefect, tmux, artifacts] = await Promise.all([
      fetchBeads(rigPath, previous.beads as SourceSnapshot<RawBead[]>),
      fetchPrefect(prefectUrl, undefined, previous.prefect as SourceSnapshot<Attempt[]>),
      fetchTmux(tmuxTarget, previous.tmux as SourceSnapshot<{target?: string; output: string; available: boolean}>),
      fetchArtifacts(rigPath, previous.artifacts as SourceSnapshot<Artifact[]>),
    ]);
    if (id !== request.current) return;
    const joined = reconcile(beads.data, prefect.data, artifacts.data);
    dispatch({type: "model", model: {...joined, snapshots: {beads: beads as SourceSnapshot<unknown>, prefect: prefect as SourceSnapshot<unknown>, tmux: tmux as SourceSnapshot<unknown>, artifacts: artifacts as SourceSnapshot<unknown>}}});
  }, [prefectUrl, rigPath, selected?.id]);

  useEffect(() => { if (initialModel) dispatch({type: "model", model: initialModel}); else void refresh(); }, [initialModel]);
  useEffect(() => { if (initialModel || refreshMs <= 0) return; const timer = setInterval(() => void refresh(), Math.max(1000, refreshMs)); return () => clearInterval(timer); }, [initialModel, refresh, refreshMs]);

  const runSelectedAction = useCallback(async () => {
    if (!selectedAction) return;
    if (selectedAction.id === "diagnostics") { dispatch({type: "overlay", overlay: "diagnostics"}); return; }
    if (selectedAction.id === "refresh") { dispatch({type: "overlay"}); await refresh(); return; }
    if (selectedAction.id === "scope") { const next = scopes[(scopes.indexOf(state.scope) + 1) % scopes.length]!; dispatch({type: "scope", scope: next}); dispatch({type: "overlay"}); return; }
    if ((selectedAction.destructive || selectedAction.mutates) && state.pendingActionId !== selectedAction.id) { dispatch({type: "pending", id: selectedAction.id}); return; }
    const args: Record<string, string> = {formula: process.env.PO_FORMULA ?? "software-dev-agentic", backend: process.env.PO_BACKEND ?? "codex-tmux", account: process.env.PO_ACCOUNT ?? "codex-personal", accountClass: process.env.PO_ACCOUNT_CLASS ?? "personal", model: process.env.PO_MODEL ?? "gpt-5.4", effort: process.env.PO_EFFORT ?? "xhigh", prefectUi: prefectUrl.replace(/\/api$/, "")};
    if (process.env.PO_RIG) args.rig = process.env.PO_RIG;
    const result = await executeAction(selectedAction, selected, rigPath, args);
    dispatch({type: "activity", record: {at: new Date().toISOString(), objectId: selected?.id, operation: selectedAction.preview(selected), result: result.message, verification: result.state}});
    dispatch({type: "pending"}); dispatch({type: "overlay"});
    if (result.attachTarget) onAttach?.(result.attachTarget); if (result.state !== "failed") await refresh();
  }, [onAttach, refresh, rigPath, selected, selectedAction, state.pendingActionId, state.scope]);

  useInput((input, key) => {
    if (state.overlay === "palette") {
      if (key.escape) { dispatch({type: "pending"}); dispatch({type: "overlay"}); return; }
      if (key.upArrow) { dispatch({type: "commandMove", delta: -1}); return; }
      if (key.downArrow) { dispatch({type: "commandMove", delta: 1}); return; }
      if (key.return) { void runSelectedAction(); return; }
      if (key.backspace || key.delete) { dispatch({type: "query", value: state.query.slice(0, -1)}); return; }
      if (input && !key.ctrl && !key.meta) dispatch({type: "query", value: state.query + input});
      return;
    }
    if (state.overlay) { if (key.escape) dispatch({type: "overlay"}); if (input === "r") void refresh(); return; }
    if (input === "q") { exit(); return; }
    if (input === "/" || input === ":") { dispatch({type: "overlay", overlay: "palette"}); return; }
    if (input === "?") { dispatch({type: "overlay", overlay: "help"}); return; }
    if (input === "r") { void refresh(); return; }
    if (key.upArrow || input === "k") dispatch({type: "move", delta: -1});
    if (key.downArrow || input === "j") dispatch({type: "move", delta: 1});
    if ((key.rightArrow || input === "l") && selected?.kind === "epic") dispatch({type: "toggle", id: selected.id});
    if ((key.leftArrow || input === "h") && selected?.kind === "epic" && state.expanded.has(selected.id)) dispatch({type: "toggle", id: selected.id});
    if (key.return && narrow) dispatch({type: "narrowDetail", value: true});
    if (key.escape && narrow) dispatch({type: "narrowDetail", value: false});
    if (key.tab) { const tabs = ["overview", "activity", "artifacts", "description"] as const; dispatch({type: "tab", tab: tabs[(tabs.indexOf(state.detailTab) + 1) % tabs.length]!}); }
  });

  if (belowMinimum) return <Box width={columns} height={rows} alignItems="center" justifyContent="center" flexDirection="column"><Text bold>Terminal too small</Text><Text dimColor>{columns}×{rows} available · 56×18 required</Text></Box>;
  const headerHeight = 2; const footerHeight = 2; const bodyHeight = rows - headerHeight - footerHeight; const treeWidth = compact ? Math.floor(columns * .4) : Math.floor(columns * .35); const detailWidth = columns - treeWidth - 3;
  const health = Object.values(state.model.snapshots).map((snapshot) => `${snapshot.freshness === "fresh" ? "+" : snapshot.freshness === "stale" ? "!" : "x"}${snapshot.source[0]}`).join(" ");
  return <Box width={columns} height={rows} flexDirection="column">
    <Box height={headerHeight}><Text bold color={colors.accent}>PO</Text><Text>  epic operations</Text><Text dimColor>  scope: {state.scope}  sources: {health}</Text></Box>
    {state.overlay === "palette" ? <Palette query={state.query} results={results} index={state.commandIndex} selection={selected} pending={state.pendingActionId} colors={colors} /> : state.overlay === "help" ? <Help colors={colors} /> : state.overlay === "diagnostics" ? <Diagnostics state={state} colors={colors} /> : narrow ? (state.narrowDetail ? <Detail object={selected} state={state} width={columns} height={bodyHeight} colors={colors} /> : <WorkTree state={state} width={columns} height={bodyHeight} colors={colors} ascii={ascii} />) : <Box height={bodyHeight} flexDirection="row"><WorkTree state={state} width={treeWidth} height={bodyHeight} colors={colors} ascii={ascii} /><Box width={1}><Text dimColor>│</Text></Box><Box paddingLeft={1}><Detail object={selected} state={state} width={detailWidth} height={bodyHeight} colors={colors} /></Box></Box>}
    <Box height={footerHeight} flexDirection="column"><Text dimColor>{state.activity[0] ? `${state.activity[0].verification}: ${state.activity[0].result}` : "Ready"}</Text><Text><Text bold>↑↓</Text> move  <Text bold>←→</Text> expand  <Text bold>Enter</Text> {narrow ? "open" : "detail"}  <Text bold>:</Text> commands  <Text bold>?</Text> help  <Text bold>q</Text> quit</Text></Box>
  </Box>;
}
