import React from "react";
import {Box, Text} from "ink";
import type {OperatorAction, Selection} from "../actions/registry.js";
import type {UIState} from "../state/store.js";
import type {Theme} from "../theme/theme.js";

export function Palette({query, results, index, selection, pending, colors}: {query: string; results: OperatorAction[]; index: number; selection?: Selection; pending?: string; colors: Theme}) {
  const current = results[Math.min(index, Math.max(0, results.length - 1))];
  return <Box borderStyle="single" borderColor={colors.accent} paddingX={1} flexDirection="column">
    <Text bold color={colors.accent}>&gt; {query}<Text dimColor>{query ? "" : " Type a command…"}</Text></Text>
    <Text dimColor>{results.length} result{results.length === 1 ? "" : "s"} · scope {selection?.id ?? "global"}</Text>
    {results.slice(0, 7).map((action, row) => <Text key={action.id} inverse={row === index} bold={row === index}>{row === index ? ">" : " "} {action.title}{action.destructive ? " [confirm]" : ""}</Text>)}
    {current ? <><Text> </Text><Text bold>Preview</Text><Text>{current.preview(selection)}</Text>{pending === current.id ? <Text color={colors.warning}>Press Enter again to confirm; Esc cancels.</Text> : <Text dimColor>Enter preview/execute · Esc cancel</Text>}</> : <Text dimColor>No matching commands.</Text>}
  </Box>;
}

export interface ActionFormView {action: OperatorAction; args: Record<string, string>; index: number; input: string; stage: "args" | "preview" | "confirm" | "executing"}
export function ActionForm({form, selection, colors}: {form: ActionFormView; selection?: Selection; colors: Theme}) {
  const spec = form.action.arguments?.[form.index];
  return <Box borderStyle="single" borderColor={colors.accent} paddingX={1} flexDirection="column">
    <Text bold>{form.action.title}</Text><Text dimColor>target: {selection?.id ?? "global"}</Text><Text> </Text>
    {form.stage === "args" && spec ? <><Text>{spec.label} {spec.required ? "(required)" : ""}</Text><Text color={colors.accent}>&gt; {form.input}</Text><Text dimColor>Enter accepts · Esc default-cancels the command</Text></> : null}
    {form.stage === "preview" ? <><Text bold>Concrete preview</Text><Text>{form.action.preview(selection, form.args)}</Text><Text dimColor>Enter executes · Esc cancels</Text></> : null}
    {form.stage === "confirm" ? <><Text color={colors.warning} bold>Destructive confirmation</Text><Text>This will affect {selection?.id}. Type the exact ID to confirm:</Text><Text color={colors.accent}>&gt; {form.input}</Text><Text dimColor>Any other value or Esc cancels.</Text></> : null}
    {form.stage === "executing" ? <Text color={colors.warning}>Executing; duplicate submission disabled…</Text> : null}
  </Box>;
}

export function Help({colors}: {colors: Theme}) { return <Box borderStyle="single" borderColor={colors.border} paddingX={1} flexDirection="column"><Text bold>Keyboard</Text><Text>↑/↓ j/k  move selection       ←/→ h/l  collapse / expand</Text><Text>Enter     detail / confirm       Esc       back / cancel</Text><Text>/ or :    command bar             ?         this help</Text><Text>Tab       detail subview         r         refresh sources</Text><Text>q         quit (when no overlay)</Text><Text> </Text><Text dimColor>Ctrl+C, Ctrl+Z, Ctrl+\, Ctrl+S and Ctrl+Q remain terminal-owned.</Text></Box>; }

export function Diagnostics({state, colors}: {state: UIState; colors: Theme}) { return <Box borderStyle="single" borderColor={colors.border} paddingX={1} flexDirection="column"><Text bold>Source diagnostics</Text>{Object.values(state.model.snapshots).map((snapshot) => <Box key={snapshot.source} flexDirection="column"><Text>{snapshot.freshness === "fresh" ? "+" : snapshot.freshness === "stale" ? "!" : "x"} {snapshot.source} · {snapshot.freshness} · fetched {snapshot.fetchedAt}</Text><Text dimColor>  {snapshot.diagnostic?.operation ?? "not yet attempted"}{snapshot.diagnostic?.target ? ` · ${snapshot.diagnostic.target}` : ""} · last success {snapshot.lastSuccessAt ?? "never"}</Text>{snapshot.retry ? <Text dimColor>  retry {snapshot.retry.attempt} · next {snapshot.retry.nextAt ?? "manual"}</Text> : null}{snapshot.diagnostic?.exitStatus !== undefined ? <Text>  exit {snapshot.diagnostic.exitStatus}</Text> : null}{snapshot.error || snapshot.diagnostic?.stderr ? <Text color={colors.error}>  {snapshot.error ?? snapshot.diagnostic?.stderr}</Text> : null}{snapshot.diagnostic?.logPath ? <Text dimColor>  log {snapshot.diagnostic.logPath}</Text> : null}</Box>)}{state.model.unresolved.slice(0, 5).map((record) => <Text key={`${record.source}-${record.id}`} color={colors.warning}>! {record.source} {record.id}: {record.reason}</Text>)}<Text dimColor>Esc returns · r retries all sources</Text></Box>; }
