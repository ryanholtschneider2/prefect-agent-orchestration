import React from "react";
import {Box, Text} from "ink";
import type {Epic, Issue} from "../domain/model.js";
import {epicRollup, lifecycleGroup} from "../domain/model.js";
import {age, truncateCells} from "../domain/text.js";
import type {Scope, UIState} from "../state/store.js";
import {stateGlyph, type Theme} from "../theme/theme.js";

const groups: Scope[] = ["active", "blocked", "failed", "completed", "archived"];
export function WorkTree({state, width, height, colors, ascii}: {state: UIState; width: number; height: number; colors: Theme; ascii: boolean}) {
  const lines: React.ReactNode[] = []; let used = 0;
  for (const group of groups) {
    const epics = state.model.epics.filter((epic) => state.scope === "all" ? lifecycleGroup(epic.state) === group : lifecycleGroup(epic.state) === group || epic.children.some((child) => lifecycleGroup(child.state) === group));
    const standalone = state.model.standalone.filter((issue) => lifecycleGroup(issue.state) === group);
    if (state.scope !== "all" && state.scope !== group || (!epics.length && !standalone.length)) continue;
    lines.push(<Text key={`g-${group}`} bold dimColor color={colors.muted}>{group.toUpperCase()}  {epics.length + standalone.length}</Text>); used++;
    for (const epic of epics) {
      const selected = state.selectedId === epic.id; const roll = epicRollup(epic); const marker = state.expanded.has(epic.id) ? (ascii ? "v" : "▾") : (ascii ? ">" : "▸");
      const suffix = `${roll.complete}/${roll.total} · ${roll.running} run${roll.blocked ? ` · ${roll.blocked} block` : ""}`;
      lines.push(<Text key={epic.id} inverse={selected} bold={selected}>{marker} {stateGlyph(epic.state, ascii)} {truncateCells(epic.title, Math.max(8, width - suffix.length - 8))} <Text dimColor>{suffix} {age(epic.updatedAt)}</Text></Text>); used++;
      if (state.expanded.has(epic.id)) for (const child of epic.children.filter((issue) => state.scope === "all" || lifecycleGroup(issue.state) === state.scope)) {
        const active = state.selectedId === child.id; const attempt = child.attempts[0]; const suffix2 = attempt ? `${attempt.roles.at(-1)?.role ?? attempt.formula ?? "run"} ${age(attempt.startedAt)}` : age(child.updatedAt);
        lines.push(<Text key={child.id} inverse={active} bold={active}>  {ascii ? "-" : "└"} {stateGlyph(child.state, ascii)} {truncateCells(child.title, Math.max(8, width - suffix2.length - 10))} <Text dimColor>{suffix2}{child.attempts.length > 1 ? ` ×${child.attempts.length}` : ""}</Text></Text>); used++;
      }
    }
    if (standalone.length) {
      lines.push(<Text key={`s-${group}`} dimColor>  STANDALONE WORK</Text>); used++;
      for (const issue of standalone) { const active = state.selectedId === issue.id; lines.push(<Text key={issue.id} inverse={active} bold={active}>  {stateGlyph(issue.state, ascii)} {truncateCells(issue.title, width - 10)} <Text dimColor>{age(issue.updatedAt)}</Text></Text>); used++; }
    }
  }
  if (!lines.length) lines.push(<Text key="empty" dimColor>No work in this scope. Press : to change scope.</Text>);
  return <Box flexDirection="column" width={width} height={height} overflow="hidden">{lines.slice(state.scroll, state.scroll + height)}</Box>;
}
