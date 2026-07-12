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

export function Help({colors}: {colors: Theme}) { return <Box borderStyle="single" borderColor={colors.border} paddingX={1} flexDirection="column"><Text bold>Keyboard</Text><Text>↑/↓ j/k  move selection       ←/→ h/l  collapse / expand</Text><Text>Enter     detail / confirm       Esc       back / cancel</Text><Text>/ or :    command bar             ?         this help</Text><Text>Tab       detail subview         r         refresh sources</Text><Text>q         quit (when no overlay)</Text><Text> </Text><Text dimColor>Ctrl+C, Ctrl+Z, Ctrl+\, Ctrl+S and Ctrl+Q remain terminal-owned.</Text></Box>; }

export function Diagnostics({state, colors}: {state: UIState; colors: Theme}) { return <Box borderStyle="single" borderColor={colors.border} paddingX={1} flexDirection="column"><Text bold>Source diagnostics</Text>{Object.values(state.model.snapshots).map((snapshot) => <Box key={snapshot.source} flexDirection="column"><Text>{snapshot.freshness === "fresh" ? "+" : snapshot.freshness === "stale" ? "!" : "x"} {snapshot.source} · {snapshot.freshness} · fetched {snapshot.fetchedAt}</Text>{snapshot.error ? <Text color={colors.error}>  {snapshot.error}</Text> : null}</Box>)}{state.model.unattributedAttempts.length ? <Text color={colors.warning}>{state.model.unattributedAttempts.length} Prefect runs lack a resolvable issue ID.</Text> : null}<Text dimColor>Esc returns · r retries all sources</Text></Box>; }
