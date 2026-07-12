import React, {useCallback, useEffect, useMemo, useReducer, useRef, useState} from "react";
import {Box, Text, useApp, useInput, useStdout} from "ink";
import {ActionCoordinator, filterActions, newAttemptObserved, type OperatorAction, type Selection} from "../actions/registry.js";
import {Detail} from "../components/Detail.js";
import {ActionForm, Diagnostics, Help, Palette, type ActionFormView} from "../components/Overlays.js";
import {WorkTree} from "../components/Tree.js";
import {reconcile, resolveSessionTarget, type Artifact, type Attempt, type OperationsModel, type RawBead, type SourceSnapshot, type TmuxSession} from "../domain/model.js";
import {fetchArtifacts, fetchBead, fetchBeads, fetchPrefect, fetchTmux, fetchTmuxSessions} from "../sources/adapters.js";
import {initialState, reducer, selectedObject, type Scope} from "../state/store.js";
import {SourceController} from "../state/sourceController.js";
import {theme} from "../theme/theme.js";

export interface AppProps {rigPath: string; prefectUrl: string; refreshMs: number; ascii?: boolean; initialModel?: OperationsModel; onAttach?: (target: string) => void; dimensions?: {columns: number; rows: number}}
const scopes: Scope[] = ["all", "active", "blocked", "failed", "completed", "archived"];

export function App({rigPath, prefectUrl, refreshMs, ascii = false, initialModel, onAttach, dimensions}: AppProps) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState); const {exit} = useApp(); const {stdout} = useStdout();
  const [form, setForm] = useState<ActionFormView>();
  const colors = useMemo(() => theme(), []); const selected = selectedObject(state) as Selection | undefined;
  const actionCoordinator = useMemo(() => new ActionCoordinator(undefined, async (action, object, args) => {
    if (!object) return false;
    if (action.id === "state") { const row = await fetchBead(rigPath, object.id); return row?.status === args.state; }
    if (action.id === "comment") { const row = await fetchBead(rigPath, object.id); return row?.comments?.some((comment) => comment.text === args.comment) || undefined; }
    if (["dispatch", "retry", "pause", "resume", "cancel"].includes(action.id)) {
      const snapshot = await fetchPrefect(prefectUrl); const attempts = snapshot.data.filter((attempt) => attempt.issueId === object.id || attempt.epicId === object.id);
      if (snapshot.freshness !== "fresh") return undefined;
      if (action.id === "dispatch" || action.id === "retry") return newAttemptObserved(attempts, args.previousAttemptId) || undefined;
      const wanted = action.id === "cancel" ? ["CANCELLED", "CANCELLING"] : action.id === "pause" ? ["PAUSED"] : ["RUNNING", "SCHEDULED"];
      return attempts.some((attempt) => wanted.includes(attempt.state.toUpperCase())) || undefined;
    }
    return undefined;
  }, 15_000, 500), [prefectUrl, rigPath]);
  const controllers = useRef<Array<SourceController<unknown>>>([]);
  const sourceData = useRef<{beads: RawBead[]; prefect: Attempt[]; artifacts: Artifact[]; tmux: TmuxSession[]}>({beads: [], prefect: [], artifacts: [], tmux: []});
  const sourceSnapshots = useRef(state.model.snapshots);
  const columns = dimensions?.columns ?? (stdout.columns || 100); const rows = dimensions?.rows ?? (stdout.rows || 30); const belowMinimum = columns < 56 || rows < 18; const narrow = columns < 80; const compact = columns < 100;
  const bodyRows = Math.max(1, rows - 4);
  const results = filterActions(state.query, selected); const selectedAction = results[Math.min(state.commandIndex, Math.max(0, results.length - 1))];

  const publish = useCallback((source: "beads" | "prefect" | "tmux" | "artifacts", snapshot: SourceSnapshot<unknown>) => {
    sourceSnapshots.current = {...sourceSnapshots.current, [source]: snapshot};
    if (source === "beads") sourceData.current.beads = snapshot.data as RawBead[];
    if (source === "prefect") sourceData.current.prefect = snapshot.data as Attempt[];
    if (source === "artifacts") sourceData.current.artifacts = snapshot.data as Artifact[];
    if (source === "tmux" && Array.isArray(snapshot.data)) sourceData.current.tmux = snapshot.data as TmuxSession[];
    const joined = reconcile(sourceData.current.beads, sourceData.current.prefect, sourceData.current.artifacts, sourceData.current.tmux);
    dispatch({type: "model", model: {...joined, snapshots: sourceSnapshots.current}});
  }, []);

  const refresh = useCallback(async () => { await Promise.all(controllers.current.map((controller) => controller.refreshNow())); }, []);

  useEffect(() => {
    if (initialModel) { dispatch({type: "model", model: initialModel}); return; }
    if (refreshMs <= 0) return;
    const configs = [
      {source: "beads" as const, controller: new SourceController<RawBead[]>({intervalMs: Math.max(2000, refreshMs * 2), timeoutMs: 8000, load: (_signal, previous) => fetchBeads(rigPath, previous)})},
      {source: "prefect" as const, controller: new SourceController<Attempt[]>({intervalMs: Math.max(1000, refreshMs), timeoutMs: 8000, load: (signal, previous) => fetchPrefect(prefectUrl, signal, previous)})},
      {source: "artifacts" as const, controller: new SourceController<Artifact[]>({intervalMs: Math.max(3000, refreshMs * 2), timeoutMs: 5000, load: (_signal, previous) => fetchArtifacts(rigPath, previous, new Set(sourceData.current.beads.map((item) => item.id)))})},
      {source: "tmux" as const, controller: new SourceController<TmuxSession[]>({intervalMs: Math.max(2000, refreshMs), timeoutMs: 3000, load: (_signal, previous) => fetchTmuxSessions(previous)})},
    ];
    controllers.current = configs.map(({controller}) => controller as SourceController<unknown>);
    const unsub = configs.map(({source, controller}) => controller.subscribe((snapshot) => publish(source, snapshot as SourceSnapshot<unknown>)));
    configs.forEach(({controller}) => controller.start());
    return () => { configs.forEach(({controller}) => controller.stop()); unsub.forEach((fn) => fn()); controllers.current = []; };
  }, [initialModel, prefectUrl, publish, refreshMs, rigPath]);

  useEffect(() => {
    if (initialModel || refreshMs <= 0) return;
    if (selected?.kind === "issue") {
      void fetchPrefect(prefectUrl, undefined, sourceSnapshots.current.prefect as SourceSnapshot<Attempt[]>, selected.id)
        .then((snapshot) => publish("prefect", snapshot));
    }
    const role = selected?.kind === "issue" ? selected.attempts[0]?.roles.at(-1)?.role : undefined;
    const target = selected?.kind === "issue" && role ? resolveSessionTarget(selected.id, role, sourceData.current.tmux, selected.attempts[0]?.formula) : selected?.kind === "issue" ? selected.sessions[0]?.target : undefined;
    const controller = new SourceController<{target?: string; output: string; available: boolean}>({intervalMs: Math.max(1000, Math.floor(refreshMs / 2)), timeoutMs: 3000, load: (_signal, previous) => fetchTmux(target, previous)});
    controllers.current.push(controller as SourceController<unknown>); const unsub = controller.subscribe((snapshot) => dispatch({type: "liveOutput", output: snapshot.data.output, target: snapshot.data.target, error: snapshot.error})); controller.start();
    return () => { controller.stop(); unsub(); controllers.current = controllers.current.filter((item) => item !== controller as SourceController<unknown>); };
  }, [initialModel, prefectUrl, publish, refreshMs, selected?.id]);

  const executeForm = useCallback(async (active: ActionFormView) => {
    const selectedAction = active.action;
    if (selectedAction.id === "diagnostics") { dispatch({type: "overlay", overlay: "diagnostics"}); return; }
    if (selectedAction.id === "refresh") { dispatch({type: "overlay"}); await refresh(); return; }
    if (selectedAction.id === "scope") { const next = active.args.scope as Scope; if (!scopes.includes(next)) return; dispatch({type: "scope", scope: next}); setForm(undefined); dispatch({type: "overlay"}); return; }
    setForm({...active, stage: "executing"});
    const beadsOperation = state.model.snapshots.beads.diagnostic?.operation ?? "";
    const args = {...active.args, prefectApi: prefectUrl, prefectUi: prefectUrl.replace(/\/api$/, ""), beadsBackend: beadsOperation.startsWith("br") ? "br" : beadsOperation.startsWith("bd") ? "dolt" : process.env.PO_BEADS_BACKEND ?? "", retryBackend: selected?.kind === "issue" ? selected.attempts[0]?.runtime.backend ?? "" : "", previousAttemptId: selected?.kind === "issue" ? selected.attempts[0]?.id ?? "" : ""};
    const result = await actionCoordinator.run(selectedAction, selected, rigPath, args);
    dispatch({type: "activity", record: {at: new Date().toISOString(), objectId: selected?.id, operation: selectedAction.preview(selected, args), result: result.message, verification: result.state}});
    setForm(undefined); dispatch({type: "overlay"});
    if (result.attachTarget) onAttach?.(result.attachTarget); if (result.state !== "failed") await refresh();
  }, [actionCoordinator, onAttach, prefectUrl, refresh, rigPath, selected, state.scope]);

  const beginAction = useCallback((action: OperatorAction) => {
    if (action.id === "diagnostics") { dispatch({type: "overlay", overlay: "diagnostics"}); return; }
    if (action.id === "refresh") { dispatch({type: "overlay"}); void refresh(); return; }
    const artifacts = selected?.kind === "issue" ? selected.artifacts : selected?.kind === "epic" ? selected.children.flatMap((issue) => issue.artifacts) : [];
    const defaults: Record<string, string | undefined> = {artifactPath: artifacts[0]?.path, sessionTarget: selected?.kind === "issue" ? selected.sessions.find((item) => item.available)?.target : undefined, scope: state.scope};
    const args = Object.fromEntries((action.arguments ?? []).map((spec) => [spec.key, defaults[spec.key] ?? spec.defaultValue?.() ?? ""]));
    const stage = action.arguments?.length ? "args" : "preview";
    setForm({action, args, index: 0, input: action.arguments?.[0] ? args[action.arguments[0].key] ?? "" : "", stage});
  }, [refresh, selected, state.scope]);

  useInput((input, key) => {
    if (state.overlay === "palette") {
      if (key.escape) { setForm(undefined); dispatch({type: "overlay"}); return; }
      if (form) {
        if (form.stage === "executing") return;
        const choiceSpec = form.stage === "args" ? form.action.arguments?.[form.index] : undefined;
        if (choiceSpec?.choices?.length && (key.upArrow || key.downArrow)) {
          const current = Math.max(0, choiceSpec.choices.indexOf(form.input)); const delta = key.upArrow ? -1 : 1;
          setForm({...form, input: choiceSpec.choices[(current + delta + choiceSpec.choices.length) % choiceSpec.choices.length]!}); return;
        }
        if (key.backspace || key.delete) { setForm({...form, input: form.input.slice(0, -1)}); return; }
        if (key.return) {
          if (form.stage === "args") {
            const spec = form.action.arguments?.[form.index]; if (!spec) return;
            const value = form.input.trim(); if (spec.required && !value) return;
            const args = {...form.args, [spec.key]: value}; const next = form.index + 1;
            if (next < (form.action.arguments?.length ?? 0)) { const nextSpec = form.action.arguments![next]!; setForm({...form, args, index: next, input: nextSpec.defaultValue?.() ?? ""}); }
            else setForm({...form, args, input: "", stage: "preview"});
            return;
          }
          if (form.stage === "preview") { if (form.action.destructive) setForm({...form, input: "", stage: "confirm"}); else void executeForm(form); return; }
          if (form.stage === "confirm") { if (form.input === selected?.id) void executeForm(form); else { setForm(undefined); dispatch({type: "overlay"}); } return; }
        }
        if (input && !key.ctrl && !key.meta && (form.stage === "args" || form.stage === "confirm")) setForm({...form, input: form.input + input});
        return;
      }
      if (key.upArrow) { dispatch({type: "commandMove", delta: -1}); return; }
      if (key.downArrow) { dispatch({type: "commandMove", delta: 1}); return; }
      if (key.return && selectedAction) { beginAction(selectedAction); return; }
      if (key.backspace || key.delete) { dispatch({type: "query", value: state.query.slice(0, -1)}); return; }
      if (input && !key.ctrl && !key.meta) dispatch({type: "query", value: state.query + input});
      return;
    }
    if (state.overlay) { if (key.escape) dispatch({type: "overlay"}); if (input === "r") void refresh(); return; }
    if (input === "q") { exit(); return; }
    if (input === "/" || input === ":") { dispatch({type: "overlay", overlay: "palette"}); return; }
    if (input === "?") { dispatch({type: "overlay", overlay: "help"}); return; }
    if (input === "r") { void refresh(); return; }
    if (key.upArrow || input === "k") dispatch({type: "move", delta: -1, viewport: bodyRows - 2});
    if (key.downArrow || input === "j") dispatch({type: "move", delta: 1, viewport: bodyRows - 2});
    if (key.pageUp) dispatch({type: "detailScroll", delta: -Math.max(1, Math.floor(bodyRows / 2))});
    if (key.pageDown) dispatch({type: "detailScroll", delta: Math.max(1, Math.floor(bodyRows / 2))});
    if (input === "J") dispatch({type: "liveScroll", delta: 5});
    if (input === "K") dispatch({type: "liveScroll", delta: -5});
    if (input === "G") dispatch({type: "follow", value: true});
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
    {state.overlay === "palette" ? (form ? <ActionForm form={form} selection={selected} colors={colors} /> : <Palette query={state.query} results={results} index={state.commandIndex} selection={selected} pending={state.pendingActionId} colors={colors} />) : state.overlay === "help" ? <Help colors={colors} /> : state.overlay === "diagnostics" ? <Diagnostics state={state} colors={colors} /> : narrow ? (state.narrowDetail ? <Detail object={selected} state={state} width={columns} height={bodyHeight} colors={colors} /> : <WorkTree state={state} width={columns} height={bodyHeight} colors={colors} ascii={ascii} />) : <Box height={bodyHeight} flexDirection="row"><WorkTree state={state} width={treeWidth} height={bodyHeight} colors={colors} ascii={ascii} /><Box width={1}><Text dimColor>│</Text></Box><Box paddingLeft={1}><Detail object={selected} state={state} width={detailWidth} height={bodyHeight} colors={colors} /></Box></Box>}
    <Box height={footerHeight} flexDirection="column"><Text dimColor>{state.activity[0] ? `${state.activity[0].verification}: ${state.activity[0].result}` : "Ready"}</Text><Text><Text bold>↑↓</Text> move  <Text bold>←→</Text> expand  <Text bold>Enter</Text> {narrow ? "open" : "detail"}  <Text bold>:</Text> commands  <Text bold>?</Text> help  <Text bold>q</Text> quit</Text></Box>
  </Box>;
}
